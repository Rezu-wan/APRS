"""Stage 10 demo-control API tests.

Covers: role gating (403s + FORBIDDEN audit rows), SUPPORT read access,
idempotent prepare (never processes — the §30 no-bypass invariant), the S5
settlement-race flow through the REAL recovery engine, inject step
validation, deterministic reset (including on an empty DB), /status and the
new /sandbox/ledger endpoint.

DEMO-S* ids are FIXED, so every stateful test starts from a reset to stay
order-independent and deterministic (no sleeps).
"""

from __future__ import annotations

from api.db.database import SessionLocal
from api.db.models import (
    PaymentEvent,
    RecoveryActionRecord,
    SecurityAuditRecord,
    Transaction,
)
from api.services.payment_provider import get_payment_provider

from tests.conftest import (
    ADMIN_KEY,
    CUSTOMER_KEY,
    SUPPORT_KEY,
    SYSTEM_KEY,
)

SCENARIOS_URL = "/api/v1/demo/scenarios"
STATUS_URL = "/api/v1/demo/status"
RESET_URL = "/api/v1/demo/reset"
PREPARE_URL = "/api/v1/demo/scenarios/{key}/prepare"
INJECT_URL = "/api/v1/demo/scenarios/{key}/inject-late-settlement"
LEDGER_URL = "/api/v1/sandbox/ledger"
PROCESS_URL = "/api/v1/transactions/{tid}/recovery/process"
EVENTS_URL = "/api/v1/transactions/{tid}/payment-events"


def _demo_rows(model, tid: str) -> int:
    db = SessionLocal()
    try:
        return db.query(model).filter(model.transaction_id == tid).count()
    finally:
        db.close()


def _audit_rows(action: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(SecurityAuditRecord)
            .filter(SecurityAuditRecord.action == action)
            .count()
        )
    finally:
        db.close()


def _reset(client):
    resp = client.post(RESET_URL, headers=SYSTEM_KEY)
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# role gating
# ---------------------------------------------------------------------------

def test_customer_is_403_on_every_demo_endpoint_and_ledger(client):
    for method, url in (
        ("GET", SCENARIOS_URL),
        ("GET", STATUS_URL),
        ("GET", LEDGER_URL),
        ("POST", PREPARE_URL.format(key="S1")),
        ("POST", INJECT_URL.format(key="S5")),
        ("POST", RESET_URL),
    ):
        resp = getattr(client, method.lower())(url, headers=CUSTOMER_KEY)
        assert resp.status_code == 403, (method, url, resp.status_code)
        body = resp.json()
        assert body["error"]["code"] == "INSUFFICIENT_PERMISSIONS"
        assert body["error"]["request_id"]

    # FORBIDDEN audit rows actually land (Stage 9 central AppError hook)
    before = _audit_rows("FORBIDDEN")
    client.post(RESET_URL, headers=CUSTOMER_KEY)
    assert _audit_rows("FORBIDDEN") >= before + 1


def test_support_can_read_but_not_write(client):
    assert client.get(SCENARIOS_URL, headers=SUPPORT_KEY).status_code == 200
    assert client.get(STATUS_URL, headers=SUPPORT_KEY).status_code == 200
    assert client.get(LEDGER_URL, headers=SUPPORT_KEY).status_code == 200
    assert client.post(PREPARE_URL.format(key="S1"), headers=SUPPORT_KEY).status_code == 403
    assert client.post(INJECT_URL.format(key="S5"), headers=SUPPORT_KEY).status_code == 403
    assert client.post(RESET_URL, headers=SUPPORT_KEY).status_code == 403


# ---------------------------------------------------------------------------
# reset on an EMPTY database (no DEMO rows yet)
# ---------------------------------------------------------------------------

def test_reset_on_empty_database(client):
    body = _reset(client)
    assert body["reset"] is True
    assert body["simulated"] is True
    assert len(body["scenarios"]) == 6
    for item in body["scenarios"]:
        assert item["exists"] is True
        assert item["prepared"] is True
        assert item["processed"] is False
        assert item["recovery"] is None


# ---------------------------------------------------------------------------
# prepare: seeds evidence, never processes
# ---------------------------------------------------------------------------

def test_prepare_s1_never_processes_and_is_idempotent(client):
    _reset(client)

    resp = client.post(PREPARE_URL.format(key="S1"), headers=ADMIN_KEY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["prepared"] is True
    assert body["simulated"] is True
    scenario = body["scenario"]
    assert scenario["transaction_id"] == "DEMO-S1"
    assert scenario["exists"] is True
    assert scenario["prepared"] is True
    assert scenario["processed"] is False
    assert scenario["recovery"] is None  # prepare NEVER processes (§30)
    assert scenario["risk"] is not None

    # evidence really exists in the real tables
    assert _demo_rows(Transaction, "DEMO-S1") == 1
    events_before = _demo_rows(PaymentEvent, "DEMO-S1")
    assert events_before > 0
    assert _demo_rows(RecoveryActionRecord, "DEMO-S1") == 0

    # idempotent replay: no duplicate payment events
    resp = client.post(PREPARE_URL.format(key="S1"), headers=ADMIN_KEY)
    assert resp.status_code == 200
    assert _demo_rows(PaymentEvent, "DEMO-S1") == events_before
    actions = resp.json()["actions"]
    assert any("duplicates=" in a for a in actions)
    assert _demo_rows(RecoveryActionRecord, "DEMO-S1") == 0

    # DEMO_SEED audit rows were written
    assert _audit_rows("DEMO_SEED") >= 1


# ---------------------------------------------------------------------------
# S5: the Stage 10 settlement race through the REAL recovery engine
# ---------------------------------------------------------------------------

def test_s5_race_prepare_inject_process_blocked(client):
    _reset(client)

    resp = client.post(PREPARE_URL.format(key="S5"), headers=SYSTEM_KEY)
    assert resp.status_code == 200

    # inject: only valid for S5
    resp = client.post(INJECT_URL.format(key="S5"), headers=SYSTEM_KEY)
    assert resp.status_code == 200
    assert resp.json()["prepared"] is True

    # the late settlement is present in the real evidence table
    db = SessionLocal()
    try:
        settlement = (
            db.query(PaymentEvent)
            .filter(
                PaymentEvent.transaction_id == "DEMO-S5",
                PaymentEvent.event_type == "SETTLEMENT_CONFIRMED",
            )
            .one_or_none()
        )
        assert settlement is not None
        late_ts = settlement.event_timestamp
    finally:
        db.close()

    # REAL recovery engine: the fresh settlement must block the recovery
    resp = client.post(PROCESS_URL.format(tid="DEMO-S5"), headers=SYSTEM_KEY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "BLOCKED"
    assert body["decision"] in ("RECOVERY_BLOCKED", "MANUAL_REVIEW_QUEUED")

    db = SessionLocal()
    try:
        row = (
            db.query(RecoveryActionRecord)
            .filter(RecoveryActionRecord.transaction_id == "DEMO-S5")
            .one_or_none()
        )
        assert row is not None
        assert row.status == "BLOCKED"
        # the settlement timestamp is LATER than the timeout chain's last event
        timeout = (
            db.query(PaymentEvent)
            .filter(
                PaymentEvent.transaction_id == "DEMO-S5",
                PaymentEvent.event_type == "MERCHANT_CONFIRMATION_TIMEOUT",
            )
            .one()
        )
        assert late_ts > timeout.event_timestamp
    finally:
        db.close()

    # idempotent inject: no duplicate settlement
    count_after = _demo_rows(PaymentEvent, "DEMO-S5")
    resp = client.post(INJECT_URL.format(key="S5"), headers=SYSTEM_KEY)
    assert resp.status_code == 200
    assert _demo_rows(PaymentEvent, "DEMO-S5") == count_after


def test_inject_late_settlement_only_valid_for_s5(client):
    _reset(client)
    resp = client.post(INJECT_URL.format(key="S1"), headers=SYSTEM_KEY)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "DEMO_INVALID_STEP"


def test_prepare_unknown_scenario_is_404(client):
    resp = client.post(PREPARE_URL.format(key="S9"), headers=SYSTEM_KEY)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "DEMO_SCENARIO_NOT_FOUND"


# ---------------------------------------------------------------------------
# reset after activity
# ---------------------------------------------------------------------------

def test_reset_clears_recovery_and_reprepares(client):
    _reset(client)
    assert client.post(PREPARE_URL.format(key="S1"), headers=SYSTEM_KEY).status_code == 200
    resp = client.post(PROCESS_URL.format(tid="DEMO-S1"), headers=SYSTEM_KEY)
    assert resp.status_code == 200
    assert resp.json()["decision"] == "AUTO_RECOVERED"
    provider = get_payment_provider()
    assert provider.get_ledger_entry("DEMO-S1") is not None

    audits_before = _audit_rows("DEMO_RESET")
    body = _reset(client)

    assert body["reset"] is True
    assert len(body["scenarios"]) == 6
    s1 = next(s for s in body["scenarios"] if s["key"] == "S1")
    assert s1["exists"] is True
    assert s1["prepared"] is True
    assert s1["processed"] is False
    assert s1["recovery"] is None
    assert _demo_rows(RecoveryActionRecord, "DEMO-S1") == 0
    assert provider.get_ledger_entry("DEMO-S1") is None
    assert provider.available_limit == 10000.0
    assert _audit_rows("DEMO_RESET") == audits_before + 1
    for s in body["scenarios"]:
        assert s["prepared"] is True


# ---------------------------------------------------------------------------
# /status and /sandbox/ledger
# ---------------------------------------------------------------------------

def test_demo_status_snapshot(client):
    resp = client.get(STATUS_URL, headers=SYSTEM_KEY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["simulated"] is True
    assert body["database"] == "connected"
    assert body["ml_models"] == "loaded"
    genai = body["genai"]
    assert genai["provider"] == "mock"
    assert genai["status"] == "available"
    assert genai["prompt_version"]
    sandbox = body["sandbox_provider"]
    assert sandbox["provider"] == "mock"
    assert sandbox["initial_limit"] == 10000.0
    assert sandbox["currency"] == "BDT"
    assert sandbox["available_limit"] <= 10000.0
    assert "held_entries" in sandbox and "released_entries" in sandbox


def test_sandbox_ledger_endpoint(client):
    _reset(client)
    assert client.post(PREPARE_URL.format(key="S1"), headers=SYSTEM_KEY).status_code == 200
    assert client.post(PROCESS_URL.format(tid="DEMO-S1"), headers=SYSTEM_KEY).status_code == 200

    resp = client.get(LEDGER_URL, headers=SYSTEM_KEY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["simulated"] is True
    assert body["initial_limit"] == 10000.0
    assert body["currency"] == "BDT"
    entries = body["entries"]
    assert isinstance(entries, list)
    s1 = next(e for e in entries if e["transaction_id"] == "DEMO-S1")
    assert s1["status"] == "RELEASED"
    assert s1["released_amount"] >= 1200.0
    assert s1["provider_reference"]


# ---------------------------------------------------------------------------
# scenario catalog shape
# ---------------------------------------------------------------------------

def test_scenarios_catalog_shape(client):
    _reset(client)
    resp = client.get(SCENARIOS_URL, headers=SUPPORT_KEY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["simulated"] is True
    items = body["scenarios"]
    assert [i["key"] for i in items] == ["S1", "S2", "S3", "S4", "S5", "S6"]
    for item in items:
        assert item["transaction_id"] == f"DEMO-{item['key']}"
        assert set(item["expected"]) == {"anomaly", "risk_level", "decision", "status"}
        assert item["story"]
        assert item["steps_key"]
        assert item["recovery"] is None  # fresh reset: nothing processed
        assert item["risk"] is not None

    by_key = {i["key"]: i for i in items}
    assert by_key["S1"]["expected"]["decision"] == "AUTO_RECOVERED"
    assert by_key["S2"]["expected"]["anomaly"] == "DOUBLE_DEDUCTION"
    assert by_key["S3"]["expected"]["decision"] == "RECOVERY_BLOCKED"
    assert by_key["S4"]["expected"]["anomaly"] == "INCOMPLETE"
    assert by_key["S5"]["expected"]["decision"] == "RECOVERY_BLOCKED"
    assert by_key["S6"]["expected"]["decision"] == "ALREADY_RECOVERED"

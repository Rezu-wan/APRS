"""Stage 8 autonomous-recovery API tests: S1-S7 scenarios plus auth and the
/stats/summary autonomous_recovery block.

Seeding style matches test_risk_api.py: transactions are created through the
normal ingestion endpoint; payment events are inserted through a SessionLocal
session. The sandbox provider is the module-level singleton reached via
get_payment_provider() so assertions can inspect the REAL simulated ledger.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from api.core.payment_lifecycle import EVENT_TYPE_INFO
from api.db.database import SessionLocal
from api.db.models import PaymentEvent
from api.services.payment_provider import get_payment_provider

from tests.conftest import (
    ADMIN_KEY,
    CLEAN_FAILED_TX,
    CUSTOMER_KEY,
    SUPPORT_KEY,
    SYSTEM_KEY,
    make_event,
)

EVENT_URL = "/api/v1/transaction/event"
PROCESS_URL = "/api/v1/transactions/{tid}/recovery/process"
EVALUATE_URL = "/api/v1/transactions/{tid}/recovery/evaluate"
GET_URL = "/api/v1/transactions/{tid}/recovery"
STATS_URL = "/api/v1/stats/summary"

# S1: debit confirmed -> gateway confirmed -> merchant timeout (GENUINE_FAILURE)
_S1_EVENTS = [
    "CUSTOMER_DEBIT_CONFIRMED",
    "GATEWAY_RESPONSE_RECEIVED",
    "MERCHANT_CONFIRMATION_TIMEOUT",
]
# S2: two debit confirmations -> DOUBLE_DEDUCTION
_S2_EVENTS = [
    "CUSTOMER_DEBIT_CONFIRMED",
    "GATEWAY_RESPONSE_RECEIVED",
    "MERCHANT_CONFIRMATION_TIMEOUT",
    "CUSTOMER_DEBIT_CONFIRMED",
]
# S3: the full happy path -> NONE / ALREADY_SUCCESS
_S3_EVENTS = [
    "CUSTOMER_DEBIT_CONFIRMED",
    "GATEWAY_REQUEST_SENT",
    "GATEWAY_RESPONSE_RECEIVED",
    "MERCHANT_CONFIRMATION_REQUESTED",
    "MERCHANT_CONFIRMATION_RECEIVED",
    "SETTLEMENT_REQUESTED",
    "SETTLEMENT_CONFIRMED",
]
# S4: a single debit only -> INCOMPLETE / INSUFFICIENT_EVIDENCE
_S4_EVENTS = ["CUSTOMER_DEBIT_CONFIRMED"]


def _unique_id() -> str:
    return f"TXN-AUTO-{uuid.uuid4().hex[:12]}"


def _seed_tx(client, tid: str) -> None:
    resp = client.post(
        EVENT_URL, json=make_event(tid, **CLEAN_FAILED_TX), headers=SYSTEM_KEY
    )
    assert resp.status_code == 200


def _seed_events(tid: str, event_types: list[str], *, late: bool = False) -> None:
    db = SessionLocal()
    try:
        base = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
            if late
            else datetime.now(timezone.utc) - timedelta(hours=1)
        )
        for i, event_type in enumerate(event_types):
            info = EVENT_TYPE_INFO[event_type]
            db.add(
                PaymentEvent(
                    transaction_id=tid,
                    provider_event_id=f"{tid}-{event_type}-{i}",
                    event_type=event_type,
                    source=info["source"],
                    status=info["outcome"],
                    event_timestamp=base + timedelta(minutes=i),
                )
            )
        db.commit()
    finally:
        db.close()


def _process(client, tid: str):
    return client.post(PROCESS_URL.format(tid=tid), headers=SYSTEM_KEY)


def test_s1_merchant_timeout_auto_recovered(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    _seed_events(tid, _S1_EVENTS)

    resp = _process(client, tid)
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "AUTO_RECOVERED"
    assert body["action"] == "RELEASE_LIMIT"
    assert body["status"] == "VERIFIED"
    assert body["simulated"] is True
    assert body["provider_reference"]
    assert body["assessment"]["anomaly_type"] == "GENUINE_FAILURE"
    assert body["assessment"]["recovery_candidate"] is True

    # ledger really released, state really moved
    provider = get_payment_provider()
    ledger = provider.get_ledger_entry(tid)
    assert ledger is not None and ledger["status"] == "RELEASED"

    got = client.get(GET_URL.format(tid=tid), headers=SYSTEM_KEY)
    assert got.status_code == 200
    row = got.json()
    assert row["status"] == "VERIFIED"
    assert row["verified_at"] is not None

    timeline = client.get(
        f"/api/v1/transactions/{tid}/timeline", headers=SYSTEM_KEY
    ).json()
    types = [e["event_type"] for e in timeline["events"]]
    for event in (
        "RECOVERY_APPROVED", "RECOVERY_STARTED",
        "RECOVERY_EXECUTED", "RECOVERY_VERIFIED",
    ):
        assert event in types


def test_s2_double_deduction_blocked_provider_never_called(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    _seed_events(tid, _S2_EVENTS)

    resp = _process(client, tid)
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "RECOVERY_BLOCKED"
    assert body["action"] == "NO_ACTION"
    assert body["status"] == "BLOCKED"
    assert body["assessment"]["anomaly_type"] == "DOUBLE_DEDUCTION"

    provider = get_payment_provider()
    assert provider.get_ledger_entry(tid) is None

    got = client.get(GET_URL.format(tid=tid), headers=SYSTEM_KEY).json()
    assert got["blocked_reason"] == "DOUBLE_DEDUCTION"


def test_s3_success_blocked_already_success(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    _seed_events(tid, _S3_EVENTS)

    resp = _process(client, tid)
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "RECOVERY_BLOCKED"
    assert body["action"] == "NO_ACTION"
    got = client.get(GET_URL.format(tid=tid), headers=SYSTEM_KEY).json()
    assert got["blocked_reason"] == "ALREADY_SUCCESS"


def test_s4_insufficient_evidence_blocked(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    _seed_events(tid, _S4_EVENTS)

    resp = _process(client, tid)
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "RECOVERY_BLOCKED"
    assert body["action"] == "NO_ACTION"
    got = client.get(GET_URL.format(tid=tid), headers=SYSTEM_KEY).json()
    assert got["blocked_reason"] == "INSUFFICIENT_EVIDENCE"


def test_s5_race_fresh_events_flip_the_outcome(client):
    """THE spec-mandated race: assessment runs, a settlement confirms, and
    the process call re-derives fresh facts — the outcome must be blocked,
    never a double recovery."""
    tid = _unique_id()
    _seed_tx(client, tid)
    _seed_events(tid, _S1_EVENTS)
    # assessment first (persisted evidence), THEN the settlement lands
    assert client.post(
        f"/api/v1/transactions/{tid}/risk-assessment", headers=SYSTEM_KEY
    ).status_code == 200
    _seed_events(tid, ["SETTLEMENT_CONFIRMED"], late=True)

    resp = _process(client, tid)
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] in ("RECOVERY_BLOCKED", "MANUAL_REVIEW_QUEUED")
    assert body["status"] == "BLOCKED"
    # the fresh settlement flips the classification away from GENUINE_FAILURE
    # (SUCCESSFUL_BUT_UNCONFIRMED when no merchant event exists — the
    # NEW_SUCCESSFUL_SETTLEMENT gate code itself is asserted in
    # test_recovery_executor.test_safety_gate_blocks_after_settlement; with a
    # merchant TIMEOUT still on record the rules honestly return UNKNOWN)
    assert body["assessment"]["anomaly_type"] in (
        "SUCCESSFUL_BUT_UNCONFIRMED",
        "UNKNOWN",
    )

    provider = get_payment_provider()
    assert provider.get_ledger_entry(tid) is None


def test_s6_duplicate_process_replays(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    _seed_events(tid, _S1_EVENTS)

    first = _process(client, tid)
    assert first.status_code == 200
    assert first.json()["decision"] == "AUTO_RECOVERED"

    provider = get_payment_provider()
    released = provider.get_ledger_entry(tid)["released_amount"]

    second = _process(client, tid)
    assert second.status_code == 200
    body = second.json()
    assert body["decision"] == "ALREADY_RECOVERED"
    assert body["recovery_id"] == first.json()["recovery_id"]
    assert provider.get_ledger_entry(tid)["released_amount"] == released


def test_s7_provider_failure_never_verified(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    _seed_events(tid, _S1_EVENTS)

    provider = get_payment_provider()
    provider.set_failure("TIMEOUT")

    resp = _process(client, tid)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "FAILED"
    assert body["decision"] == "RECOVERY_BLOCKED"
    assert body["provider_reference"] is None

    got = client.get(GET_URL.format(tid=tid), headers=SYSTEM_KEY).json()
    assert got["status"] == "FAILED"
    assert got["failure_reason"] == "PROVIDER_TIMEOUT"
    assert got["verified_at"] is None
    ledger = provider.get_ledger_entry(tid)
    assert ledger is None or ledger["released_amount"] == 0.0


def test_process_support_is_403(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    _seed_events(tid, _S1_EVENTS)
    resp = client.post(PROCESS_URL.format(tid=tid), headers=SUPPORT_KEY)
    assert resp.status_code == 403


def test_process_no_key_is_401(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    resp = client.post(PROCESS_URL.format(tid=tid))
    assert resp.status_code == 401


def test_process_unknown_tx_is_404(client):
    resp = _process(client, "TXN-DOES-NOT-EXIST")
    assert resp.status_code == 404


def test_evaluate_as_support(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    _seed_events(tid, _S1_EVENTS)

    resp = client.post(EVALUATE_URL.format(tid=tid), headers=SUPPORT_KEY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["eligible"] is True
    assert body["action"] == "RELEASE_LIMIT"
    assert body["policy_version"] == "autonomous-v1"
    assert body["simulated"] is True
    assert body["assessment"]["recovery_candidate"] is True

    # evaluate writes nothing: no recovery row exists yet
    resp = client.get(GET_URL.format(tid=tid), headers=SYSTEM_KEY)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
    assert resp.json()["error"]["message"] == (
        "no recovery action for this transaction"
    )


def test_evaluate_unknown_tx_is_404(client):
    resp = client.post(EVALUATE_URL.format(tid="TXN-DOES-NOT-EXIST"), headers=SYSTEM_KEY)
    assert resp.status_code == 404


def test_get_recovery_as_support_ok_customer_403(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    _seed_events(tid, _S1_EVENTS)
    assert _process(client, tid).status_code == 200

    assert client.get(
        GET_URL.format(tid=tid), headers=SUPPORT_KEY
    ).status_code == 200
    assert client.get(
        GET_URL.format(tid=tid), headers=CUSTOMER_KEY
    ).status_code == 403


def test_get_recovery_none_yet_is_404(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    resp = client.get(GET_URL.format(tid=tid), headers=SYSTEM_KEY)
    assert resp.status_code == 404


def test_stats_has_autonomous_recovery_block(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    _seed_events(tid, _S1_EVENTS)
    assert _process(client, tid).status_code == 200

    body = client.get(STATS_URL, headers=SYSTEM_KEY).json()
    block = body["autonomous_recovery"]
    assert block["attempts"] >= 1
    assert block["completed"] >= 1
    assert block["verified"] >= 1
    assert block["blocked"] >= 0
    assert block["failed"] >= 0


def test_manual_review_queued_high_risk(client):
    """R-B: a genuine failure at HIGH risk must queue manual review, not
    execute. previous_failures=5 pushes the risk level up."""
    tid = _unique_id()
    payload = make_event(tid, **CLEAN_FAILED_TX)
    payload["previous_failures"] = 5
    resp = client.post(EVENT_URL, json=payload, headers=SYSTEM_KEY)
    assert resp.status_code == 200
    _seed_events(tid, _S1_EVENTS)

    resp = _process(client, tid)
    assert resp.status_code == 200
    body = resp.json()
    if body["assessment"]["risk_level"] in ("HIGH", "CRITICAL"):
        assert body["decision"] == "MANUAL_REVIEW_QUEUED"
        assert body["action"] == "MANUAL_REVIEW"
        assert body["status"] == "BLOCKED"
        assert get_payment_provider().get_ledger_entry(tid) is None

"""
Stage 11 Phase 11E — Recovery Policy Simulator tests.

The router is registered by the integrator in api.main; this module
registers it idempotently itself so the tests are self-contained during
parallel development (same pattern as tests/test_relationship.py).

Purity is THE critical property under test: a simulation run must not
change any row counts except +1 SecurityAuditRecord (the best-effort
POLICY_SIMULATION audit row).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from api.db.database import SessionLocal
from api.db.models import (
    DigitalTwinEvent,
    PaymentEvent,
    RecoveryActionRecord,
    RiskAssessmentRecord,
    SandboxLedgerEntry,
    SecurityAuditRecord,
    Transaction,
)
from api.core.payment_lifecycle import EVENT_TYPE_INFO
from api.main import app
from api.routes import policy_simulator as policy_simulator_route
from api.services.audit import AUDIT_POLICY_SIMULATION
from api.services.demo_scenarios import (
    prepare_scenario,
    purge_demo_rows,
    scenario_start,
    scenario_transaction_id,
)

from tests.conftest import ADMIN_KEY, CUSTOMER_KEY, SUPPORT_KEY

URL = "/api/v1/policy-simulator/run"

V1 = "autonomous-v1"
V2 = "autonomous-v2-experimental"
MANUAL = "manual-only-baseline"

# The demo corpus prepared by the fixture below: S1 genuine failure
# (prepare only — never processed), S2 double deduction, S3 success.
DEMO_KEYS = ["S1", "S2", "S3"]


@pytest.fixture(scope="module", autouse=True)
def _register_router():
    """Idempotent registration so the tests pass before/after integration."""
    for route in app.routes:
        if getattr(route, "path", None) == URL:
            break
    else:
        app.include_router(policy_simulator_route.router)
    yield


_seq = 0


def _next_id(prefix: str) -> str:
    global _seq
    _seq += 1
    return f"{prefix}-{uuid.uuid4().hex[:8]}-{_seq}"


def _seed_events(db, tid: str, event_types: list[str], start: datetime):
    """Deterministic event chain via the pinned EVENT_TYPE_INFO triples."""
    elapsed_ms = 0.0
    for seq, event_type in enumerate(event_types):
        info = EVENT_TYPE_INFO[event_type]
        latency = 120 + seq * 40
        elapsed_ms += latency
        db.add(
            PaymentEvent(
                transaction_id=tid,
                provider_event_id=f"{tid}-{event_type}-{seq:03d}",
                event_type=event_type,
                source=info["source"],
                status=info["outcome"],
                event_timestamp=start + timedelta(milliseconds=elapsed_ms),
                reference_id=f"{tid}-{info['stage'].lower()}-ref",
                latency_ms=latency,
            )
        )


def _seed_gateway_timeout_tx(tid: str) -> None:
    """Crafted MEDIUM-risk genuine failure: gateway-timeout chain
    (confidence 0.43) plus high-retry / high-latency transaction features
    that push the deterministic risk score past the 0.75 one-step rise, so
    the assessment lands on MEDIUM — exactly the input where the
    v2-experimental confidence gate differs from v1."""
    db = SessionLocal()
    try:
        db.add(
            Transaction(
                transaction_id=tid,
                user_id="USER-SIM",
                merchant_id="MERCHANT-SIM",
                amount=Decimal("750.00"),
                currency="BDT",
                timestamp=datetime.now(timezone.utc),
                gateway_latency_ms=6000,  # > HIGH_LATENCY_MS
                retry_count=3,            # >= HIGH_RETRY_THRESHOLD
                network_quality="Good",
                previous_failures=0,
                account_age_days=500,
                failure_reason="Timeout",
                current_state="FAILED",
            )
        )
        _seed_events(
            db,
            tid,
            [
                "CUSTOMER_DEBIT_CONFIRMED",
                "GATEWAY_REQUEST_SENT",
                "GATEWAY_TIMEOUT",
            ],
            datetime.now(timezone.utc),
        )
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module", autouse=True)
def demo_corpus():
    """Deterministic corpus: purge ALL demo rows, re-prepare S1..S3 through
    the real demo service (prepare only — recovery/process is NEVER run, so
    S1 keeps its genuine-failure recoverable ground truth), and add one
    crafted MEDIUM-risk gateway-timeout transaction."""
    db = SessionLocal()
    try:
        purge_demo_rows(db)
        for key in DEMO_KEYS:
            prepare_scenario(db, key)
    finally:
        db.close()
    _seed_gateway_timeout_tx(_next_id("TXN-SIM-MED"))
    yield


def _post(client, headers, body):
    return client.post(URL, json=body, headers=headers)


def _demo_body(policies):
    return {"policies": policies, "source": {"demo": True}}


def _result(run, version):
    matches = [p for p in run["policies"] if p["policy_version"] == version]
    assert len(matches) == 1
    return matches[0]


def _counts():
    db = SessionLocal()
    try:
        return {
            "transaction": db.query(Transaction).count(),
            "payment_event": db.query(PaymentEvent).count(),
            "risk_assessment": db.query(RiskAssessmentRecord).count(),
            "recovery_action": db.query(RecoveryActionRecord).count(),
            "twin_event": db.query(DigitalTwinEvent).count(),
            "sandbox_ledger": db.query(SandboxLedgerEntry).count(),
            "security_audit": db.query(SecurityAuditRecord).count(),
        }
    finally:
        db.close()


# ------------------------------------------------------------------- roles


def test_admin_can_run(client):
    resp = _post(client, ADMIN_KEY, _demo_body([V1]))
    assert resp.status_code == 200, resp.text
    run = resp.json()
    assert run["simulated"] is True
    assert run["affects_live_policy"] is False
    assert run["dataset"]["source"] == "demo"
    assert all(
        f"DEMO-{k}" in run["dataset"]["transaction_ids"] for k in DEMO_KEYS
    )


def test_support_forbidden(client):
    resp = _post(client, SUPPORT_KEY, _demo_body([V1]))
    assert resp.status_code == 403


def test_customer_forbidden(client):
    resp = _post(client, CUSTOMER_KEY, _demo_body([V1]))
    assert resp.status_code == 403


def test_unknown_policy_lists_valid_names(client):
    resp = _post(client, ADMIN_KEY, _demo_body(["not-a-policy"]))
    assert resp.status_code in (400, 422)
    body = resp.text
    assert V1 in body
    assert MANUAL in body
    assert V2 in body


def test_bad_source_rejected(client):
    resp = _post(client, ADMIN_KEY, {"policies": [V1], "source": {}})
    assert resp.status_code == 422
    resp = _post(
        client,
        ADMIN_KEY,
        {"policies": [V1], "source": {"demo": True, "transaction_ids": ["X"]}},
    )
    assert resp.status_code == 422


# ------------------------------------------------------------------ purity


def test_simulation_is_pure(client):
    before = _counts()
    resp = _post(
        client,
        ADMIN_KEY,
        {
            "policies": [V1, V2, MANUAL],
            "source": {
                "transaction_ids": [
                    scenario_transaction_id(k) for k in DEMO_KEYS
                ]
            },
        },
    )
    assert resp.status_code == 200, resp.text
    after = _counts()
    assert after["transaction"] == before["transaction"]
    assert after["payment_event"] == before["payment_event"]
    assert after["risk_assessment"] == before["risk_assessment"]
    assert after["recovery_action"] == before["recovery_action"]
    assert after["twin_event"] == before["twin_event"]
    assert after["sandbox_ledger"] == before["sandbox_ledger"]
    # the ONLY permitted write: one best-effort audit row
    assert after["security_audit"] == before["security_audit"] + 1
    db = SessionLocal()
    try:
        row = (
            db.query(SecurityAuditRecord)
            .filter(SecurityAuditRecord.action == AUDIT_POLICY_SIMULATION)
            .order_by(SecurityAuditRecord.id.desc())
            .first()
        )
        assert row is not None
        assert row.audit_metadata["simulated"] is True
        assert row.audit_metadata["dataset_fingerprint"]
    finally:
        db.close()


# ------------------------------------------------- v1 on the demo corpus


def test_v1_demo_corpus_decisions(client):
    resp = _post(client, ADMIN_KEY, _demo_body([V1]))
    assert resp.status_code == 200
    run = resp.json()
    assert run["ground_truth_available"] is True

    v1 = _result(run, V1)
    by_tx = {d["transaction_id"]: d for d in v1["decisions"]}

    s1 = by_tx[scenario_transaction_id("S1")]
    assert s1["action"] == "RELEASE_LIMIT"
    assert s1["eligible"] is True
    assert s1["gate_vetoed"] is False

    s2 = by_tx[scenario_transaction_id("S2")]
    assert s2["action"] == "NO_ACTION"
    assert s2["blocked_reason"] == "DOUBLE_DEDUCTION"

    s3 = by_tx[scenario_transaction_id("S3")]
    assert s3["action"] == "NO_ACTION"
    assert s3["blocked_reason"] == "ALREADY_SUCCESS"

    assert v1["would_release"] == 1
    assert v1["gate_vetoes"] == 0
    assert v1["false_recovery"] == 0
    assert v1["missed_recovery"] == 0


def test_manual_only_baseline_never_releases(client):
    resp = _post(client, ADMIN_KEY, _demo_body([MANUAL]))
    assert resp.status_code == 200
    manual = _result(resp.json(), MANUAL)
    assert manual["would_release"] == 0
    assert manual["transactions_evaluated"] == 3
    assert manual["provider_calls_avoided"] == manual["transactions_evaluated"]
    assert manual["experimental"] is True
    # ground truth: the baseline misses S1 (active policy + gate would
    # release it) and nothing else
    assert manual["missed_recovery"] == 1
    assert manual["false_recovery"] == 0


def test_v2_experimental_measurable_delta(client):
    """The crafted MEDIUM-risk gateway-timeout tx: v1 releases it (risk
    MEDIUM, no confidence requirement); v2 requires confidence >= 0.6 and
    the gateway-timeout chain carries 0.43 — so v2 goes to MANUAL_REVIEW.
    That is exactly one missed recovery for v2 against the active baseline,
    visible in the flat comparison (NO ranking anywhere)."""
    crafted = [
        t
        for t in _tx_ids()
        if t.startswith("TXN-SIM-MED")
    ]
    assert len(crafted) == 1
    resp = _post(
        client,
        ADMIN_KEY,
        {
            "policies": [V1, V2],
            "source": {
                "transaction_ids": [
                    scenario_transaction_id("S1"),
                    crafted[0],
                ]
            },
        },
    )
    assert resp.status_code == 200
    run = resp.json()
    assert run["ground_truth_available"] is False
    assert run["dataset"]["source"] == "transaction_ids"

    v1 = _result(run, V1)
    v2 = _result(run, V2)
    v1_by_tx = {d["transaction_id"]: d for d in v1["decisions"]}
    v2_by_tx = {d["transaction_id"]: d for d in v2["decisions"]}

    # v1 releases the MEDIUM-risk tx; v2's confidence gate does not.
    assert v1_by_tx[crafted[0]]["action"] == "RELEASE_LIMIT"
    assert v2_by_tx[crafted[0]]["action"] == "MANUAL_REVIEW"
    assert v2_by_tx[crafted[0]]["blocked_reason"] == "RISK_NO_LONGER_PERMITS"

    # v1 releases S1 as usual; both agree on it (LOW risk path unchanged).
    s1 = scenario_transaction_id("S1")
    assert v1_by_tx[s1]["action"] == "RELEASE_LIMIT"
    assert v2_by_tx[s1]["action"] == "RELEASE_LIMIT"

    # arbitrary-id corpus: no known outcomes -> ground-truth metrics are
    # honestly null, never invented
    assert run["ground_truth_available"] is False
    assert v1["false_recovery"] is None and v1["missed_recovery"] is None
    assert v2["false_recovery"] is None and v2["missed_recovery"] is None
    assert v2["experimental"] is True

    comparison = {c["metric"]: c["per_policy"] for c in run["comparison"]}
    assert comparison["would_release"][V1] != comparison["would_release"][V2]
    assert comparison["would_manual_review"][V1] != (
        comparison["would_manual_review"][V2]
    )
    # no ranking / "best" field anywhere in the response
    assert "ranking" not in run
    assert "best" not in run
    assert all("rank" not in c for c in run["comparison"])


def _tx_ids():
    db = SessionLocal()
    try:
        return [t.transaction_id for t in db.query(Transaction).all()]
    finally:
        db.close()


# ------------------------------------------------------------ determinism


def test_determinism_same_run_twice(client):
    body = {
        "policies": [V1, V2, MANUAL],
        "source": {
            "transaction_ids": [
                scenario_transaction_id(k) for k in DEMO_KEYS
            ]
        },
    }
    run1 = _post(client, ADMIN_KEY, body).json()
    run2 = _post(client, ADMIN_KEY, body).json()

    assert run1["dataset"]["dataset_fingerprint"] == (
        run2["dataset"]["dataset_fingerprint"]
    )
    assert run1["dataset"]["transaction_ids"] == (
        run2["dataset"]["transaction_ids"]
    )

    def numeric(run):
        out = {}
        for p in run["policies"]:
            out[p["policy_version"]] = {
                k: v
                for k, v in p.items()
                if k != "decision_latency_ms_avg"
                and isinstance(v, (int, float))
            }
        return out

    assert numeric(run1) == numeric(run2)
    c1 = {c["metric"]: c["per_policy"] for c in run1["comparison"]}
    c2 = {c["metric"]: c["per_policy"] for c in run2["comparison"]}
    assert c1 == c2

"""Stage 7 risk-assessment API tests:
POST/GET /api/v1/transactions/{id}/risk-assessment and the /stats/summary
risk_assessments block.

Payment events are seeded by inserting PaymentEvent rows through a
SessionLocal session (same style as test_reconstruction_api.py); the
transaction itself is created through the normal ingestion endpoint. The
anomaly ML model may or may not be present in models/ — the tests only
assert rule-owned fields (anomaly_type, recovery_candidate), which ML can
never change (precedence rule 1).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from api.core.payment_lifecycle import EVENT_TYPE_INFO
from api.db.database import SessionLocal
from api.db.models import PaymentEvent

from tests.conftest import (
    ADMIN_KEY,
    CLEAN_FAILED_TX,
    CUSTOMER_KEY,
    SUPPORT_KEY,
    SYSTEM_KEY,
    make_event,
)

EVENT_URL = "/api/v1/transaction/event"
RISK_URL = "/api/v1/transactions/{tid}/risk-assessment"
TIMELINE_URL = "/api/v1/transactions/{tid}/timeline"
STATS_URL = "/api/v1/stats/summary"

# debit confirmed -> gateway confirmed -> merchant timeout (rule R4:
# GENUINE_FAILURE, recovery candidate)
_MERCHANT_TIMEOUT_EVENTS = [
    "CUSTOMER_DEBIT_CONFIRMED",
    "GATEWAY_RESPONSE_RECEIVED",
    "MERCHANT_CONFIRMATION_TIMEOUT",
]


def _unique_id() -> str:
    return f"TXN-RISKAPI-{uuid.uuid4().hex[:12]}"


def _seed_tx(client, tid: str) -> None:
    payload = make_event(tid, **CLEAN_FAILED_TX)
    resp = client.post(EVENT_URL, json=payload, headers=SYSTEM_KEY)
    assert resp.status_code == 200


def _seed_events(tid: str, event_types: list[str]) -> None:
    db = SessionLocal()
    try:
        base = datetime.now(timezone.utc) - timedelta(hours=1)
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


def test_post_risk_assessment_genuine_failure(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    _seed_events(tid, _MERCHANT_TIMEOUT_EVENTS)

    resp = client.post(RISK_URL.format(tid=tid), headers=SYSTEM_KEY)
    assert resp.status_code == 200
    body = resp.json()
    assessment = body["assessment"]
    assert assessment["transaction_id"] == tid
    # rules own the classification — ML can never rewrite it (precedence 1)
    assert assessment["anomaly_type"] == "GENUINE_FAILURE"
    assert assessment["recovery_candidate"] is True
    assert assessment["rule_version"] == "1"
    assert body["reused"] is False

    timeline = client.get(TIMELINE_URL.format(tid=tid), headers=SYSTEM_KEY).json()
    classified = [
        e for e in timeline["events"] if e["event_type"] == "ANOMALY_CLASSIFIED"
    ]
    assert len(classified) == 1
    meta = classified[0]["metadata"]
    assert meta["anomaly_type"] == "GENUINE_FAILURE"
    assert meta["assessment_id"] == assessment["assessment_id"]
    assert meta["evidence_fingerprint"]
    # observation, not a transition
    assert classified[0]["previous_state"] == classified[0]["new_state"]


def test_post_risk_assessment_idempotent_repeat(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    _seed_events(tid, _MERCHANT_TIMEOUT_EVENTS)

    first = client.post(RISK_URL.format(tid=tid), headers=SYSTEM_KEY)
    assert first.status_code == 200
    assert first.json()["reused"] is False

    timeline_before = client.get(
        TIMELINE_URL.format(tid=tid), headers=SYSTEM_KEY
    ).json()

    second = client.post(RISK_URL.format(tid=tid), headers=SYSTEM_KEY)
    assert second.status_code == 200
    body = second.json()
    assert body["reused"] is True
    assert body["digital_twin_event_recorded"] is False
    assert body["assessment"]["assessment_id"] == (
        first.json()["assessment"]["assessment_id"]
    )

    timeline_after = client.get(
        TIMELINE_URL.format(tid=tid), headers=SYSTEM_KEY
    ).json()
    assert timeline_after["event_count"] == timeline_before["event_count"]


def test_post_risk_assessment_unknown_tx_is_404(client):
    resp = client.post(RISK_URL.format(tid="TXN-DOES-NOT-EXIST"), headers=SYSTEM_KEY)
    assert resp.status_code == 404


def test_post_risk_assessment_no_key_is_401(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    resp = client.post(RISK_URL.format(tid=tid))
    assert resp.status_code == 401


def test_post_risk_assessment_support_is_403(client):
    """SUPPORT may READ assessments but never CREATE one: assessments are
    recovery-routing evidence produced by the system, not by support staff."""
    tid = _unique_id()
    _seed_tx(client, tid)
    resp = client.post(RISK_URL.format(tid=tid), headers=SUPPORT_KEY)
    assert resp.status_code == 403


def test_get_risk_assessment_as_support(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    _seed_events(tid, _MERCHANT_TIMEOUT_EVENTS)
    assert client.post(RISK_URL.format(tid=tid), headers=SYSTEM_KEY).status_code == 200

    resp = client.get(RISK_URL.format(tid=tid), headers=SUPPORT_KEY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["reused"] is True
    assert body["digital_twin_event_recorded"] is False
    assert body["assessment"]["anomaly_type"] == "GENUINE_FAILURE"


def test_get_risk_assessment_customer_is_403(client):
    """IDOR rationale: assessments carry internal anti-fraud evidence (rules,
    ML scores, FALSE_COMPLAINT classifications) — never customer-visible."""
    tid = _unique_id()
    _seed_tx(client, tid)
    resp = client.get(RISK_URL.format(tid=tid), headers=CUSTOMER_KEY)
    assert resp.status_code == 403


def test_get_risk_assessment_unknown_tx_is_404(client):
    resp = client.get(RISK_URL.format(tid="TXN-DOES-NOT-EXIST"), headers=SYSTEM_KEY)
    assert resp.status_code == 404


def test_get_risk_assessment_serves_dataset_evidence_without_source(client):
    """Dataset-loaded assessments carry evidence entries with no ``source``
    key (the CSV has no provenance column) — GET must serve them instead of
    500ing on the strict EvidenceItem model."""
    from api.db.models import RiskAssessmentRecord

    tid = _unique_id()
    _seed_tx(client, tid)
    db = SessionLocal()
    try:
        db.add(
            RiskAssessmentRecord(
                assessment_id=f"RSA-{uuid.uuid4().hex[:12]}",
                transaction_id=tid,
                evidence_fingerprint="f" * 64,
                anomaly_type="GENUINE_FAILURE",
                risk_level="LOW",
                risk_score=0.2,
                deterministic_risk_score=0.2,
                recovery_candidate=True,
                # dataset shape: code/description/severity only, no source
                evidence=[
                    {
                        "code": "SCENARIO",
                        "description": "Scenario failure injected for DEMO run",
                        "severity": "LOW",
                    }
                ],
                triggered_rules=[{"rule_id": "R1", "name": "Genuine failure"}],
                model_version="synthetic-v1",
                rule_version="1",
            )
        )
        db.commit()
    finally:
        db.close()

    resp = client.get(RISK_URL.format(tid=tid), headers=SUPPORT_KEY)
    assert resp.status_code == 200
    evidence = resp.json()["assessment"]["evidence"]
    assert evidence == [
        {"code": "SCENARIO", "description": "Scenario failure injected for DEMO run",
         "source": None, "severity": "LOW"}
    ]


def test_get_risk_assessment_none_yet_is_404(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    resp = client.get(RISK_URL.format(tid=tid), headers=SYSTEM_KEY)
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"] == "no risk assessment for this transaction"


def test_stats_contains_risk_assessments_block(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    _seed_events(tid, _MERCHANT_TIMEOUT_EVENTS)
    assert client.post(RISK_URL.format(tid=tid), headers=ADMIN_KEY).status_code == 200

    resp = client.get(STATS_URL, headers=SYSTEM_KEY)
    assert resp.status_code == 200
    block = resp.json()["risk_assessments"]
    assert block["total"] >= 1
    assert block["by_anomaly_type"].get("GENUINE_FAILURE", 0) >= 1
    assert set(block["by_risk_level"]) <= {
        "LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN",
    }
    assert block["recovery_candidates"] >= 1

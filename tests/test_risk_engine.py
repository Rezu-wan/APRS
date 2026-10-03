"""Stage 7 hybrid risk ENGINE tests: api/services/risk_engine.py.

Transactions and payment events are seeded through a SessionLocal session
(same style as test_reconstruction_api.py); ML is a fake wrapper returning a
pinned anomaly score so the precedence rules are tested deterministically
without model artifacts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from api.core.payment_lifecycle import EVENT_TYPE_INFO
from api.db.database import SessionLocal
from api.db.models import PaymentEvent, RiskAssessmentRecord, Transaction
from api.schemas.risk_assessment import (
    ANOMALY_DOUBLE_DEDUCTION,
    ANOMALY_INCOMPLETE,
    ANOMALY_SUSPICIOUS,
    ANOMALY_UNKNOWN,
    EVIDENCE_ML_HIGH_ANOMALY,
    RISK_HIGH,
    RISK_UNKNOWN,
)
from api.services.risk_engine import persist_assessment, run_assessment

# the session-scoped client fixture creates the test schema; engine tests
# seed rows directly through SessionLocal but depend on it for the schema
from tests.conftest import SYSTEM_KEY, client  # noqa: F401

_T0 = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)


def _unique_id() -> str:
    return f"TXN-RISK-{uuid.uuid4().hex[:12]}"


class _FakeML:
    """Duck-typed stand-in for MLService with a pinned anomaly score."""

    def __init__(self, score: float | None, predicted: str = "gateway_timeout"):
        self._score = score
        self._predicted = predicted

    def predict_anomaly_features(self, features: dict) -> dict | None:
        if self._score is None:
            return None
        return {
            "predicted_scenario": self._predicted,
            "ml_anomaly_score": self._score,
            "class_probabilities": {"normal_success": round(1 - self._score, 4)},
        }


def _seed_tx(tid: str, **overrides) -> None:
    db = SessionLocal()
    try:
        db.add(
            Transaction(
                transaction_id=tid,
                user_id="USER-001",
                merchant_id="MERCHANT-001",
                amount=Decimal("30.00"),
                **overrides,
            )
        )
        db.commit()
    finally:
        db.close()


def _seed_events(tid: str, event_types: list[str], reference_id: str | None = None):
    db = SessionLocal()
    try:
        rows = []
        for i, event_type in enumerate(event_types):
            info = EVENT_TYPE_INFO[event_type]
            row = PaymentEvent(
                transaction_id=tid,
                provider_event_id=f"{tid}-{event_type}-{i}",
                event_type=event_type,
                source=info["source"],
                status=info["outcome"],
                event_timestamp=_T0 + timedelta(minutes=i),
                reference_id=reference_id,
            )
            db.add(row)
            rows.append(row)
        db.commit()
        return rows
    finally:
        db.close()


def _run(tid: str, ml, *, customer_reported_failure: bool = False):
    """Run the engine on a fresh session and persist like the route does."""
    db = SessionLocal()
    try:
        tx = (
            db.query(Transaction)
            .filter(Transaction.transaction_id == tid)
            .one()
        )
        assessment, fingerprint, reused = run_assessment(
            db, tx, ml, customer_reported_failure=customer_reported_failure
        )
        if not reused:
            persist_assessment(db, tx, assessment, fingerprint)
            db.commit()
        return assessment, fingerprint, reused
    finally:
        db.close()


def _count_records(tid: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(RiskAssessmentRecord)
            .filter(RiskAssessmentRecord.transaction_id == tid)
            .count()
        )
    finally:
        db.close()


def test_deterministic_evidence_overrides_low_ml_risk(client):
    """Precedence rule 1: DOUBLE_DEDUCTION stays DOUBLE_DEDUCTION even when
    the model scores the payment as almost normal (0.05)."""
    tid = _unique_id()
    _seed_tx(tid)
    _seed_events(tid, ["CUSTOMER_DEBIT_CONFIRMED", "CUSTOMER_DEBIT_CONFIRMED"])

    assessment, fingerprint, reused = _run(tid, _FakeML(0.05))

    assert reused is False
    assert assessment.anomaly_type == ANOMALY_DOUBLE_DEDUCTION
    assert assessment.recovery_candidate is False
    assert assessment.ml_anomaly_score == 0.05
    # blend: max(deterministic, 0.5*0.05 + 0.5*deterministic) == deterministic
    assert assessment.risk_score == assessment.deterministic_risk_score == 1.0
    # rules say HIGH; the blend (1.0) crosses 0.75 so the level rises the
    # permitted ONE step (precedence rule 3) — never more
    assert assessment.risk_level == "CRITICAL"
    # no ML_HIGH_ANOMALY evidence: rules were decisive, ML never upgraded
    assert all(e.code != EVIDENCE_ML_HIGH_ANOMALY for e in assessment.evidence)


def test_ml_supports_incomplete_case(client):
    """Precedence rule 2: INCOMPLETE evidence + ml 0.85 predicting a
    behaviorally suspicious scenario -> SUSPICIOUS (HIGH, not a candidate,
    ML_HIGH_ANOMALY evidence attached)."""
    tid = _unique_id()
    _seed_tx(tid)
    _seed_events(tid, ["CUSTOMER_DEBIT_CONFIRMED"])

    assessment, fingerprint, reused = _run(
        tid, _FakeML(0.85, predicted="suspicious_high_retry")
    )

    assert reused is False
    assert assessment.anomaly_type == ANOMALY_SUSPICIOUS
    assert assessment.risk_level == RISK_HIGH
    assert assessment.recovery_candidate is False


def test_ml_agreeing_with_incomplete_does_not_escalate(client):
    """P(not normal) alone must NOT trigger the SUSPICIOUS upgrade: when the
    model predicts incomplete_event_chain it is CORROBORATING the rules'
    INCOMPLETE verdict (sparse evidence), not detecting behavior — the
    assessment stays INCOMPLETE/UNKNOWN. (Found in live E2E: a single-debit
    transaction scored ml 1.0 purely from sparsity.)"""
    tid = _unique_id()
    _seed_tx(tid)
    _seed_events(tid, ["CUSTOMER_DEBIT_CONFIRMED"])

    assessment, _, _ = _run(tid, _FakeML(1.0, predicted="incomplete_event_chain"))

    assert assessment.anomaly_type == ANOMALY_INCOMPLETE
    assert assessment.risk_level == RISK_UNKNOWN
    assert assessment.recovery_candidate is False
    # the score is recorded as a signal, but no ML_HIGH_ANOMALY evidence is
    # attached — corroboration of INCOMPLETE is not an escalation
    assert all(e.code != EVIDENCE_ML_HIGH_ANOMALY for e in assessment.evidence)
    assert assessment.ml_anomaly_score == 1.0
    assert assessment.model_version == "synthetic-v1"


def test_ml_below_threshold_keeps_incomplete(client):
    """ML below 0.7 supports but never upgrades: classification stays
    INCOMPLETE and the risk level never falls or rises."""
    tid = _unique_id()
    _seed_tx(tid)
    _seed_events(tid, ["CUSTOMER_DEBIT_CONFIRMED"])

    assessment, fingerprint, reused = _run(tid, _FakeML(0.5))

    assert reused is False
    assert assessment.anomaly_type == ANOMALY_INCOMPLETE
    assert assessment.ml_anomaly_score == 0.5
    assert all(e.code != EVIDENCE_ML_HIGH_ANOMALY for e in assessment.evidence)
    # blend: max(0.7, 0.5*0.5 + 0.5*0.7) = 0.7
    assert assessment.risk_score == 0.7
    assert assessment.model_version == "synthetic-v1"


def test_unknown_when_evidence_insufficient(client):
    """Contradictory non-debit-failure evidence (debit FAILED + gateway
    TIMEOUT) leaves the fallback rule R0: UNKNOWN, never a guessed verdict."""
    tid = _unique_id()
    _seed_tx(tid)
    _seed_events(tid, ["CUSTOMER_DEBIT_FAILED", "GATEWAY_TIMEOUT"])

    assessment, fingerprint, reused = _run(tid, _FakeML(None))

    assert reused is False
    assert assessment.anomaly_type == ANOMALY_UNKNOWN
    assert assessment.risk_level == "UNKNOWN"
    assert assessment.recovery_candidate is False
    assert assessment.ml_anomaly_score is None
    assert assessment.model_version == "rules-only"
    assert assessment.risk_score == assessment.deterministic_risk_score


def test_idempotent_reuse(client):
    """Same evidence twice: the second run reuses the stored assessment —
    reused=True, no new row, and the SAME assessment_id."""
    tid = _unique_id()
    _seed_tx(tid)
    _seed_events(tid, ["CUSTOMER_DEBIT_CONFIRMED", "GATEWAY_REQUEST_SENT",
                       "GATEWAY_TIMEOUT"])

    first, fingerprint, reused_first = _run(tid, _FakeML(0.05))
    assert reused_first is False
    assert _count_records(tid) == 1

    second, fingerprint2, reused_second = _run(tid, _FakeML(0.05))
    assert reused_second is True
    assert fingerprint2 == fingerprint
    assert second.assessment_id == first.assessment_id
    assert _count_records(tid) == 1


def test_new_events_new_assessment(client):
    """A new payment event changes the fingerprint: re-assessment, new row,
    reused=False."""
    tid = _unique_id()
    _seed_tx(tid)
    _seed_events(tid, ["CUSTOMER_DEBIT_CONFIRMED", "GATEWAY_REQUEST_SENT",
                       "GATEWAY_TIMEOUT"])

    first, _, reused_first = _run(tid, _FakeML(0.05))
    assert reused_first is False

    _seed_events(tid, ["MERCHANT_CONFIRMATION_TIMEOUT"])

    second, fingerprint2, reused_second = _run(tid, _FakeML(0.05))
    assert reused_second is False
    assert second.assessment_id != first.assessment_id
    assert _count_records(tid) == 2


def test_dataset_evidence_without_source_reads_back_ok(client):
    """db-branch dataset rows (risk_assessments.csv) store evidence items
    without the source column; the GET read path must stamp the honest
    DATASET provenance instead of 500ing (regression for the support
    workspace browser E2E finding)."""
    from api.schemas.risk_assessment import SOURCE_DATASET
    from api.services.risk_engine import assessment_from_record

    tid = _unique_id()
    _seed_tx(tid)
    db = SessionLocal()
    try:
        db.add(
            RiskAssessmentRecord(
                assessment_id=f"RSA-{uuid.uuid4().hex[:12]}",
                transaction_id=tid,
                evidence_fingerprint="dataset-seed-fingerprint",
                evidence=[{"code": "HIGH_RETRY", "description": "3 retries", "severity": "MEDIUM"}],
                triggered_rules=[],
                anomaly_type=ANOMALY_SUSPICIOUS,
                risk_level=RISK_HIGH,
                risk_score=0.7,
                ml_anomaly_score=0.5,
                deterministic_risk_score=0.7,
                recovery_candidate=True,
                model_version="synthetic-v1",
                rule_version="1",
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
        record = (
            db.query(RiskAssessmentRecord)
            .filter(RiskAssessmentRecord.transaction_id == tid)
            .one()
        )
        assessment = assessment_from_record(record)
        assert assessment.evidence[0].source == SOURCE_DATASET
    finally:
        db.close()

    # and the HTTP read path serves the row instead of a 500
    resp = client.get(
        f"/api/v1/transactions/{tid}/risk-assessment", headers=SYSTEM_KEY
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["assessment"]["evidence"][0]["source"] == SOURCE_DATASET

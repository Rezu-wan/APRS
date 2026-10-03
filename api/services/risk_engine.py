"""
api/services/risk_engine.py — Stage 7 HYBRID slice: combine the deterministic
rule engine (api/services/anomaly_rules.py) with the Stage 7 anomaly ML model
(ml/predict_anomaly.py) under the pinned precedence contract.

HYBRID PRECEDENCE (the core of this module, implemented EXACTLY as specified):

  1. Deterministic rules ALWAYS win the anomaly_type. ML never rewrites event
     facts: if R1 says DOUBLE_DEDUCTION, the classification is
     DOUBLE_DEDUCTION no matter what the model scores.
  2. ML contributes ml_anomaly_score. If the rules classification is
     INCOMPLETE or UNKNOWN (the rules could not establish an outcome) AND
     ml_anomaly_score >= 0.7 AND the model predicts a behaviorally suspicious
     scenario (suspicious_high_retry / duplicate_transaction /
     double_deduction), the assessment is upgraded to SUSPICIOUS:
     risk_level HIGH, recovery_candidate False, and an EvidenceItem
     (code ML_HIGH_ANOMALY, source "ML") citing the score and the model's
     predicted_scenario is appended. A predicted incomplete_event_chain
     merely CORROBORATES the rules' INCOMPLETE verdict — P(not normal) alone
     is escalation-worthy only when the behavior itself looks suspicious
     (otherwise sparse evidence would always upgrade, making INCOMPLETE
     unreachable whenever the model is loaded).
  3. risk_score = round(max(deterministic_risk_score,
     0.5 * ml_anomaly_score + 0.5 * deterministic_risk_score), 2) when ML is
     present; else the deterministic score. risk_level may rise ONE step
     (LOW -> MEDIUM -> HIGH -> CRITICAL) if the blended score crosses 0.75 —
     it may NEVER fall below the rules' level.
  4. ML unavailable (model not loaded / predict returns None) ->
     ml_anomaly_score None and a rules-only assessment. This MUST still work:
     the API degrades to the deterministic engine and logs once.

IDEMPOTENCY: evidence_fingerprint = sha256 over {transaction_id, sorted
(provider_event_id, event_type) pairs, reconstruction root_cause + confidence,
customer_reported_failure, effective model_version, RULE_VERSION}. If the
latest stored assessment for the transaction carries the same fingerprint it
is REUSED (reused=True) — no re-insert, no twin append.

Persistence: persist_assessment() and record_anomaly_classified() add rows
without committing — the CALLER (route layer) owns the commit, matching the
reconstruction/recovery services.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db.models import (
    DigitalTwinEvent,
    PaymentEvent,
    RiskAssessmentRecord,
    Transaction,
)
from api.schemas.risk_assessment import (
    ANOMALY_INCOMPLETE,
    ANOMALY_SUSPICIOUS,
    ANOMALY_UNKNOWN,
    EVIDENCE_ML_HIGH_ANOMALY,
    RISK_CRITICAL,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    SOURCE_DATASET,
    EvidenceItem,
    RiskAssessment,
    TriggeredRule,
)
from api.services.anomaly_rules import RULE_VERSION, assess_rules
from api.services.event_reconstruction import reconstruct_from_events
from api.services.metrics import (
    METRICS_RISK_ASSESSMENTS_TOTAL,
    record_counter,
)
from api.services.payment_event_service import get_payment_events

logger = logging.getLogger("payment_recovery.risk_engine")

ANOMALY_CLASSIFIED_EVENT_TYPE = "ANOMALY_CLASSIFIED"

# precedence rule 2: ML upgrade threshold
ML_UPGRADE_THRESHOLD = 0.7

# Scenarios that indicate BEHAVIORAL anomaly. The SUSPICIOUS upgrade requires
# the model to predict one of these — P(not normal_success) alone is not
# enough: a sparse/incomplete event chain scores high on "not normal" because
# incomplete_event_chain is itself an anomalous training class, but agreeing
# with the rules' INCOMPLETE verdict is corroboration, not suspicion. (Found
# in live E2E: a single-debit transaction scored 1.0 "not normal" purely from
# sparsity, which would have made INCOMPLETE unreachable whenever ML loaded.)
_SUSPICIOUS_LIKE_SCENARIOS = frozenset(
    {"suspicious_high_retry", "duplicate_transaction", "double_deduction"}
)
# precedence rule 3: blended score that may raise the risk level one step
ML_LEVEL_RISE_THRESHOLD = 0.75

_LEVEL_LADDER = (RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL)


def compute_reference_lookup(db: Session, transaction_id: str):
    """Build the reference_lookup callable for the rules engine.

    One bounded prefetch: all distinct reference_ids on THIS transaction's
    payment events, then a single query for OTHER transaction_ids sharing any
    of them. The returned callable answers from that in-memory map and never
    touches the DB again — R2 therefore costs exactly two queries regardless
    of how many events or references the transaction carries.
    """
    refs = db.scalars(
        select(PaymentEvent.reference_id)
        .where(
            PaymentEvent.transaction_id == transaction_id,
            PaymentEvent.reference_id.isnot(None),
        )
        .distinct()
    ).all()

    if not refs:
        return lambda reference_id: []

    rows = db.execute(
        select(PaymentEvent.reference_id, PaymentEvent.transaction_id)
        .where(
            PaymentEvent.reference_id.in_(refs),
            PaymentEvent.transaction_id != transaction_id,
        )
        .distinct()
    ).all()

    sharing: dict[str, set[str]] = {}
    for reference_id, other_tx in rows:
        sharing.setdefault(reference_id, set()).add(other_tx)

    def lookup(reference_id: str) -> list[str]:
        return sorted(sharing.get(reference_id, ()))

    return lookup


def _fingerprint(
    transaction_id: str,
    events: list[PaymentEvent],
    reconstruction,
    customer_reported_failure: bool,
    model_version: str,
) -> str:
    """sha256 over the assessment's input evidence (documented composition).

    model_version is the EFFECTIVE version ("rules-only" when ML did not
    contribute, ANOMALY_MODEL_VERSION when it did), so a model upgrade alone
    produces a new assessment instead of silently reusing the old one.
    """
    root_cause = reconstruction.root_cause if reconstruction is not None else None
    confidence = (
        reconstruction.reconstruction_confidence
        if reconstruction is not None
        else None
    )
    payload = json.dumps(
        {
            "transaction_id": transaction_id,
            "events": sorted(
                (str(e.provider_event_id), str(e.event_type)) for e in events
            ),
            "root_cause": root_cause,
            "reconstruction_confidence": confidence,
            "customer_reported_failure": customer_reported_failure,
            "model_version": model_version,
            "rule_version": RULE_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_latest_record(db: Session, transaction_id: str) -> RiskAssessmentRecord | None:
    """Latest stored assessment row for a transaction (reuse/GET path)."""
    return db.scalars(
        select(RiskAssessmentRecord)
        .where(RiskAssessmentRecord.transaction_id == transaction_id)
        .order_by(RiskAssessmentRecord.created_at.desc(), RiskAssessmentRecord.id.desc())
        .limit(1)
    ).one_or_none()


def _latest_record(db: Session, transaction_id: str) -> RiskAssessmentRecord | None:
    return db.scalars(
        select(RiskAssessmentRecord)
        .where(RiskAssessmentRecord.transaction_id == transaction_id)
        .order_by(RiskAssessmentRecord.created_at.desc(), RiskAssessmentRecord.id.desc())
        .limit(1)
    ).one_or_none()


def assessment_from_record(record: RiskAssessmentRecord) -> RiskAssessment:
    """Rebuild the pydantic assessment from a stored row (reuse/GET path)."""
    return _assessment_from_record(record)


def _assessment_from_record(record: RiskAssessmentRecord) -> RiskAssessment:
    """Rebuild the pydantic assessment from a stored row (reuse path)."""
    evidence = [
        # dataset-restored rows (db-branch risk_assessments.csv) predate the
        # source column — stamp the honest provenance instead of 500ing
        EvidenceItem(**{**e, "source": e.get("source") or SOURCE_DATASET})
        for e in (record.evidence or [])
    ]
    return RiskAssessment(
        transaction_id=record.transaction_id,
        assessment_id=record.assessment_id,
        anomaly_type=record.anomaly_type,
        risk_level=record.risk_level,
        risk_score=record.risk_score,
        ml_anomaly_score=record.ml_anomaly_score,
        deterministic_risk_score=record.deterministic_risk_score,
        recovery_candidate=record.recovery_candidate,
        recovery_block_reason=record.recovery_block_reason,
        evidence=evidence,
        triggered_rules=[TriggeredRule(**r) for r in (record.triggered_rules or [])],
        reconstruction_root_cause=record.reconstruction_root_cause,
        reconstruction_confidence=record.reconstruction_confidence,
        customer_reported_failure=record.customer_reported_failure,
        model_version=record.model_version,
        rule_version=record.rule_version,
        created_at=record.created_at,
    )


def _apply_ml(
    assessment: RiskAssessment,
    ml: dict | None,
    now: datetime,
) -> RiskAssessment:
    """Apply precedence rules 2-3 to a rules-only assessment. Pure — returns
    the (possibly new) assessment object; ML never rewrites the anomaly_type
    established by the rules."""
    if ml is None:
        return assessment

    score = float(ml.get("ml_anomaly_score", 0.0) or 0.0)
    predicted = str(ml.get("predicted_scenario", "unknown"))

    evidence = list(assessment.evidence)
    anomaly_type = assessment.anomaly_type
    risk_level = assessment.risk_level
    candidate = assessment.recovery_candidate
    block_reason = assessment.recovery_block_reason

    upgraded = False
    if (
        anomaly_type in (ANOMALY_INCOMPLETE, ANOMALY_UNKNOWN)
        and score >= ML_UPGRADE_THRESHOLD
        and predicted in _SUSPICIOUS_LIKE_SCENARIOS
    ):
        # precedence rule 2: the rules could not establish an outcome AND the
        # model predicts a behaviorally suspicious scenario (not merely "not
        # normal" — a predicted incomplete_event_chain corroborates INCOMPLETE,
        # it does not escalate it) — SUSPICIOUS, and never a recovery candidate
        # on an ML signal alone.
        anomaly_type = ANOMALY_SUSPICIOUS
        risk_level = RISK_HIGH
        candidate = False
        block_reason = (
            "high ML anomaly score without a deterministic classification"
        )
        upgraded = True
        evidence.append(
            EvidenceItem(
                code=EVIDENCE_ML_HIGH_ANOMALY,
                description=(
                    f"ML anomaly score {score:.2f} at/above the "
                    f"{ML_UPGRADE_THRESHOLD} upgrade threshold "
                    f"(predicted scenario: {predicted}); the deterministic "
                    "rules found no determinable outcome."
                ),
                source="ML",
                severity="HIGH",
            )
        )

    # precedence rule 3: blended score, never below the deterministic score;
    # the level may rise ONE step if the blend crosses 0.75 — never fall.
    # (Skipped when rule 2 already set the level explicitly: the upgrade IS
    # the ML-driven level decision for that path.)
    blended = round(
        max(
            assessment.deterministic_risk_score,
            0.5 * score + 0.5 * assessment.deterministic_risk_score,
        ),
        2,
    )
    if (
        not upgraded
        and risk_level in _LEVEL_LADDER
        and blended > ML_LEVEL_RISE_THRESHOLD
    ):
        idx = _LEVEL_LADDER.index(risk_level)
        if idx < len(_LEVEL_LADDER) - 1:
            risk_level = _LEVEL_LADDER[idx + 1]

    return assessment.model_copy(
        update={
            "anomaly_type": anomaly_type,
            "risk_level": risk_level,
            "risk_score": blended,
            "ml_anomaly_score": round(score, 4),
            "recovery_candidate": candidate,
            "recovery_block_reason": block_reason,
            "evidence": evidence,
            "model_version": _model_version(),
            "created_at": now,
        }
    )


def _model_version() -> str:
    from ml.predict_anomaly import ANOMALY_MODEL_VERSION

    return ANOMALY_MODEL_VERSION


def run_assessment(
    db: Session,
    tx: Transaction,
    ml_service,
    *,
    customer_reported_failure: bool,
) -> tuple[RiskAssessment, str, bool]:
    """Full hybrid assessment for one transaction.

    Returns (assessment, evidence_fingerprint, reused). When the latest
    stored record carries the same fingerprint the assessment is rebuilt from
    that row and reused=True — nothing is re-inserted and no twin event is
    appended by the caller.

    ``ml_service`` is the MLService wrapper (duck-typed: needs
    predict_anomaly_features); ML unavailability degrades to rules-only
    (precedence rule 4) and is logged once per occurrence.
    """
    now = datetime.now(timezone.utc)

    events = get_payment_events(db, tx.transaction_id)
    reconstruction = (
        reconstruct_from_events(tx.transaction_id, events, now) if events else None
    )

    # fingerprint BEFORE ML is applied: the rules-only fingerprint decides
    # reuse; a different ML outcome simply re-assesses (rule 4's model
    # version is part of the payload composition, see _fingerprint)
    probe_ml = ml_service.predict_anomaly_features(
        _build_features(tx, events, reconstruction)
    )
    model_version = _model_version() if probe_ml is not None else "rules-only"
    fingerprint = _fingerprint(
        tx.transaction_id,
        events,
        reconstruction,
        customer_reported_failure,
        model_version,
    )

    latest = _latest_record(db, tx.transaction_id)
    if latest is not None and latest.evidence_fingerprint == fingerprint:
        logger.info(
            "risk assessment reused: tx=%s anomaly_type=%s risk_level=%s",
            tx.transaction_id, latest.anomaly_type, latest.risk_level,
        )
        return _assessment_from_record(latest), fingerprint, True

    assessment = assess_rules(
        tx,
        events,
        reconstruction
        if reconstruction is not None
        else reconstruct_from_events(tx.transaction_id, [], now),
        customer_reported_failure=customer_reported_failure,
        now=now,
        reference_lookup=compute_reference_lookup(db, tx.transaction_id),
    )
    assessment = _apply_ml(assessment, probe_ml, now)

    logger.info(
        "risk assessment computed: tx=%s anomaly_type=%s risk_level=%s "
        "risk_score=%.2f ml_anomaly_score=%s",
        tx.transaction_id,
        assessment.anomaly_type,
        assessment.risk_level,
        assessment.risk_score,
        assessment.ml_anomaly_score,
    )
    return assessment, fingerprint, False


def _build_features(tx, events, reconstruction) -> dict:
    from ml.predict_anomaly import build_features

    return build_features(tx, events, reconstruction)


def persist_assessment(
    db: Session,
    tx: Transaction,
    assessment: RiskAssessment,
    fingerprint: str,
) -> RiskAssessmentRecord:
    """Insert the assessment row (NO commit — the caller commits)."""
    record = RiskAssessmentRecord(
        assessment_id=assessment.assessment_id,
        transaction_id=tx.transaction_id,
        evidence_fingerprint=fingerprint,
        anomaly_type=assessment.anomaly_type,
        risk_level=assessment.risk_level,
        risk_score=assessment.risk_score,
        ml_anomaly_score=assessment.ml_anomaly_score,
        deterministic_risk_score=assessment.deterministic_risk_score,
        recovery_candidate=assessment.recovery_candidate,
        recovery_block_reason=assessment.recovery_block_reason,
        reconstruction_root_cause=assessment.reconstruction_root_cause,
        reconstruction_confidence=assessment.reconstruction_confidence,
        customer_reported_failure=assessment.customer_reported_failure,
        evidence=[e.model_dump() for e in assessment.evidence],
        triggered_rules=[r.model_dump() for r in assessment.triggered_rules],
        model_version=assessment.model_version,
        rule_version=assessment.rule_version,
    )
    db.add(record)
    record_counter(METRICS_RISK_ASSESSMENTS_TOTAL)  # Stage 11G: genuinely new assessments only (service-side)
    return record


def record_anomaly_classified(
    db: Session,
    tx: Transaction,
    assessment: RiskAssessment,
    fingerprint: str,
) -> bool:
    """Append the ANOMALY_CLASSIFIED observation to the transaction-state
    Digital Twin.

    An OBSERVATION, not a transition: previous_state == new_state ==
    tx.current_state — the state machine is untouched (same convention as
    record_root_cause_event for ROOT_CAUSE_IDENTIFIED).

    Idempotent on fingerprint: if the latest ANOMALY_CLASSIFIED event for the
    transaction already carries the same evidence fingerprint, nothing is
    appended (returns False). The CALLER owns the commit.
    """
    latest = db.scalars(
        select(DigitalTwinEvent)
        .where(
            DigitalTwinEvent.transaction_id == tx.transaction_id,
            DigitalTwinEvent.event_type == ANOMALY_CLASSIFIED_EVENT_TYPE,
        )
        .order_by(DigitalTwinEvent.timestamp.desc(), DigitalTwinEvent.id.desc())
        .limit(1)
    ).one_or_none()

    if latest is not None:
        meta = latest.event_metadata or {}
        if meta.get("evidence_fingerprint") == fingerprint:
            return False

    from api.services.digital_twin import append_event

    append_event(
        db,
        tx,
        previous_state=tx.current_state,
        new_state=tx.current_state,
        event_type=ANOMALY_CLASSIFIED_EVENT_TYPE,
        reason=f"Anomaly: {assessment.anomaly_type} ({assessment.risk_level})",
        event_metadata={
            "anomaly_type": assessment.anomaly_type,
            "risk_level": assessment.risk_level,
            "risk_score": assessment.risk_score,
            "recovery_candidate": assessment.recovery_candidate,
            "triggered_rules": [r.rule_id for r in assessment.triggered_rules],
            "assessment_id": assessment.assessment_id,
            "model_version": assessment.model_version,
            "rule_version": assessment.rule_version,
            "evidence_fingerprint": fingerprint,
        },
    )
    logger.info(
        "anomaly classified recorded: tx=%s anomaly_type=%s",
        tx.transaction_id, assessment.anomaly_type,
    )
    return True

"""
api/services/autonomous_recovery.py — Stage 8 ORCHESTRATION (spec section 26).

One transaction, one call, the full pipeline:

    assessment (risk_engine.run_assessment, REUSED idempotently by
    evidence fingerprint) -> reconstruction -> decision policy
    -> [if RELEASE_LIMIT] recovery_executor.execute_recovery
    -> [else] BLOCKED row + eligibility twin observation

Auth/architecture note: process/execute is SYSTEM/ADMIN only (it moves
simulated money); evaluate and read are additionally open to SUPPORT so
support staff can see WHY a transaction was or was not auto-recovered without
being able to trigger execution — the same read/act split as the Stage 7
risk-assessment endpoints.

Idempotency: the idempotency key is derived by the executor from
(transaction_id, action, policy_version, evidence_fingerprint) — the same
evidence replays, new evidence may warrant a new row. The eligibility twin
observation (RECOVERY_ELIGIBILITY_ASSESSED) is idempotent per fingerprint:
it is appended only when the assessment fingerprint CHANGED (assessment was
recomputed, reused=False).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.core.exceptions import (
    InvalidStateTransitionError,
    NotFoundError,
)
from api.core.state_machine import TransactionState, validate_transition
from api.db.models import RecoveryActionRecord, RecoveryDecision, Transaction
from api.schemas.recovery_autonomous import (
    ACTION_MANUAL_REVIEW,
    ACTION_RELEASE_LIMIT,
    RecoveryDecision as AutonomousDecision,
)
from api.services.digital_twin import append_event
from api.services.event_reconstruction import reconstruct_from_events
from api.services.metrics import (
    METRICS_MANUAL_REVIEW_TOTAL,
    METRICS_RECOVERY_BLOCKED_TOTAL,
    METRICS_RECOVERY_FAILED_TOTAL,
    METRICS_RECOVERY_LATENCY,
    METRICS_RECOVERY_SUCCESS_TOTAL,
    record_counter,
    record_latency,
)
from api.services.payment_event_service import get_payment_events
from api.services.recovery_decision_policy import decide
from api.services.recovery_executor import (
    compute_idempotency_key,
    execute_recovery,
    get_row_by_key,
)
from api.services.recovery_safety import check_safety
from api.services.risk_engine import (
    assessment_from_record,
    get_latest_record,
    persist_assessment,
    record_anomaly_classified,
    run_assessment,
)
from api.services.transaction_service import get_transaction

logger = logging.getLogger("payment_recovery.autonomous_recovery")

ELIGIBILITY_EVENT_TYPE = "RECOVERY_ELIGIBILITY_ASSESSED"

# Response "decision" values (pinned API vocabulary).
DECISION_AUTO_RECOVERED = "AUTO_RECOVERED"
DECISION_BLOCKED = "RECOVERY_BLOCKED"
DECISION_MANUAL_REVIEW_QUEUED = "MANUAL_REVIEW_QUEUED"
DECISION_ALREADY_RECOVERED = "ALREADY_RECOVERED"


def _assessment_summary(assessment) -> dict:
    return {
        "anomaly_type": assessment.anomaly_type,
        "risk_level": assessment.risk_level,
        "recovery_candidate": assessment.recovery_candidate,
    }


def _log(event: str, **fields) -> None:
    logger.info(
        "%s: %s",
        event,
        " ".join(f"{k}={v}" for k, v in fields.items()),
    )


def process_transaction(
    db: Session,
    ml,
    provider,
    transaction_id: str,
    *,
    customer_reported_failure: bool = False,
) -> dict:
    """Run the full autonomous-recovery pipeline for one transaction.

    NO commit here — the route layer commits ONCE (the executor flushed each
    write as it went).
    """
    now = datetime.now(timezone.utc)
    _t0 = time.perf_counter()

    def _finish(response: dict) -> dict:
        """Stage 11G outcome counters, service-side so EVERY caller (route,
        demo prepare, chaos) is counted. Replays (ALREADY_RECOVERED) are not
        new attempts; a FAILED row is a provider/verification failure, any
        other non-eligible outcome is a blocked recovery."""
        decision = response.get("decision")
        if decision == DECISION_AUTO_RECOVERED:
            record_counter(METRICS_RECOVERY_SUCCESS_TOTAL)
        elif decision == DECISION_MANUAL_REVIEW_QUEUED:
            record_counter(METRICS_MANUAL_REVIEW_TOTAL)
        elif decision == DECISION_BLOCKED:
            if response.get("status") == "FAILED":
                record_counter(METRICS_RECOVERY_FAILED_TOTAL)
            else:
                record_counter(METRICS_RECOVERY_BLOCKED_TOTAL)
        record_latency(
            METRICS_RECOVERY_LATENCY, (time.perf_counter() - _t0) * 1000
        )
        return response

    tx = get_transaction(db, transaction_id)
    if tx is None:
        raise NotFoundError(f"transaction {transaction_id} not found")

    _log(
        "recovery_evaluation_started",
        transaction_id=transaction_id,
        customer_reported_failure=customer_reported_failure,
    )

    assessment, fingerprint, reused = run_assessment(
        db,
        tx,
        ml,
        customer_reported_failure=customer_reported_failure,
    )
    if not reused:
        persist_assessment(db, tx, assessment, fingerprint)
        record_anomaly_classified(db, tx, assessment, fingerprint)
        db.flush()

    events = get_payment_events(db, transaction_id)
    reconstruction = reconstruct_from_events(transaction_id, events, now)
    decision = decide(tx, assessment, reconstruction, now=now)

    twin_recorded = not reused  # ANOMALY_CLASSIFIED appended above on recompute

    if decision.eligible and decision.action == ACTION_RELEASE_LIMIT:
        row, info = execute_recovery(
            db, tx, decision, assessment, fingerprint, provider, now=now
        )
        twin_recorded = twin_recorded or bool(info["digital_twin_events"])
        if info["already_recovered"]:
            response_decision = (
                DECISION_BLOCKED
                if row.status == "BLOCKED"
                else DECISION_ALREADY_RECOVERED
            )
        elif row.status in ("COMPLETED", "VERIFIED"):
            response_decision = DECISION_AUTO_RECOVERED
        else:
            response_decision = DECISION_BLOCKED
        _log(
            "recovery_execution_finished",
            transaction_id=transaction_id,
            recovery_id=row.recovery_id,
            status=row.status,
            decision=response_decision,
            policy_version=decision.policy_version,
            risk_assessment_id=decision.risk_assessment_id,
        )
        return _finish(_response(
            tx, decision, row, response_decision,
            assessment, twin_recorded,
        ))

    # ---- not eligible: persist a BLOCKED row (idempotent by key) ----------
    key = compute_idempotency_key(
        transaction_id, decision.action, decision.policy_version, fingerprint
    )
    row = get_row_by_key(db, key)
    if row is None:
        row = RecoveryActionRecord(
            transaction_id=transaction_id,
            action=decision.action,
            status="BLOCKED",
            idempotency_key=key,
            attempt_count=1,
            requested_amount=tx.amount,
            currency=tx.currency,
            policy_version=decision.policy_version,
            risk_assessment_id=decision.risk_assessment_id,
            decision_reason=decision.decision_reason,
            blocked_reason=decision.blocked_reason,
        )
        db.add(row)
        db.flush()
        twin_recorded = True

    # eligibility observation — only when the assessment fingerprint changed
    if not reused:
        append_event(
            db,
            tx,
            previous_state=tx.current_state,
            new_state=tx.current_state,
            event_type=ELIGIBILITY_EVENT_TYPE,
            reason=f"Recovery eligibility: {decision.action} "
            f"({decision.blocked_reason})",
            event_metadata={
                "eligible": decision.eligible,
                "action": decision.action,
                "blocked_reason": decision.blocked_reason,
                "policy_version": decision.policy_version,
                "risk_assessment_id": decision.risk_assessment_id,
                "evidence_fingerprint": fingerprint,
            },
        )
        twin_recorded = True

    response_decision = DECISION_BLOCKED
    if decision.action == ACTION_MANUAL_REVIEW:
        response_decision = _queue_manual_review(db, tx, decision, assessment)

    _log(
        "recovery_evaluation_blocked",
        transaction_id=transaction_id,
        recovery_id=row.recovery_id,
        action=decision.action,
        blocked_reason=decision.blocked_reason,
        policy_version=decision.policy_version,
        risk_assessment_id=decision.risk_assessment_id,
    )
    return _finish(_response(
        tx, decision, row, response_decision, assessment, twin_recorded
    ))


def _queue_manual_review(
    db: Session,
    tx: Transaction,
    decision: AutonomousDecision,
    assessment,
) -> str:
    """Mirror recovery_service semantics for a MANUAL_REVIEW outcome: insert
    the Stage-3 decision row (idempotent via UNIQUE transaction_id) and move
    the state when the transition is legal. Returns the response decision."""
    existing = db.scalars(
        select(RecoveryDecision).where(
            RecoveryDecision.transaction_id == tx.transaction_id
        )
    ).one_or_none()
    if existing is None:
        row = RecoveryDecision(
            transaction_id=tx.transaction_id,
            decision="MANUAL_REVIEW",
            safe_to_release=bool(assessment.recovery_candidate),
            safe_to_release_probability=float(assessment.risk_score),
            risk_score=float(assessment.risk_score),
            reason=decision.decision_reason,
            decided_by="AUTONOMOUS_POLICY",
            policy_snapshot={
                "policy_version": decision.policy_version,
                "blocked_reason": decision.blocked_reason,
                "simulated": True,
            },
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            # a concurrent winner inserted the decision — its row is the
            # answer for both callers
            db.rollback()
            logger.info(
                "manual review decision race lost: transaction_id=%s",
                tx.transaction_id,
            )

    try:
        validate_transition(tx.current_state, TransactionState.MANUAL_REVIEW)
    except InvalidStateTransitionError:
        # not in a state that can move to MANUAL_REVIEW (e.g. already moved)
        return DECISION_BLOCKED

    if tx.current_state != TransactionState.MANUAL_REVIEW:
        previous_state = tx.current_state
        tx.current_state = TransactionState.MANUAL_REVIEW
        append_event(
            db,
            tx,
            previous_state=previous_state,
            new_state=TransactionState.MANUAL_REVIEW,
            reason=decision.decision_reason,
            event_metadata={
                "decided_by": "AUTONOMOUS_POLICY",
                "policy_version": decision.policy_version,
                "simulated": True,
            },
        )
    return DECISION_MANUAL_REVIEW_QUEUED


def _response(
    tx: Transaction,
    decision: AutonomousDecision,
    row: RecoveryActionRecord,
    response_decision: str,
    assessment,
    twin_recorded: bool,
) -> dict:
    return {
        "transaction_id": tx.transaction_id,
        "decision": response_decision,
        "action": decision.action,
        "status": row.status,
        "recovery_id": row.recovery_id,
        "provider_reference": row.provider_reference,
        "reason": decision.decision_reason,
        "simulated": True,
        "assessment": _assessment_summary(assessment),
        "digital_twin_recorded": twin_recorded,
    }


def evaluate_transaction(db: Session, ml, transaction_id: str) -> dict:
    """Policy + safety ONLY — no row writes, no provider calls, no twin
    events. Returns the pinned evaluation shape."""
    tx = get_transaction(db, transaction_id)
    if tx is None:
        raise NotFoundError(f"transaction {transaction_id} not found")

    now = datetime.now(timezone.utc)
    _log("recovery_evaluation_started", transaction_id=transaction_id, mode="evaluate")

    assessment, fingerprint, _reused = run_assessment(
        db, tx, ml, customer_reported_failure=False
    )
    events = get_payment_events(db, transaction_id)
    reconstruction = reconstruct_from_events(transaction_id, events, now)
    decision = decide(tx, assessment, reconstruction, now=now)

    blocked_reason = decision.blocked_reason
    eligible = decision.eligible
    if eligible:
        # the safety gate re-derives from fresh data, exactly as the executor
        # would — evaluate must never promise what the gate would refuse
        latest_record = get_latest_record(db, transaction_id)
        latest_assessment = (
            assessment_from_record(latest_record)
            if latest_record is not None
            else assessment  # nothing persisted yet — use the just-computed one
        )
        gate = check_safety(
            tx,
            events,
            reconstruction,
            latest_assessment,
            None,
            now=now,
        )
        if not gate.allowed:
            eligible = False
            blocked_reason = gate.blocked_reason

    _log(
        "recovery_evaluation_finished",
        transaction_id=transaction_id,
        eligible=eligible,
        action=decision.action,
        blocked_reason=blocked_reason,
        policy_version=decision.policy_version,
    )
    return {
        "transaction_id": transaction_id,
        "eligible": eligible,
        "action": decision.action,
        "decision_reason": decision.decision_reason,
        "blocked_reason": blocked_reason,
        "policy_version": decision.policy_version,
        "simulated": True,
        "assessment": _assessment_summary(assessment),
    }

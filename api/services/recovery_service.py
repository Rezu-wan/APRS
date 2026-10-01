"""
api/services/recovery_service.py — idempotent recovery operations.

POST /api/v1/recovery/release-limit flow:

  1. load the transaction (404 if unknown)
  2. IDEMPOTENCY: a stored recovery_decisions row (UNIQUE per transaction)
     means the decision was already made — return it unchanged, create
     nothing. This also covers concurrent duplicate requests: the unique
     constraint is the source of truth, not a state check alone.
  3. if the outcome arrived but no assessment ran yet, run it now
     (FAILED/STALLED -> RISK_ASSESSED -> RECOVERY_PENDING with twin events)
  4. otherwise the transaction must sit in RECOVERY_PENDING (else HTTP 409 —
     e.g. SUCCESS has nothing to recover, PROCESSING is still in flight)
  5. run the recovery POLICY (recovery_policy.py) — never the raw ML score,
     never GenAI
  6. transition state, insert the decision row, append the twin event, commit
     atomically
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.core.config import Settings
from api.core.exceptions import ConflictError, NotFoundError
from api.core.state_machine import TransactionState, validate_transition
from api.db.models import RecoveryDecision, Transaction
from api.services.digital_twin import append_event
from api.services.ml_service import MLService
from api.services.recovery_policy import evaluate_policy
from api.services.transaction_service import run_assessment

logger = logging.getLogger("payment_recovery.recovery")


def _assessment_from_tx(tx: Transaction) -> dict:
    return {
        "failure_prediction": tx.failure_prediction,
        "failure_probabilities": {},
        "failure_probability": tx.failure_probability or 0.0,
        "risk_score": tx.risk_score or 0.0,
        "safe_to_release_probability": tx.safe_to_release_probability or 0.0,
        "safe_to_release": bool(tx.safe_to_release),
    }


def _decision_payload(decision: RecoveryDecision, tx: Transaction) -> dict:
    return {
        "transaction_id": tx.transaction_id,
        "decision": decision.decision,
        "safe_to_release": decision.safe_to_release,
        "safe_to_release_probability": decision.safe_to_release_probability,
        "risk_score": decision.risk_score,
        "reason": decision.reason,
        "decided_by": decision.decided_by,
        "decided_at": decision.created_at,
        "already_applied": True,
        "current_state": tx.current_state,
    }


def request_release(
    db: Session,
    ml: MLService,
    settings: Settings,
    transaction_id: str,
    role: str,
) -> dict:
    logger.info("recovery requested: id=%s role=%s", transaction_id, role)

    tx = db.scalars(
        select(Transaction).where(Transaction.transaction_id == transaction_id)
    ).one_or_none()
    if tx is None:
        raise NotFoundError(f"transaction {transaction_id} not found")

    existing = db.scalars(
        select(RecoveryDecision).where(RecoveryDecision.transaction_id == transaction_id)
    ).one_or_none()
    if existing is not None:
        logger.info(
            "idempotent replay: id=%s decision=%s", transaction_id, existing.decision
        )
        return _decision_payload(existing, tx)

    # If the outcome landed but assessment has not run (e.g. caller jumped
    # straight to recovery), assess first so the decision uses fresh ML output.
    if tx.current_state in (TransactionState.FAILED, TransactionState.STALLED):
        assessment, _ = run_assessment(db, tx, ml, settings)
        db.flush()
    elif tx.current_state == TransactionState.RECOVERY_PENDING:
        assessment = _assessment_from_tx(tx)
    else:
        raise ConflictError(
            f"transaction {transaction_id} is in state {tx.current_state}: "
            "recovery can only run on assessed, unresolved transactions"
        )

    policy = evaluate_policy(tx, assessment, settings)
    validate_transition(tx.current_state, policy.decision)

    previous_state = tx.current_state
    tx.current_state = policy.decision

    decision = RecoveryDecision(
        transaction_id=tx.transaction_id,
        decision=policy.decision,
        safe_to_release=policy.safe_to_release,
        safe_to_release_probability=assessment["safe_to_release_probability"],
        risk_score=assessment["risk_score"],
        reason=policy.reason,
        decided_by=role,
        policy_snapshot=settings.recovery_policy_snapshot(),
    )
    db.add(decision)

    append_event(
        db,
        tx,
        previous_state=previous_state,
        new_state=policy.decision,
        ml=assessment,
        reason=policy.reason,
        event_metadata={
            "decided_by": role,
            "policy": settings.recovery_policy_snapshot(),
        },
    )

    try:
        db.commit()
    except IntegrityError:
        # concurrent duplicate request lost the UNIQUE(transaction_id) race —
        # the winner's decision is the answer for both callers
        db.rollback()
        winner = db.scalars(
            select(RecoveryDecision).where(
                RecoveryDecision.transaction_id == transaction_id
            )
        ).one_or_none()
        if winner is not None:
            db.refresh(tx)
            logger.info(
                "idempotent replay (race): id=%s decision=%s",
                transaction_id, winner.decision,
            )
            return _decision_payload(winner, tx)
        raise

    db.refresh(tx)
    logger.info(
        "recovery decision made: id=%s decision=%s by=%s",
        transaction_id, policy.decision, role,
    )
    if policy.decision == "LIMIT_RELEASED":
        logger.info("limit released: id=%s", transaction_id)
    elif policy.decision == "MANUAL_REVIEW":
        logger.info("manual review triggered: id=%s", transaction_id)

    payload = _decision_payload(decision, tx)
    payload["already_applied"] = False
    return payload

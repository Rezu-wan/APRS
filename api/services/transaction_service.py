"""
api/services/transaction_service.py — transaction ingestion & lifecycle.

record_event() implements POST /api/v1/transaction/event:

  1. find or create the transaction
  2. validate the requested state change against the state machine
     (a repeat of the transaction's current state is an idempotent no-op;
      an illegal change raises InvalidStateTransitionError -> HTTP 400)
  3. walk the legal transition path, appending one Digital Twin event per hop
  4. when the outcome is FAILED/STALLED, run the ML assessment once and chain
     FAILED/STALLED -> RISK_ASSESSED -> RECOVERY_PENDING (audit events per hop)
  5. commit state + events atomically in ONE database transaction

The ML decision (not GenAI, not the API caller) is what lands on the
transaction record; the recovery policy runs later in recovery_service.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.core.config import Settings
from api.core.state_machine import TransactionState, transition_path
from api.db.models import DigitalTwinEvent, Transaction
from api.services.digital_twin import append_event
from api.services.ml_service import MLService

logger = logging.getLogger("payment_recovery.transactions")

# FAILED is terminal: assess immediately so recovery can run. STALLED is NOT
# auto-assessed — a stall may resolve (STALLED -> PROCESSING retry), so the
# transaction stays in STALLED until either a retry event or an explicit
# recovery request (recovery_service assesses on demand).
_ASSESS_ON = {TransactionState.FAILED}


@dataclass
class RecordEventResult:
    transaction: Transaction
    created: bool = False
    ml_assessment: dict | None = None
    events: list[DigitalTwinEvent] = field(default_factory=list)

    @property
    def state_changed(self) -> bool:
        # the registration event has previous_state=None; every hop after it
        # is a real state change
        return any(e.previous_state is not None for e in self.events)


def _find_transaction(db: Session, transaction_id: str) -> Transaction | None:
    return db.scalars(
        select(Transaction).where(Transaction.transaction_id == transaction_id)
    ).one_or_none()


def _apply_payload_fields(tx: Transaction, payload) -> None:
    """Refresh the transaction's raw attributes with the latest observation."""
    tx.user_id = payload.user_id
    tx.merchant_id = payload.merchant_id
    tx.amount = payload.amount
    tx.currency = payload.currency
    if payload.timestamp is not None:
        tx.timestamp = payload.timestamp
    tx.gateway_latency_ms = payload.gateway_latency_ms
    tx.retry_count = payload.retry_count
    tx.network_quality = payload.network_quality
    tx.previous_failures = payload.previous_failures
    tx.account_age_days = payload.account_age_days
    tx.failure_reason = payload.failure_reason


def run_assessment(
    db: Session, tx: Transaction, ml: MLService, settings: Settings
) -> tuple[dict, list[DigitalTwinEvent]]:
    """Assess risk and chain <outcome> -> RISK_ASSESSED -> RECOVERY_PENDING.

    Returns (assessment, events_created); the caller owns the commit.
    """
    assessment = ml.assess(
        amount=tx.amount,
        gateway_latency_ms=tx.gateway_latency_ms,
        retry_count=tx.retry_count,
        network_quality=tx.network_quality,
        previous_failures=tx.previous_failures,
        account_age_days=tx.account_age_days,
        status=tx.current_state,
        failure_reason=tx.failure_reason,
    )
    tx.failure_prediction = assessment["failure_prediction"]
    tx.failure_probability = assessment["failure_probability"]
    tx.risk_score = assessment["risk_score"]
    tx.safe_to_release_probability = assessment["safe_to_release_probability"]
    tx.safe_to_release = assessment["safe_to_release"]

    events: list[DigitalTwinEvent] = []
    previous = tx.current_state
    for hop in (TransactionState.RISK_ASSESSED, TransactionState.RECOVERY_PENDING):
        events.append(
            append_event(
                db,
                tx,
                previous_state=previous,
                new_state=hop,
                ml=assessment if hop == TransactionState.RISK_ASSESSED else None,
                reason=(
                    "ML risk assessment completed"
                    if hop == TransactionState.RISK_ASSESSED
                    else "awaiting recovery decision"
                ),
                event_metadata={"policy": settings.recovery_policy_snapshot()}
                if hop == TransactionState.RISK_ASSESSED
                else None,
            )
        )
        previous = hop
        tx.current_state = hop
    return assessment, events


def record_event(
    db: Session, payload, ml: MLService, settings: Settings
) -> RecordEventResult:
    transaction_id = payload.transaction_id
    logger.info(
        "transaction event received: id=%s status=%s", transaction_id, payload.status
    )

    tx = _find_transaction(db, transaction_id)
    created = tx is None
    if not created:
        _apply_payload_fields(tx, payload)
        result = RecordEventResult(transaction=tx)
    else:
        tx = Transaction(
            transaction_id=transaction_id,
            current_state=TransactionState.INITIATED,
        )
        # apply fields BEFORE the first flush so the INSERT is complete
        _apply_payload_fields(tx, payload)
        db.add(tx)
        try:
            db.flush()
        except IntegrityError:
            # concurrent first-event for the same transaction_id: adopt the
            # winner's row and continue as a normal update (idempotent merge)
            db.rollback()
            tx = _find_transaction(db, transaction_id)
            if tx is None:
                raise
            created = False
            _apply_payload_fields(tx, payload)
            result = RecordEventResult(transaction=tx)
            logger.info(
                "concurrent create for %s: merging into existing row", transaction_id
            )
        if created:
            result = RecordEventResult(transaction=tx, created=True)
            result.events.append(
                append_event(
                    db, tx,
                    previous_state=None,
                    new_state=TransactionState.INITIATED,
                    reason="transaction registered",
                )
            )
            logger.info("transaction created: id=%s", transaction_id)

    path = transition_path(tx.current_state, str(payload.status.value))
    previous = path[0]
    for hop in path[1:]:
        result.events.append(append_event(db, tx, previous_state=previous, new_state=hop))
        previous = hop
        tx.current_state = hop

    if tx.current_state in _ASSESS_ON and tx.risk_score is None:
        result.ml_assessment, assessment_events = run_assessment(db, tx, ml, settings)
        result.events.extend(assessment_events)

    db.commit()
    db.refresh(tx)
    if not created:
        logger.info(
            "transaction event applied: id=%s state=%s hops=%s",
            transaction_id, tx.current_state,
            [e.new_state for e in result.events],
        )
    return result


def get_transaction(db: Session, transaction_id: str) -> Transaction | None:
    return _find_transaction(db, transaction_id)

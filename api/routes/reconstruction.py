"""
api/routes/reconstruction.py — Stage 6 payment-event reconstruction endpoint.

Read-only over stored payment events, plus ONE idempotent side effect: a
ROOT_CAUSE_IDENTIFIED event is appended to the Digital Twin the first time a
reconstruction reaches a verdict-worthy state (the service layer decides
idempotency; the twin is never appended twice for the same conclusion).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.core.exceptions import ForbiddenError, NotFoundError
from api.core.payment_lifecycle import PaymentStage
from api.core.security import AuthContext, can_access_transaction, require_roles
from api.db.database import get_db
from api.schemas.reconstruction import ReconstructionResult
from api.services.event_reconstruction import reconstruct_from_events
from api.services.payment_event_service import (
    get_payment_events,
    record_root_cause_event,
)
from api.services.transaction_service import get_transaction

logger = logging.getLogger("payment_recovery.reconstruction")

router = APIRouter(prefix="/api/v1/transactions", tags=["reconstruction"])


@router.get(
    "/{transaction_id}/reconstruction",
    response_model=ReconstructionResult,
    responses={404: {"description": "Transaction not found"}},
)
def get_reconstruction(
    transaction_id: str,
    db: Session = Depends(get_db),
    # Stage 9: CUSTOMER opened with OWNERSHIP scoping. The reconstruction
    # engine is pure-read over payment events and shows the payment story —
    # acceptable for OWNED transactions (internals are stripped at the
    # explanations layer, which stays the only surface for AI narratives).
    # Unowned/unknown ids for CUSTOMER → non-enumerating 403.
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT", "CUSTOMER")),
):
    """Deterministically reconstruct the payment lifecycle from stored
    payment-domain events. Pure derivation (no LLM, no invented events) plus
    one idempotent Digital Twin append for the identified root cause."""
    tx = get_transaction(db, transaction_id)
    if auth.role == "CUSTOMER":
        if not can_access_transaction(auth, tx):
            raise ForbiddenError("transaction not accessible")
    elif tx is None:
        raise NotFoundError(f"transaction {transaction_id} not found")

    events = get_payment_events(db, transaction_id)
    result = reconstruct_from_events(
        transaction_id, events, datetime.now(timezone.utc)
    )

    twin_payload = {
        "root_cause": result.root_cause,
        "failure_stage": result.failure_stage,
        "last_successful_stage": result.last_successful_stage,
        "stage_statuses": {
            PaymentStage.BANK_DEBIT: result.customer_debit_status,
            PaymentStage.GATEWAY: result.gateway_status,
            PaymentStage.MERCHANT_CONFIRMATION: result.merchant_confirmation_status,
            PaymentStage.SETTLEMENT: result.settlement_status,
        },
        "missing_events": result.missing_events,
    }
    recorded = record_root_cause_event(db, tx, twin_payload)
    if recorded:
        db.commit()

    result.digital_twin_event_recorded = recorded

    logger.info(
        "reconstruction transaction_id=%s events_loaded=%d root_cause=%s failure_stage=%s",
        transaction_id,
        len(events),
        result.root_cause,
        result.failure_stage,
    )
    return result

"""
api/routes/payment_events.py — Stage 6 payment-domain event ingestion.

Additive endpoints for the fine-grained payment evidence stream (debit ->
gateway -> merchant confirmation -> settlement). The transaction state
machine and the existing transaction endpoints are untouched.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from api.core.security import AuthContext, require_roles
from api.db.database import get_db
from api.schemas.payment_events import (
    PaymentEventBatchRequest,
    PaymentEventBatchResponse,
    PaymentEventOut,
)
from api.services.payment_event_service import ingest_events

router = APIRouter(prefix="/api/v1/transactions", tags=["payment-events"])

logger = logging.getLogger("payment_recovery.payment_events")


@router.post(
    "/{transaction_id}/payment-events",
    response_model=PaymentEventBatchResponse,
    status_code=status.HTTP_200_OK,
    responses={
        404: {"description": "Transaction not found"},
        409: {"description": "EVENT_CONFLICT: provider_event_id already "
                           "exists with different content"},
    },
)
def ingest_payment_events(
    transaction_id: str,
    payload: PaymentEventBatchRequest,
    db: Session = Depends(get_db),
    # payment-domain write: mirrors ingestion rights (no SUPPORT/CUSTOMER)
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN")),
):
    """Ingest a batch of provider-observed payment-domain events. Idempotent
    per provider_event_id — provider redeliveries count as duplicates and are
    never re-inserted. A redelivered id whose payload content differs from the
    stored row is rejected with 409 EVENT_CONFLICT."""
    result = ingest_events(db, transaction_id, payload.events)
    # counts only — never log payloads
    logger.info(
        "payment-events endpoint: tx=%s created=%d duplicates=%d",
        transaction_id, result["created"], result["duplicates"],
    )
    return PaymentEventBatchResponse(
        transaction_id=result["transaction_id"],
        created=result["created"],
        duplicates=result["duplicates"],
        events=[PaymentEventOut.model_validate(e) for e in result["events"]],
    )

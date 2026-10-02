"""
api/routes/transactions.py — transaction ingestion & Digital Twin reads.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.core.exceptions import ForbiddenError, NotFoundError
from api.core.security import AuthContext, can_access_transaction, require_roles
from api.db.database import get_db
from api.schemas.transaction import (
    TimelineEvent,
    TimelineResponse,
    TransactionEventRequest,
    TransactionEventResponse,
    TransactionResponse,
)
from api.services.digital_twin import get_timeline
from api.services.ml_service import get_ml_service
from api.services.transaction_service import get_transaction, record_event

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])

# The ingestion endpoint is singular by API contract: POST /api/v1/transaction/event
ingest_router = APIRouter(prefix="/api/v1/transaction", tags=["transactions"])


@ingest_router.post(
    "/event",
    response_model=TransactionEventResponse,
    status_code=status.HTTP_200_OK,
    responses={400: {"description": "Invalid state transition"}, 503: {"description": "ML models unavailable"}},
)
def ingest_transaction_event(
    payload: TransactionEventRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN")),
    ml=Depends(get_ml_service),
):
    """Receive a payment transaction event, advance the transaction through the
    state machine, run the ML risk assessment when the outcome is
    FAILED/STALLED, and persist the full Digital Twin timeline atomically."""
    from api.core.config import get_settings

    result = record_event(db, payload, ml, get_settings())

    return TransactionEventResponse(
        transaction_id=result.transaction.transaction_id,
        current_state=result.transaction.current_state,
        created=result.created,
        state_changed=result.state_changed,
        ml_assessment=result.ml_assessment,
        new_events=[
            {
                "event_type": e.event_type,
                "previous_state": e.previous_state,
                "new_state": e.new_state,
            }
            for e in result.events
        ],
    )


@router.get(
    "/{transaction_id}",
    response_model=TransactionResponse,
    responses={404: {"description": "Transaction not found"}},
)
def get_transaction_by_id(
    transaction_id: str,
    db: Session = Depends(get_db),
    # Stage 9: CUSTOMER opened with OWNERSHIP scoping — a customer key may
    # read only transactions whose user_id matches its bound identity.
    # Unowned AND unknown ids both return 403 (non-enumerating).
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT", "CUSTOMER")),
):
    tx = get_transaction(db, transaction_id)
    if auth.role == "CUSTOMER":
        if not can_access_transaction(auth, tx):
            raise ForbiddenError("transaction not accessible")
    elif tx is None:
        raise HTTPException(status_code=404, detail=f"transaction {transaction_id} not found")
    return tx


@router.get(
    "/{transaction_id}/timeline",
    response_model=TimelineResponse,
    responses={404: {"description": "Transaction not found"}},
)
def get_transaction_timeline(
    transaction_id: str,
    db: Session = Depends(get_db),
    # Stage 9: CUSTOMER opened with OWNERSHIP scoping (same contract as the
    # single-transaction read: unowned/unknown → non-enumerating 403).
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT", "CUSTOMER")),
):
    tx = get_transaction(db, transaction_id)
    if auth.role == "CUSTOMER":
        if not can_access_transaction(auth, tx):
            raise ForbiddenError("transaction not accessible")
    elif tx is None:
        raise NotFoundError(f"transaction {transaction_id} not found")
    events = get_timeline(db, transaction_id)
    return TimelineResponse(
        transaction_id=transaction_id,
        current_state=tx.current_state,
        event_count=len(events),
        events=[TimelineEvent.model_validate(e) for e in events],
    )

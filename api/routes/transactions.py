"""
api/routes/transactions.py — transaction ingestion, listing & Digital Twin reads.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from api.core.exceptions import ForbiddenError, NotFoundError
from api.core.security import AuthContext, can_access_transaction, require_roles
from api.core.state_machine import ALL_STATES, TransactionState
from api.db.database import get_db
from api.db.models import Merchant, Transaction
from api.schemas.transaction import (
    TimelineEvent,
    TimelineResponse,
    TransactionEventRequest,
    TransactionEventResponse,
    TransactionListResponse,
    TransactionResponse,
    TransactionsSummaryResponse,
)
from api.services.digital_twin import get_timeline
from api.services.ml_service import get_ml_service
from api.services.transaction_service import get_transaction, record_event

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])

# The ingestion endpoint is singular by API contract: POST /api/v1/transaction/event
ingest_router = APIRouter(prefix="/api/v1/transaction", tags=["transactions"])


def _scoped_transactions(db: Session, auth: AuthContext, user_id: str | None):
    """Transaction query scoped by role — CUSTOMER callers are ALWAYS scoped to
    their own user_id server-side (any ?user_id= they pass is ignored); staff
    see everything and may filter by ?user_id=."""
    query = db.query(Transaction)
    if auth.role == "CUSTOMER":
        query = query.filter(Transaction.user_id == (auth.customer_id or ""))
    elif user_id:
        query = query.filter(Transaction.user_id == user_id)
    return query


def _txn_response(tx: Transaction, merchants: dict[str, Merchant]) -> TransactionResponse:
    """Response with merchant-catalog labels resolved (name/category instead of
    only the raw MER- id). Missing catalog row -> nulls."""
    resp = TransactionResponse.model_validate(tx)
    merchant = merchants.get(tx.merchant_id)
    if merchant is not None:
        resp.merchant_name = merchant.name
        resp.merchant_category = merchant.category
    return resp


def _merchant_map(db: Session, txs: list[Transaction]) -> dict[str, Merchant]:
    merchant_ids = {t.merchant_id for t in txs if t.merchant_id}
    if not merchant_ids:
        return {}
    rows = db.query(Merchant).filter(Merchant.merchant_id.in_(merchant_ids)).all()
    return {m.merchant_id: m for m in rows}


@router.get("", response_model=TransactionListResponse)
def list_transactions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user_id: str | None = Query(default=None, max_length=64),
    state: str | None = Query(default=None, max_length=32),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200, description="Search query (transaction ID, user ID, merchant name)"),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT", "CUSTOMER")),
):
    """List transactions, newest first, with optional state / date-range /
    text-search filters (same scoping rules as the summary endpoint)."""
    query = _scoped_transactions(db, auth, user_id)
    if state is not None:
        if state not in ALL_STATES:
            # ALL_STATES is built from vars(TransactionState), which drags in
            # dunder values; list only the real state attributes in the error.
            valid = sorted(
                value
                for key, value in vars(TransactionState).items()
                if not key.startswith("__") and isinstance(value, str)
            )
            raise HTTPException(
                status_code=422,
                detail=f"unknown state {state!r}; valid states: {', '.join(valid)}",
            )
        query = query.filter(Transaction.current_state == state)
    if date_from is not None:
        query = query.filter(Transaction.timestamp >= date_from)
    if date_to is not None:
        query = query.filter(Transaction.timestamp <= date_to)
    if q:
        # Server-side text search across transaction ID, user ID, merchant ID,
        # and merchant name (via LEFT JOIN). Case-insensitive (ilike for
        # PostgreSQL, like with lower() fallback for SQLite).
        search_pattern = f"%{q}%"
        query = query.outerjoin(Merchant, Transaction.merchant_id == Merchant.merchant_id)
        query = query.filter(
            or_(
                Transaction.id.ilike(search_pattern),
                Transaction.user_id.ilike(search_pattern),
                Transaction.merchant_id.ilike(search_pattern),
                Merchant.name.ilike(search_pattern),
            )
        )
    total = query.count()
    items = (
        query.order_by(Transaction.timestamp.desc(), Transaction.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    merchants = _merchant_map(db, items)
    return TransactionListResponse(
        items=[_txn_response(t, merchants) for t in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/summary", response_model=TransactionsSummaryResponse)
def list_transactions_summary(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT", "CUSTOMER")),
):
    """Aggregate totals over the SAME scope as the list endpoint: CUSTOMER
    callers get aggregates for their own transactions only; staff get
    platform-wide aggregates. Powers the customer dashboard's own-data
    statistics without exposing staff-only /stats/summary detail."""
    query = _scoped_transactions(db, auth, None)
    state_rows = (
        query.with_entities(Transaction.current_state, func.count(Transaction.id))
        .group_by(Transaction.current_state)
        .all()
    )
    currency_rows = (
        query.with_entities(Transaction.currency, func.sum(Transaction.amount))
        .group_by(Transaction.currency)
        .all()
    )
    return TransactionsSummaryResponse(
        total=sum(count for _, count in state_rows),
        by_state={state: count for state, count in state_rows},
        # Decimal -> str keeps the exact stored amount (no float drift).
        amounts_by_currency={currency: str(total) for currency, total in currency_rows},
    )


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
    return _txn_response(tx, _merchant_map(db, [tx]))


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

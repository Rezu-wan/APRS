"""
api/routes/autonomous_recovery.py — Stage 8 autonomous-recovery endpoints.

POST /{id}/recovery/process — run the full autonomous pipeline (assessment ->
policy -> safety gate -> sandbox release -> verification) and commit ONCE.
SYSTEM/ADMIN only: execution moves (simulated) money, so it must not be
triggerable by support staff or customers.

POST /{id}/recovery/evaluate — policy + safety only, no writes. Also open to
SUPPORT: answering "why wasn't this auto-recovered?" is a read-shaped
question and matches the existing auth architecture (SUPPORT reads
assessments and stats but never creates decisions or executions).

GET /{id}/recovery — latest recovery action row, read-only evidence;
SYSTEM/ADMIN/SUPPORT.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.core.exceptions import NotFoundError
from api.core.security import AuthContext, require_roles
from api.db.database import get_db
from api.db.models import RecoveryActionRecord
from api.schemas.transaction import UtcDatetime
from api.services.ml_service import get_ml_service
from api.services.autonomous_recovery import (
    evaluate_transaction,
    process_transaction,
)
from api.services.payment_provider import get_payment_provider

logger = logging.getLogger("payment_recovery.autonomous_recovery_api")

router = APIRouter(prefix="/api/v1/transactions", tags=["autonomous-recovery"])


class ProcessRequest(BaseModel):
    customer_reported_failure: bool = False


class ProcessResponse(BaseModel):
    transaction_id: str
    decision: str
    action: str
    status: str
    recovery_id: str
    provider_reference: str | None
    reason: str
    simulated: bool
    assessment: dict
    digital_twin_recorded: bool


class EvaluateResponse(BaseModel):
    transaction_id: str
    eligible: bool
    action: str
    decision_reason: str
    blocked_reason: str | None
    policy_version: str
    simulated: bool
    assessment: dict


class RecoveryActionResponse(BaseModel):
    transaction_id: str
    action: str
    status: str
    recovery_id: str
    decision_reason: str
    blocked_reason: str | None
    failure_reason: str | None
    requested_amount: float
    released_amount: float | None
    currency: str
    provider: str | None
    provider_reference: str | None
    policy_version: str
    created_at: UtcDatetime
    verified_at: UtcDatetime | None
    simulated: bool


@router.post(
    "/{transaction_id}/recovery/process",
    response_model=ProcessResponse,
    responses={404: {"description": "Transaction not found"}},
)
def process_recovery(
    transaction_id: str,
    request: ProcessRequest | None = None,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN")),
    ml=Depends(get_ml_service),
):
    """Run the full autonomous-recovery pipeline for one transaction and
    commit ONCE. Idempotent: identical evidence replays the stored recovery
    action without calling the provider again."""
    body = request or ProcessRequest()
    result = process_transaction(
        db,
        ml,
        get_payment_provider(),
        transaction_id,
        customer_reported_failure=body.customer_reported_failure,
    )
    db.commit()
    return result


@router.post(
    "/{transaction_id}/recovery/evaluate",
    response_model=EvaluateResponse,
    responses={404: {"description": "Transaction not found"}},
)
def evaluate_recovery(
    transaction_id: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT")),
    ml=Depends(get_ml_service),
):
    """Policy + safety evaluation ONLY: no recovery row, no provider call,
    no Digital Twin event. Read-shaped, hence SUPPORT-visible."""
    return evaluate_transaction(db, ml, transaction_id)


@router.get(
    "/{transaction_id}/recovery",
    response_model=RecoveryActionResponse,
    responses={404: {"description": "Transaction or recovery action not found"}},
)
def get_recovery_action(
    transaction_id: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT")),
):
    """Latest recovery action record for the transaction (read-only)."""
    row = db.scalars(
        select(RecoveryActionRecord)
        .where(RecoveryActionRecord.transaction_id == transaction_id)
        .order_by(
            RecoveryActionRecord.created_at.desc(), RecoveryActionRecord.id.desc()
        )
        .limit(1)
    ).one_or_none()
    if row is None:
        raise NotFoundError("no recovery action for this transaction")

    return RecoveryActionResponse(
        transaction_id=row.transaction_id,
        action=row.action,
        status=row.status,
        recovery_id=row.recovery_id,
        decision_reason=row.decision_reason,
        blocked_reason=row.blocked_reason,
        failure_reason=row.failure_reason,
        requested_amount=float(row.requested_amount),
        released_amount=(
            float(row.released_amount) if row.released_amount is not None else None
        ),
        currency=row.currency,
        provider=row.provider,
        provider_reference=row.provider_reference,
        policy_version=row.policy_version,
        created_at=row.created_at,
        verified_at=row.verified_at,
        simulated=True,
    )

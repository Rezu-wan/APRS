"""
api/routes/recovery.py — idempotent recovery operations.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.core.config import get_settings
from api.core.security import AuthContext, require_roles
from api.db.database import get_db
from api.schemas.recovery import RecoveryDecisionResponse, RecoveryRequest
from api.services.ml_service import get_ml_service
from api.services.recovery_service import request_release

router = APIRouter(prefix="/api/v1/recovery", tags=["recovery"])


@router.post(
    "/release-limit",
    response_model=RecoveryDecisionResponse,
    responses={
        404: {"description": "Transaction not found"},
        409: {"description": "Transaction not in a recoverable state"},
    },
)
def release_limit(
    payload: RecoveryRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN")),
    ml=Depends(get_ml_service),
):
    """Run (or replay) the recovery decision for a transaction.

    The decision comes from the recovery POLICY applied to the ML assessment —
    never from the caller, and never from GenAI. Calling this endpoint on an
    already-decided transaction replays the stored decision without side
    effects (idempotency)."""
    result = request_release(
        db, ml, get_settings(), payload.transaction_id, auth.role
    )
    return RecoveryDecisionResponse(**result)

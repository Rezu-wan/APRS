"""
api/routes/temporal.py — Stage 11 Phase 11B: temporal state-at endpoint.

GET /api/v1/transactions/{transaction_id}/state-at?timestamp=<ISO-8601>

Returns the historical state of one transaction as of the given instant
(see api/services/temporal.py). Read-only besides one best-effort
security-audit row (TEMPORAL_QUERY) — never mutates transaction state and
never influences the recovery policy or safety gate.

Authorization: SYSTEM/ADMIN/SUPPORT. CUSTOMER is excluded because temporal
evidence is an internal analysis surface (root cause, confidence, excluded
future evidence counts).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.core.exceptions import AppError, NotFoundError
from api.core.security import AuthContext, require_roles
from api.db.database import get_db
from api.services.audit import AUDIT_TEMPORAL_QUERY, record_security_event
from api.services.temporal import TemporalStateReport, state_at
from api.services.transaction_service import get_transaction

router = APIRouter(prefix="/api/v1/transactions", tags=["temporal"])


class _ValidationError(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"


def _parse_timestamp(raw: str) -> datetime:
    """Parse the query instant. Trailing 'Z' is accepted; naive input is
    assumed UTC. Garbage input is a 422 VALIDATION_ERROR, never a 500."""
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise _ValidationError(
            f"timestamp must be ISO-8601, got {raw!r}"
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@router.get(
    "/{transaction_id}/state-at",
    response_model=TemporalStateReport,
    responses={404: {"description": "Transaction not found"}},
)
def get_state_at(
    transaction_id: str,
    timestamp: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT")),
):
    """Historical state of one transaction as of ``timestamp`` (Phase 11B).
    ONLY evidence with event_timestamp <= timestamp is used — future events
    are counted as uncertainty and never influence the reconstruction."""
    transaction = get_transaction(db, transaction_id)
    if transaction is None:
        raise NotFoundError(f"transaction {transaction_id} not found")

    at = _parse_timestamp(timestamp)
    report: TemporalStateReport = state_at(db, transaction, at)

    record_security_event(
        actor_type=auth.role,
        actor_id=auth.key_name,
        action=AUDIT_TEMPORAL_QUERY,
        resource_type="transaction",
        resource_id=transaction_id,
        audit_metadata={
            "as_of": report.as_of,
            "observed": report.observed_event_count,
            "excluded": report.excluded_event_count,
        },
        db=db,
    )

    return report

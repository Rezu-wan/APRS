"""
api/routes/stats.py — honest aggregate counts for the operational dashboard.

Pure reads: no state changes, no Digital Twin events, no ML calls. Every
number is computed from the database at request time — nothing is cached
and nothing is fabricated.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.core.security import require_roles
from api.db.database import get_db
from api.db.models import RecoveryDecision, Transaction

logger = logging.getLogger("payment_recovery.stats")

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])


class StatsSummary(BaseModel):
    total: int
    by_state: dict[str, int]
    decisions: dict[str, int]


@router.get("/summary", response_model=StatsSummary)
def stats_summary(
    db: Session = Depends(get_db),
    auth=Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT")),
) -> StatsSummary:
    """Honest aggregate counts for the operational dashboard. Computed from
    the database at request time — nothing cached, nothing fabricated."""
    state_rows = db.execute(
        select(Transaction.current_state, func.count()).group_by(
            Transaction.current_state
        )
    ).all()
    by_state = {state: count for state, count in state_rows}

    decision_rows = db.execute(
        select(RecoveryDecision.decision, func.count()).group_by(
            RecoveryDecision.decision
        )
    ).all()
    observed_decisions = {decision: count for decision, count in decision_rows}
    decisions = {
        decision: observed_decisions.get(decision, 0)
        for decision in ("LIMIT_RELEASED", "MANUAL_REVIEW", "RECOVERY_REJECTED")
    }

    total = sum(by_state.values())
    logger.debug(
        "stats summary: total=%d states=%d decision_kinds=%d",
        total, len(by_state), len(observed_decisions),
    )
    return StatsSummary(total=total, by_state=by_state, decisions=decisions)

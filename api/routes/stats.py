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
from api.db.models import (
    RecoveryActionRecord,
    RecoveryDecision,
    RiskAssessmentRecord,
    Transaction,
)

logger = logging.getLogger("payment_recovery.stats")

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])


class RiskAssessmentStats(BaseModel):
    total: int
    by_anomaly_type: dict[str, int]
    by_risk_level: dict[str, int]
    recovery_candidates: int


class AutonomousRecoveryStats(BaseModel):
    """Stage 8 recovery_actions counts: attempts = all rows (every persisted
    attempt, including BLOCKED ones); completed = COMPLETED + VERIFIED;
    blocked = BLOCKED; failed = FAILED; verified = VERIFIED (subset of
    completed)."""

    attempts: int
    completed: int
    blocked: int
    failed: int
    verified: int


class StatsSummary(BaseModel):
    total: int
    by_state: dict[str, int]
    decisions: dict[str, int]
    risk_assessments: RiskAssessmentStats
    autonomous_recovery: AutonomousRecoveryStats


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

    anomaly_rows = db.execute(
        select(RiskAssessmentRecord.anomaly_type, func.count()).group_by(
            RiskAssessmentRecord.anomaly_type
        )
    ).all()
    by_anomaly_type = {anomaly: count for anomaly, count in anomaly_rows}
    risk_level_rows = db.execute(
        select(RiskAssessmentRecord.risk_level, func.count()).group_by(
            RiskAssessmentRecord.risk_level
        )
    ).all()
    by_risk_level = {level: count for level, count in risk_level_rows}
    recovery_candidates = db.scalar(
        select(func.count()).where(RiskAssessmentRecord.recovery_candidate.is_(True))
    ) or 0

    status_rows = db.execute(
        select(RecoveryActionRecord.status, func.count()).group_by(
            RecoveryActionRecord.status
        )
    ).all()
    recovery_by_status = {status: count for status, count in status_rows}
    autonomous_recovery = AutonomousRecoveryStats(
        attempts=sum(recovery_by_status.values()),
        completed=(
            recovery_by_status.get("COMPLETED", 0)
            + recovery_by_status.get("VERIFIED", 0)
        ),
        blocked=recovery_by_status.get("BLOCKED", 0),
        failed=recovery_by_status.get("FAILED", 0),
        verified=recovery_by_status.get("VERIFIED", 0),
    )

    logger.debug(
        "stats summary: total=%d states=%d decision_kinds=%d assessments=%d",
        total, len(by_state), len(observed_decisions), sum(by_anomaly_type.values()),
    )
    return StatsSummary(
        total=total,
        by_state=by_state,
        decisions=decisions,
        risk_assessments=RiskAssessmentStats(
            total=sum(by_anomaly_type.values()),
            by_anomaly_type=by_anomaly_type,
            by_risk_level=by_risk_level,
            recovery_candidates=recovery_candidates,
        ),
        autonomous_recovery=autonomous_recovery,
    )

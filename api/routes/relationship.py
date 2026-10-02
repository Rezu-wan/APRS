"""
api/routes/relationship.py — Stage 11 Phase 11D relationship-analysis endpoint.

GET /api/v1/transactions/{transaction_id}/relationships returns the advisory,
explainable relationship graph (see api/services/relationship.py). Read-only
besides one best-effort security-audit row (GRAPH_ANALYSIS) — the endpoint
never mutates transaction state and never influences the recovery policy or
safety gate.

Authorization: SYSTEM/ADMIN/SUPPORT. CUSTOMER is excluded because the report
carries internal structural analysis (cross-user reference reuse, burst
signals) that a customer-facing view would expose for gaming.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.core.exceptions import NotFoundError
from api.core.security import AuthContext, require_roles
from api.db.database import get_db
from api.services.audit import AUDIT_GRAPH_ANALYSIS, record_security_event
from api.services.relationship import (
    RelationshipReport,
    compute_relationships,
)
from api.services.transaction_service import get_transaction

logger = logging.getLogger("payment_recovery.relationship")

router = APIRouter(prefix="/api/v1/transactions", tags=["relationships"])


class RelationshipSignalOut(BaseModel):
    code: str
    label: str
    description: str
    level: str
    count: int | None
    evidence: list[str]


class RelationshipReportOut(BaseModel):
    transaction_id: str
    entities: list[dict[str, str]]
    edges: list[dict[str, str]]
    signals: list[RelationshipSignalOut]
    computed_at: str
    feature_version: str


class RelationshipResponse(BaseModel):
    report: RelationshipReportOut


@router.get(
    "/{transaction_id}/relationships",
    response_model=RelationshipResponse,
    responses={404: {"description": "Transaction not found"}},
)
def get_relationships(
    transaction_id: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT")),
):
    """Advisory relationship graph for one transaction (Stage 11, Phase 11D).
    Explanable structural signals only — never an action, never part of the
    decision path."""
    transaction = get_transaction(db, transaction_id)
    if transaction is None:
        raise NotFoundError(f"transaction {transaction_id} not found")

    report: RelationshipReport = compute_relationships(db, transaction)

    record_security_event(
        actor_type=auth.role,
        actor_id=auth.key_name,
        action=AUDIT_GRAPH_ANALYSIS,
        resource_type="transaction",
        resource_id=transaction_id,
        audit_metadata={
            "feature_version": report.feature_version,
            "signal_count": len(report.signals),
        },
        db=db,
    )

    return RelationshipResponse(
        report=RelationshipReportOut(
            transaction_id=report.transaction_id,
            entities=report.entities,
            edges=report.edges,
            signals=[
                RelationshipSignalOut(
                    code=s.code, label=s.label, description=s.description,
                    level=s.level, count=s.count, evidence=s.evidence,
                )
                for s in report.signals
            ],
            computed_at=report.computed_at,
            feature_version=report.feature_version,
        )
    )

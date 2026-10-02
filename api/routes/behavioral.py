"""
api/routes/behavioral.py — Stage 11 Phase C: behavioral-signal API.

GET /api/v1/transactions/{transaction_id}/behavioral-signals

STAFF-ONLY (SYSTEM/ADMIN/SUPPORT) read-only endpoint exposing the explainable
statistical behavioral-signal report (api/services/behavioral.py). CUSTOMER
keys are refused by require_roles — these are internal risk-research signals
(spec §22: never exposed to customers). The endpoint writes NOTHING except a
best-effort security-audit row (action MODEL_SIGNAL); it does not influence
the recovery policy or the safety gate — the signals are advisory data for
the UI/research.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.core.exceptions import NotFoundError
from api.core.security import AuthContext, require_roles
from api.db.database import get_db
from api.db.models import Transaction
from api.services.audit import AUDIT_MODEL_SIGNAL, record_security_event
from api.services.behavioral import BehavioralReport, compute_behavioral_signals

logger = logging.getLogger("payment_recovery.behavioral_api")

router = APIRouter(prefix="/api/v1/transactions", tags=["behavioral"])


@router.get("/{transaction_id}/behavioral-signals", response_model=BehavioralReport)
def get_behavioral_signals(
    transaction_id: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT")),
):
    """Explainable statistical behavioral signals for one transaction.

    Read-only analysis of stored transactions/payment_events. Signals with
    insufficient history are honestly UNKNOWN — nothing is fabricated. Never
    exposed to CUSTOMER roles."""
    tx = (
        db.query(Transaction)
        .filter(Transaction.transaction_id == transaction_id)
        .one_or_none()
    )
    if tx is None:
        raise NotFoundError(f"transaction {transaction_id} not found")

    report = compute_behavioral_signals(db, tx)

    record_security_event(
        actor_type=auth.role,
        actor_id=auth.key_name,
        action=AUDIT_MODEL_SIGNAL,
        resource_type="transaction",
        resource_id=transaction_id,
        result="ALLOWED",
        reason="behavioral signal report accessed (staff, read-only)",
        audit_metadata={"feature_version": report.feature_version},
        db=db,
    )
    return report

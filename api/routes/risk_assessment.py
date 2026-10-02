"""
api/routes/risk_assessment.py — Stage 7 hybrid risk assessment endpoints.

POST runs the hybrid engine (deterministic rules ALWAYS own the anomaly_type;
ML is a supporting signal only — see api/services/risk_engine.py for the
pinned precedence contract), persists the assessment EVIDENCE and appends one
idempotent ANOMALY_CLASSIFIED observation to the Digital Twin, in ONE commit.

GET returns the latest stored assessment as read-only evidence.

Authorization / IDOR rationale: the assessment carries internal decision
evidence (triggered rules, ML scores, block reasons) used for recovery
routing. CUSTOMER is excluded from BOTH endpoints because a customer-facing
view of anti-fraud evidence (e.g. FALSE_COMPLAINT classifications) would leak
risk-model internals and enable gaming the rules; SUPPORT may READ an
assessment to answer customer inquiries but may not CREATE one.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.core.exceptions import NotFoundError
from api.core.security import AuthContext, require_roles
from api.db.database import get_db
from api.schemas.risk_assessment import RiskAssessment
from api.services.ml_service import get_ml_service
from api.services.metrics import (
    METRICS_RISK_LATENCY,
    record_latency,
)
from api.services.risk_engine import (
    assessment_from_record,
    get_latest_record,
    persist_assessment,
    record_anomaly_classified,
    run_assessment,
)
from api.services.transaction_service import get_transaction

logger = logging.getLogger("payment_recovery.risk_assessment")

router = APIRouter(prefix="/api/v1/transactions", tags=["risk-assessment"])


class RiskAssessmentRequest(BaseModel):
    customer_reported_failure: bool = False


class RiskAssessmentResponse(BaseModel):
    assessment: RiskAssessment
    reused: bool
    digital_twin_event_recorded: bool


@router.post(
    "/{transaction_id}/risk-assessment",
    response_model=RiskAssessmentResponse,
    responses={404: {"description": "Transaction not found"}},
)
def create_risk_assessment(
    transaction_id: str,
    request: RiskAssessmentRequest | None = None,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN")),
):
    """Run the hybrid rules+ML risk assessment and store the EVIDENCE (never
    an action) plus one idempotent Digital Twin observation. Idempotent: the
    same input evidence reuses the stored assessment."""
    body = request or RiskAssessmentRequest()
    started = time.perf_counter()

    tx = get_transaction(db, transaction_id)
    if tx is None:
        raise NotFoundError(f"transaction {transaction_id} not found")

    logger.info(
        "risk assessment started: tx=%s customer_reported_failure=%s",
        transaction_id, body.customer_reported_failure,
    )

    assessment, fingerprint, reused = run_assessment(
        db,
        tx,
        get_ml_service(),
        customer_reported_failure=body.customer_reported_failure,
    )

    twin_recorded = False
    if not reused:
        persist_assessment(db, tx, assessment, fingerprint)
        twin_recorded = record_anomaly_classified(db, tx, assessment, fingerprint)
        db.commit()

    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    record_latency(METRICS_RISK_LATENCY, latency_ms)  # Stage 11G (HTTP-facing latency; the counter is service-side)
    logger.info(
        "risk assessment completed: tx=%s anomaly_type=%s risk_level=%s "
        "reused=%s latency_ms=%.1f",
        transaction_id, assessment.anomaly_type, assessment.risk_level,
        reused, latency_ms,
    )
    return RiskAssessmentResponse(
        assessment=assessment,
        reused=reused,
        digital_twin_event_recorded=twin_recorded,
    )


@router.get(
    "/{transaction_id}/risk-assessment",
    response_model=RiskAssessmentResponse,
    responses={404: {"description": "Transaction or assessment not found"}},
)
def get_risk_assessment(
    transaction_id: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT")),
):
    """Latest stored risk assessment (read-only evidence; reuse semantics —
    nothing is recomputed and no twin event is appended)."""
    tx = get_transaction(db, transaction_id)
    if tx is None:
        raise NotFoundError(f"transaction {transaction_id} not found")

    record = get_latest_record(db, transaction_id)
    if record is None:
        raise NotFoundError("no risk assessment for this transaction")

    return RiskAssessmentResponse(
        assessment=assessment_from_record(record),
        reused=True,
        digital_twin_event_recorded=False,
    )

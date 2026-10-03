"""api/routes/customer_reports.py — customer problem reports.

POST /api/v1/transactions/{id}/customer-report  — file (or replay) a report
GET  /api/v1/transactions/{id}/customer-report  — read the report

A report is EVIDENCE ONLY. It never changes transaction state, never calls
the provider, and never decides anything — the twin gets one observation
and the security audit gets one row. CUSTOMER may only act on their OWN
transactions (unowned and unknown ids both return the same non-enumerating
403, matching the Stage 9 contract).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.core.exceptions import ForbiddenError, NotFoundError
from api.core.security import AuthContext, can_access_transaction, require_roles
from api.db.database import get_db
from api.schemas.customer_report import (
    CustomerReportFileResponse,
    CustomerReportRequest,
    CustomerReportResponse,
)
from api.services.audit import AUDIT_CUSTOMER_REPORT, record_security_event
from api.services.customer_reports import file_report, get_report_for
from api.services.transaction_service import get_transaction

router = APIRouter(prefix="/api/v1/transactions", tags=["customer-reports"])


def _resolve_reporter_customer_id(auth: AuthContext) -> str:
    """CUSTOMER files under its bound identity; staff file on behalf of the
    customer over an explicit channel, recorded under a staff marker."""
    if auth.role == "CUSTOMER":
        return auth.customer_id or ""
    return f"staff/{auth.key_name}"


@router.post(
    "/{transaction_id}/customer-report",
    response_model=CustomerReportFileResponse,
    responses={
        403: {"description": "Transaction not accessible"},
        404: {"description": "Transaction not found (staff only)"},
    },
)
def file_customer_report(
    transaction_id: str,
    payload: CustomerReportRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "CUSTOMER")),
):
    tx = get_transaction(db, transaction_id)
    if auth.role == "CUSTOMER":
        if not can_access_transaction(auth, tx):
            raise ForbiddenError("transaction not accessible")
    elif tx is None:
        raise NotFoundError(f"transaction {transaction_id} not found")

    reporter = _resolve_reporter_customer_id(auth)
    report, already_reported, twin_recorded = file_report(
        db,
        tx,
        reporter,
        problem_type=payload.problem_type,
        stage=payload.stage,
        description=payload.description,
    )

    record_security_event(
        actor_type=auth.role,
        actor_id=auth.key_name,
        action=AUDIT_CUSTOMER_REPORT,
        resource_type="transaction",
        resource_id=transaction_id,
        result="ALLOWED",
        reason=None if not already_reported else "replayed existing report",
        audit_metadata={
            "report_id": report.report_id,
            "problem_type": report.problem_type,
            "stage": report.stage,
            "already_reported": already_reported,
        },
        db=db,
    )
    db.commit()
    db.refresh(report)

    return CustomerReportFileResponse(
        report=CustomerReportResponse.model_validate(report),
        already_reported=already_reported,
        digital_twin_event_recorded=twin_recorded,
    )


@router.get(
    "/{transaction_id}/customer-report",
    response_model=CustomerReportResponse,
    responses={403: {"description": "Transaction not accessible"}, 404: {"description": "No report"}},
)
def get_customer_report(
    transaction_id: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT", "CUSTOMER")),
):
    tx = get_transaction(db, transaction_id)
    if auth.role == "CUSTOMER":
        if not can_access_transaction(auth, tx):
            raise ForbiddenError("transaction not accessible")
    elif tx is None:
        raise NotFoundError(f"transaction {transaction_id} not found")

    if auth.role == "CUSTOMER":
        # a customer sees only their own report
        report = get_report_for(db, transaction_id, auth.customer_id or "")
    else:
        # staff see the latest report filed on the transaction
        report = _any_report(db, transaction_id)
    if report is None:
        raise NotFoundError(f"no customer report for transaction {transaction_id}")
    return CustomerReportResponse.model_validate(report)


def _any_report(db: Session, transaction_id: str):
    from api.db.models import CustomerReport

    return (
        db.query(CustomerReport)
        .filter(CustomerReport.transaction_id == transaction_id)
        .order_by(CustomerReport.created_at.desc())
        .first()
    )

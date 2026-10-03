"""api/services/customer_reports.py — customer problem reports.

EVIDENCE ONLY, like every other service in this codebase that is not the
policy: filing a report appends one observation to the Digital Twin and
stores the report row. It never advances the state machine, never calls the
provider, and never influences a recovery decision by itself.

Idempotency: UNIQUE(transaction_id, customer_id) in the DB plus a
check-before-insert here — a repeat filing replays the stored report with
already_reported=True and appends NO second twin event.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from api.db.models import CustomerReport, Transaction
from api.services.digital_twin import append_event


def get_report_for(db: Session, transaction_id: str, customer_id: str) -> CustomerReport | None:
    """The customer's report for one transaction, or None."""
    return (
        db.query(CustomerReport)
        .filter(
            CustomerReport.transaction_id == transaction_id,
            CustomerReport.customer_id == customer_id,
        )
        .first()
    )


def file_report(
    db: Session,
    tx: Transaction,
    customer_id: str,
    *,
    problem_type: str,
    stage: str,
    description: str,
) -> tuple[CustomerReport, bool, bool]:
    """File (or replay) a problem report for `tx`.

    Returns (report, already_reported, twin_recorded). The caller owns the
    commit — no commit here, matching every other service.

    If this is a new report (not a replay), automatically creates a support
    case if one doesn't already exist for this transaction.
    """
    existing = get_report_for(db, tx.transaction_id, customer_id)
    if existing is not None:
        return existing, True, False

    report = CustomerReport(
        transaction_id=tx.transaction_id,
        customer_id=customer_id,
        problem_type=problem_type,
        stage=stage,
        description=description,
        status="OPEN",
    )
    db.add(report)
    db.flush()  # assign report_id / timestamps before we reference them

    # one observation on the append-only twin — previous == new (no
    # state transition; the state machine is never touched by a report)
    append_event(
        db,
        tx,
        new_state=tx.current_state,
        previous_state=tx.current_state,
        event_type="CUSTOMER_REPORT_FILED",
        reason=f"customer reported a problem ({problem_type} at {stage})",
        event_metadata={
            "report_id": report.report_id,
            "problem_type": problem_type,
            "stage": stage,
            "reported_by": customer_id,
        },
    )

    # Auto-create support case if none exists for this transaction
    _maybe_create_support_case(db, tx, customer_id, report)

    return report, False, True


def _maybe_create_support_case(
    db: Session,
    tx: Transaction,
    customer_id: str,
    report: CustomerReport,
) -> None:
    """Create a support case for a customer report if one doesn't exist.

    DEFENSIVE: any failure here is logged but never breaks report filing.
    """
    try:
        from api.db.models import SupportCase

        # Check if case already exists for this transaction
        existing_case = (
            db.query(SupportCase)
            .filter(SupportCase.transaction_id == tx.transaction_id)
            .first()
        )
        if existing_case is not None:
            return

        # Create new support case linked to the report
        case = SupportCase(
            customer_id=customer_id,
            transaction_id=tx.transaction_id,
            issue_type="customer_report",
            priority="medium",
            status="open",
            description=f"Customer reported {report.problem_type} at {report.stage} stage. {report.description[:200]}",
            metadata={
                "report_id": report.report_id,
                "auto_created": True,
                "problem_type": report.problem_type,
                "stage": report.stage,
            },
        )
        db.add(case)
        db.flush()
    except Exception as e:
        # Log error but never break report filing (defensive)
        import logging
        logger = logging.getLogger("payment_recovery.customer_reports")
        logger.warning(
            "failed to auto-create support case for report (non-fatal): tx=%s error=%s",
            tx.transaction_id, str(e),
        )


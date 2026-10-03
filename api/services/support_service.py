"""
api/services/support_service.py — customer-care workspace logic.

Reads: overview aggregates, customer search/profile, transaction list — all
derived from the SAME tables the rest of the app serves (no copy, no second
authority). Writes: the support_cases ticket queue, the ONLY state this
service owns.

Safety rules respected here:
  * this service never runs recovery, never assesses risk, never touches the
    sandbox — it routes human work, it does not make engine decisions;
  * every case write is audited (actor = API-key NAME, never the raw key);
  * every mutation publishes an EventEnvelope on the bus (best-effort — the
    bus can never break a case write, mirroring the Stage 11A contract);
  * missing data renders as null/empty upstream — no fabrication.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import case as sql_case
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from api.core.security import AuthContext
from api.db.models import (
    Account,
    Customer,
    CustomerBehaviorSignal,
    RecoveryActionRecord,
    RiskAssessmentRecord,
    SupportCase,
    Transaction,
)
from api.schemas.support import (
    CaseStatus,
    CustomerAggregate,
    CustomerProfileResponse,
    CustomerSearchResult,
    OpenCaseSummary,
    QueueCounts,
    SupportCaseCreate,
    SupportCaseUpdate,
)
from api.services.audit import (
    AUDIT_SUPPORT_CASE_CREATED,
    AUDIT_SUPPORT_CASE_UPDATED,
    AUDIT_SUPPORT_CUSTOMER_VIEWED,
    record_security_event,
)
from api.services.eventbus.base import EventEnvelope

logger = logging.getLogger("payment_recovery.support")

OPEN_CASE_STATUSES = (
    CaseStatus.OPEN,
    CaseStatus.IN_PROGRESS,
    CaseStatus.WAITING_FOR_CUSTOMER,
    CaseStatus.ESCALATED,
)

# Terminal state is CLOSED; RESOLVED may reopen. Everything else may move
# forward freely (an agent can skip IN_PROGRESS) — the map is the single
# authority for legal case transitions and is test-pinned.
CASE_STATUS_TRANSITIONS: dict[str, set[str]] = {
    CaseStatus.OPEN: {
        CaseStatus.IN_PROGRESS,
        CaseStatus.WAITING_FOR_CUSTOMER,
        CaseStatus.ESCALATED,
        CaseStatus.RESOLVED,
        CaseStatus.CLOSED,
    },
    CaseStatus.IN_PROGRESS: {
        CaseStatus.WAITING_FOR_CUSTOMER,
        CaseStatus.ESCALATED,
        CaseStatus.RESOLVED,
        CaseStatus.CLOSED,
    },
    CaseStatus.WAITING_FOR_CUSTOMER: {
        CaseStatus.IN_PROGRESS,
        CaseStatus.RESOLVED,
        CaseStatus.CLOSED,
    },
    CaseStatus.ESCALATED: {CaseStatus.IN_PROGRESS, CaseStatus.RESOLVED, CaseStatus.CLOSED},
    CaseStatus.RESOLVED: {CaseStatus.OPEN, CaseStatus.CLOSED},
    CaseStatus.CLOSED: set(),
}

_RECOVERY_ATTENTION_STATUSES = ("BLOCKED", "FAILED")
_RECOVERY_ATTENTION_ACTIONS = ("MANUAL_REVIEW",)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _publish(event_type: str, transaction_id: str, data: dict) -> None:
    """Best-effort bus publish — a dead bus must never break a case write."""
    try:
        from api.services.eventbus.in_memory import get_event_bus

        get_event_bus().publish(
            EventEnvelope(
                transaction_id=transaction_id,
                event_type=event_type,
                event_timestamp=_utcnow(),
                source="SYSTEM",
                payload=data,
            )
        )
    except Exception:  # noqa: BLE001 — notification must not break the operation
        logger.warning("support event publish failed: %s", event_type, exc_info=True)


# --------------------------------------------------------------------------------------
# Overview (the support queue)
# --------------------------------------------------------------------------------------


def get_overview(db: Session, *, attention_limit: int = 15, activity_limit: int = 10) -> dict:
    status_counts = dict(
        db.execute(
            select(SupportCase.status, func.count(SupportCase.case_id)).group_by(
                SupportCase.status
            )
        ).all()
    )
    counts = QueueCounts(
        open=status_counts.get(CaseStatus.OPEN, 0),
        in_progress=status_counts.get(CaseStatus.IN_PROGRESS, 0),
        waiting_for_customer=status_counts.get(CaseStatus.WAITING_FOR_CUSTOMER, 0),
        escalated=status_counts.get(CaseStatus.ESCALATED, 0),
        resolved=status_counts.get(CaseStatus.RESOLVED, 0),
        closed=status_counts.get(CaseStatus.CLOSED, 0),
    )

    return {
        "cases_by_status": counts,
        "needs_attention": _needs_attention(db, limit=attention_limit),
        "recent_activity": _recent_activity(db, limit=activity_limit),
        "generated_at": _utcnow(),
    }


def _latest_risk_subquery():
    """assessment_id of the newest risk assessment per transaction."""
    return (
        select(
            RiskAssessmentRecord.transaction_id,
            func.max(RiskAssessmentRecord.created_at).label("max_created"),
        )
        .group_by(RiskAssessmentRecord.transaction_id)
        .subquery()
    )


def _needs_attention(db: Session, *, limit: int) -> list[dict]:
    """Recoveries that ended BLOCKED/FAILED or were routed to MANUAL_REVIEW,
    newest first, enriched with the transaction's customer/amount and the
    latest risk verdict. This is real engine output — the work list support
    actually picks up."""
    latest_risk = _latest_risk_subquery()
    rows = db.execute(
        select(
            RecoveryActionRecord,
            Transaction,
            RiskAssessmentRecord.risk_level,
            RiskAssessmentRecord.anomaly_type,
            SupportCase.case_id,
        )
        .join(Transaction, Transaction.transaction_id == RecoveryActionRecord.transaction_id)
        .outerjoin(
            latest_risk,
            latest_risk.c.transaction_id == RecoveryActionRecord.transaction_id,
        )
        .outerjoin(
            RiskAssessmentRecord,
            (RiskAssessmentRecord.transaction_id == latest_risk.c.transaction_id)
            & (RiskAssessmentRecord.created_at == latest_risk.c.max_created),
        )
        .outerjoin(
            SupportCase,
            (SupportCase.transaction_id == RecoveryActionRecord.transaction_id)
            & SupportCase.status.in_([s.value for s in OPEN_CASE_STATUSES]),
        )
        .where(
            or_(
                RecoveryActionRecord.status.in_(_RECOVERY_ATTENTION_STATUSES),
                RecoveryActionRecord.action.in_(_RECOVERY_ATTENTION_ACTIONS),
            )
        )
        .order_by(RecoveryActionRecord.created_at.desc())
        .limit(limit)
    ).all()

    items: list[dict] = []
    for rec, tx, risk_level, anomaly_type, case_id in rows:
        items.append(
            {
                "transaction_id": tx.transaction_id,
                "customer_id": tx.user_id,
                "amount": float(tx.amount),
                "currency": tx.currency,
                "timestamp": tx.timestamp,
                "failure_reason": tx.failure_reason,
                "recovery_status": rec.status,
                "blocked_reason": rec.blocked_reason,
                "risk_level": risk_level,
                "anomaly_type": anomaly_type,
                "open_case_id": case_id,
            }
        )
    return items


def _recent_activity(db: Session, *, limit: int) -> list[dict]:
    rows = db.execute(
        select(Transaction, Customer.full_name)
        .outerjoin(Customer, Customer.customer_id == Transaction.user_id)
        .order_by(Transaction.timestamp.desc())
        .limit(limit)
    ).all()
    return [
        {
            "transaction_id": tx.transaction_id,
            "customer_id": tx.user_id,
            "customer_name": name,
            "amount": float(tx.amount),
            "currency": tx.currency,
            "timestamp": tx.timestamp,
            "current_state": tx.current_state,
            "failure_reason": tx.failure_reason,
        }
        for tx, name in rows
    ]


# --------------------------------------------------------------------------------------
# Customer search + profile
# --------------------------------------------------------------------------------------


def search_customers(db: Session, query: str, *, limit: int = 20) -> dict:
    """Registry matches (id/name/email/phone contains) UNION customers that
    exist only as a transactions.user_id. Exact-id matches float to the top.
    No fabrications: registry-less customers carry unknown fields."""
    q = query.strip()
    if not q:
        return {"query": q, "results": [], "total": 0}
    like = f"%{q}%"

    # an exact transaction id resolves to its customer (support agents often
    # start from a payment reference)
    tx_customer = db.scalar(
        select(Transaction.user_id).where(Transaction.transaction_id == q)
    )
    extra_ids: set[str] = set()
    if tx_customer:
        extra_ids.add(tx_customer)

    registry_rows = (
        db.execute(
            select(Customer).where(
                or_(
                    Customer.customer_id.ilike(like),
                    Customer.full_name.ilike(like),
                    Customer.email.ilike(like),
                    Customer.phone.ilike(like),
                )
            )
            .order_by(Customer.customer_id)
            .limit(limit)
        )
        .scalars()
        .all()

    )
    matched_ids = {c.customer_id for c in registry_rows}

    # a transaction-id lookup can also hit a registry customer not caught by
    # the LIKE match above
    if extra_ids - matched_ids:
        registry_extra = (
            db.execute(select(Customer).where(Customer.customer_id.in_(extra_ids)))
            .scalars()
            .all()
        )
        registry_rows = list(registry_rows) + [c for c in registry_extra if c.customer_id not in matched_ids]
        matched_ids |= {c.customer_id for c in registry_rows}

    # customers that exist only as a user_id on transactions (ingest-only DBs)
    id_only_rows = (
        db.execute(
            select(Transaction.user_id)
            .where(Transaction.user_id.ilike(like))
            .distinct()
            .limit(limit * 2)
        )
        .scalars()
        .all()
    )
    id_only = [uid for uid in id_only_rows if uid not in matched_ids][:limit]
    id_only += [uid for uid in extra_ids if uid not in matched_ids]

    ids = matched_ids | set(id_only) | extra_ids
    agg = _customer_aggregates(db, ids)
    open_cases = _open_case_counts(db, ids)

    results: list[CustomerSearchResult] = []
    for c in registry_rows:
        a = agg.get(c.customer_id)
        results.append(
            CustomerSearchResult(
                customer_id=c.customer_id,
                full_name=c.full_name,
                email=c.email,
                phone=c.phone,
                status=c.status,
                segment=c.segment,
                risk_profile=c.risk_profile,
                country=c.country,
                transaction_count=a.transaction_count if a else 0,
                failed_transaction_count=a.failed_count if a else 0,
                last_activity_at=a.last_activity_at if a else None,
                open_case_count=open_cases.get(c.customer_id, 0),
                dataset_known=True,
            )
        )
    for uid in id_only:
        a = agg.get(uid)
        results.append(
            CustomerSearchResult(
                customer_id=uid,
                full_name=uid,  # honest fallback: no registry row exists
                email="",
                phone=None,
                status="unknown",
                segment=None,
                risk_profile=None,
                country=None,
                transaction_count=a.transaction_count if a else 0,
                failed_transaction_count=a.failed_count if a else 0,
                last_activity_at=a.last_activity_at if a else None,
                open_case_count=open_cases.get(uid, 0),
                dataset_known=False,
            )
        )

    # exact-id / exact-email matches first, then registry rows, then id-only
    results.sort(
        key=lambda r: (
            0 if (q.lower() == r.customer_id.lower() or q.lower() == r.email.lower()) else 1,
            not r.dataset_known,
            r.customer_id,
        )
    )
    return {"query": q, "results": results, "total": len(results)}


def _customer_aggregates(db: Session, customer_ids: set[str]) -> dict[str, CustomerAggregate]:
    if not customer_ids:
        return {}
    rows = db.execute(
        select(
            Transaction.user_id,
            func.count(Transaction.transaction_id),
            # "failed" = hit a failure at least once (same semantics as the
            # profile aggregate — current_state == FAILED would undercount
            # because the ML chain advances failed payments onward)
            func.sum(
                sql_case(
                    (
                        or_(
                            Transaction.failure_reason.isnot(None),
                            Transaction.current_state == "FAILED",
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            func.max(Transaction.timestamp),
        )
        .where(Transaction.user_id.in_(customer_ids))
        .group_by(Transaction.user_id)
    ).all()
    return {
        uid: CustomerAggregate(
            transaction_count=count,
            failed_count=failed or 0,
            recovered_count=0,
            last_activity_at=last,
        )
        for uid, count, failed, last in rows
    }


def _open_case_counts(db: Session, customer_ids: set[str]) -> dict[str, int]:
    if not customer_ids:
        return {}
    rows = db.execute(
        select(SupportCase.customer_id, func.count(SupportCase.case_id))
        .where(
            SupportCase.customer_id.in_(customer_ids),
            SupportCase.status.in_([s.value for s in OPEN_CASE_STATUSES]),
        )
        .group_by(SupportCase.customer_id)
    ).all()
    return {uid: n for uid, n in rows}


def get_customer_profile(db: Session, customer_id: str, *, tx_limit: int = 15) -> dict | None:
    """Full customer-care profile. Returns None when the customer exists
    NOWHERE — not in the registry and not as any transaction's user_id."""
    customer = db.scalars(
        select(Customer).where(Customer.customer_id == customer_id)
    ).one_or_none()

    tx_rows = (
        db.execute(
            select(Transaction)
            .where(Transaction.user_id == customer_id)
            .order_by(Transaction.timestamp.desc())
            .limit(tx_limit)
        )
        .scalars()
        .all()
    )
    if customer is None and not tx_rows:
        return None

    accounts = (
        db.execute(
            select(Account)
            .where(Account.customer_id == customer_id)
            .order_by(Account.is_primary.desc(), Account.account_id)
        )
        .scalars()
        .all()
        if customer
        else []
    )
    signals = (
        db.execute(
            select(CustomerBehaviorSignal)
            .where(CustomerBehaviorSignal.customer_id == customer_id)
            .order_by(CustomerBehaviorSignal.signal_name)
        )
        .scalars()
        .all()
        if customer
        else []
    )
    cases = (
        db.execute(
            select(SupportCase)
            .where(
                SupportCase.customer_id == customer_id,
                SupportCase.status.in_([s.value for s in OPEN_CASE_STATUSES]),
            )
            .order_by(SupportCase.created_at.desc())
        )
        .scalars()
        .all()
    )

    total = db.scalar(
        select(func.count(Transaction.transaction_id)).where(
            Transaction.user_id == customer_id
        )
    )
    # "failed" = hit a failure at least once (failure_reason set, or still
    # sitting in FAILED). NOT current_state == FAILED: the ML chain advances
    # failed payments to RECOVERY_PENDING, which would hide them.
    failed = db.scalar(
        select(func.count(Transaction.transaction_id)).where(
            Transaction.user_id == customer_id,
            or_(
                Transaction.failure_reason.isnot(None),
                Transaction.current_state == "FAILED",
            ),
        )
    )
    recovered = db.scalar(
        select(func.count(func.distinct(RecoveryActionRecord.transaction_id)))
        .select_from(RecoveryActionRecord)
        .join(Transaction, Transaction.transaction_id == RecoveryActionRecord.transaction_id)
        .where(
            Transaction.user_id == customer_id,
            RecoveryActionRecord.status == "VERIFIED",
        )
    )
    last_activity = db.scalar(
        select(func.max(Transaction.timestamp)).where(Transaction.user_id == customer_id)
    )

    dataset_known = customer is not None
    if customer is None:
        # exists only as a transaction user_id — honest fallbacks, no
        # fabricated identity fields
        customer = Customer(
            customer_id=customer_id,
            full_name=customer_id,
            email="",
            status="unknown",
        )

    return {
        "customer": customer,
        "dataset_known": dataset_known,
        "accounts": accounts,
        "aggregate": CustomerAggregate(
            transaction_count=total or 0,
            failed_count=failed or 0,
            recovered_count=recovered or 0,
            last_activity_at=last_activity,
        ),
        "recent_transactions": tx_rows,
        "behavior_signals": signals,
        "open_cases": [
            OpenCaseSummary(
                case_id=c.case_id,
                subject=c.subject,
                status=c.status,
                priority=c.priority,
                transaction_id=c.transaction_id,
                created_at=c.created_at,
            )
            for c in cases
        ],
    }


# --------------------------------------------------------------------------------------
# Transaction list (support-facing slice)
# --------------------------------------------------------------------------------------


def list_transactions(
    db: Session,
    *,
    q: str | None = None,
    user_id: str | None = None,
    state: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    stmt = select(Transaction)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(Transaction.transaction_id.ilike(like), Transaction.user_id.ilike(like))
        )
    if user_id:
        stmt = stmt.where(Transaction.user_id == user_id)
    if state:
        stmt = stmt.where(Transaction.current_state == state.upper())

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        db.execute(stmt.order_by(Transaction.timestamp.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return {"transactions": rows, "total": total or 0, "limit": limit, "offset": offset}


# --------------------------------------------------------------------------------------
# Support cases (the ONLY state this service owns)
# --------------------------------------------------------------------------------------


def list_cases(
    db: Session,
    *,
    status: str | None = None,
    customer_id: str | None = None,
    transaction_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    stmt = select(SupportCase)
    if status:
        stmt = stmt.where(SupportCase.status == status.upper())
    if customer_id:
        stmt = stmt.where(SupportCase.customer_id == customer_id)
    if transaction_id:
        stmt = stmt.where(SupportCase.transaction_id == transaction_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        db.execute(stmt.order_by(SupportCase.created_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return {"cases": rows, "total": total or 0}


def create_case(db: Session, auth: AuthContext, payload: SupportCaseCreate) -> SupportCase:
    tx = db.scalars(
        select(Transaction).where(Transaction.transaction_id == payload.transaction_id)
    ).one_or_none()
    if tx is None:
        from api.core.exceptions import NotFoundError

        raise NotFoundError(f"transaction not found: {payload.transaction_id}")

    case = SupportCase(
        case_id=f"CASE-{uuid.uuid4().hex[:12]}",
        transaction_id=tx.transaction_id,
        customer_id=tx.user_id,
        subject=payload.subject.strip(),
        description=payload.description,
        status=CaseStatus.OPEN,
        priority=payload.priority,
        created_by=auth.key_name,
        assignee=payload.assignee,
        notes=[],
    )
    db.add(case)
    db.commit()
    db.refresh(case)

    record_security_event(
        actor_type=auth.role,
        actor_id=auth.key_name,
        action=AUDIT_SUPPORT_CASE_CREATED,
        resource_type="support_case",
        resource_id=case.case_id,
        audit_metadata={
            "transaction_id": case.transaction_id,
            "customer_id": case.customer_id,
            "priority": case.priority,
        },
    )
    _publish(
        "SUPPORT_CASE_CREATED",
        case.transaction_id,
        {"case_id": case.case_id, "status": case.status, "customer_id": case.customer_id},
    )
    logger.info(
        "support case created: case_id=%s tx=%s by=%s",
        case.case_id, case.transaction_id, auth.key_name,
    )
    return case


def update_case(
    db: Session, auth: AuthContext, case_id: str, payload: SupportCaseUpdate
) -> SupportCase | None:
    case = db.scalars(select(SupportCase).where(SupportCase.case_id == case_id)).one_or_none()
    if case is None:
        return None

    changed: list[str] = []

    if payload.status is not None and payload.status != case.status:
        allowed = CASE_STATUS_TRANSITIONS.get(case.status, set())
        if payload.status not in allowed:
            from api.core.exceptions import InvalidStateTransitionError

            raise InvalidStateTransitionError(
                f"illegal case transition: {case.status} -> {payload.status}"
            )
        case.status = payload.status
        changed.append(f"status->{payload.status.value}")
        if payload.status == CaseStatus.RESOLVED:
            case.resolved_at = _utcnow()
        elif payload.status == CaseStatus.OPEN:
            case.resolved_at = None  # reopen

    if payload.priority is not None and payload.priority != case.priority:
        case.priority = payload.priority
        changed.append(f"priority->{payload.priority.value}")

    if payload.assignee is not None and payload.assignee != case.assignee:
        case.assignee = payload.assignee or None
        changed.append(f"assignee->{payload.assignee or '(none)'}")

    if payload.note is not None and payload.note.strip():
        notes = list(case.notes or [])
        notes.append(
            {
                "at": _utcnow().isoformat(),
                "by": auth.key_name,
                "role": auth.role,
                "text": payload.note.strip(),
            }
        )
        case.notes = notes
        changed.append("note")

    if not changed:
        return case  # idempotent no-op — nothing requested actually changed

    db.commit()
    db.refresh(case)

    record_security_event(
        actor_type=auth.role,
        actor_id=auth.key_name,
        action=AUDIT_SUPPORT_CASE_UPDATED,
        resource_type="support_case",
        resource_id=case.case_id,
        audit_metadata={"changes": changed, "status": case.status},
    )
    _publish(
        "SUPPORT_CASE_UPDATED",
        case.transaction_id,
        {"case_id": case.case_id, "status": case.status, "changes": changed},
    )
    logger.info(
        "support case updated: case_id=%s changes=%s by=%s",
        case.case_id, changed, auth.key_name,
    )
    return case


def audit_customer_view(db: Session, auth: AuthContext, customer_id: str) -> None:
    """Support reads of a full customer profile are audited (sensitive PII
    surface) — same spirit as TEMPORAL_QUERY / MODEL_SIGNAL."""
    record_security_event(
        actor_type=auth.role,
        actor_id=auth.key_name,
        action=AUDIT_SUPPORT_CUSTOMER_VIEWED,
        resource_type="customer",
        resource_id=customer_id,
    )

"""
api/routes/sandbox.py — Stage 9 sandbox/ops endpoints.

POST /api/v1/sandbox/reset — SANDBOX-ONLY, staff-only (SYSTEM/ADMIN):
resets the simulated provider ledger (in-memory) AND deletes the persisted
sandbox_ledger_entries rows, then records a DEMO_RESET audit row. Wipes
SIMULATED ledger state only — never transactions, decisions, or audit rows.

GET /api/v1/sandbox/ledger — SANDBOX-ONLY, staff-only (SYSTEM/ADMIN/SUPPORT):
a read-only snapshot of the SIMULATED provider ledger (in-memory mock).

GET /api/v1/audit — SYSTEM/ADMIN only: newest-first security-audit rows
with optional action filter. Never returns secrets: actor_id is a key NAME.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from api.core.security import AuthContext, require_roles
from api.db.database import get_db
from api.db.models import SandboxLedgerEntry, SecurityAuditRecord
from api.schemas.transaction import UtcDatetime
from api.services.audit import (
    AUDIT_DEMO_RESET,
    current_request_id,
    record_security_event,
)
from api.services.payment_provider import MockPaymentProvider, get_payment_provider

logger = logging.getLogger("payment_recovery.sandbox_api")

# POST /api/v1/sandbox/reset
sandbox_router = APIRouter(prefix="/api/v1/sandbox", tags=["sandbox"])

# GET /api/v1/audit
audit_router = APIRouter(prefix="/api/v1", tags=["audit"])


class SandboxResetResponse(BaseModel):
    reset: bool
    simulated: bool
    request_id: str | None


class SandboxLedgerEntryOut(BaseModel):
    transaction_id: str
    held_amount: float
    released_amount: float
    currency: str
    provider_reference: str | None
    status: str | None


class SandboxLedgerResponse(BaseModel):
    simulated: bool
    initial_limit: float
    available_limit: float
    currency: str
    entries: list[SandboxLedgerEntryOut]


class AuditRow(BaseModel):
    audit_id: str
    created_at: UtcDatetime
    actor_type: str
    actor_id: str
    action: str
    resource_type: str | None
    resource_id: str | None
    request_id: str | None
    result: str
    reason: str | None


class AuditResponse(BaseModel):
    count: int
    rows: list[AuditRow]


@sandbox_router.post("/reset", response_model=SandboxResetResponse)
def reset_sandbox(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN")),
) -> SandboxResetResponse:
    """SANDBOX-ONLY, staff-only demo reset: wipes the SIMULATED ledger state
    (in-memory provider ledger + persisted sandbox_ledger_entries rows).
    Never touches transactions, recovery decisions, or the audit trail."""
    get_payment_provider().reset()
    deleted = db.query(SandboxLedgerEntry).delete()
    record_security_event(
        actor_type=auth.role,
        actor_id=auth.key_name,
        action=AUDIT_DEMO_RESET,
        resource_type="sandbox_ledger",
        resource_id=None,
        result="ALLOWED",
        reason="sandbox demo reset",
        audit_metadata={"entries_deleted": deleted},
        db=db,
    )
    logger.info("sandbox reset by %s (%s): %d persisted entries cleared",
                auth.key_name, auth.role, deleted)
    return SandboxResetResponse(
        reset=True, simulated=True, request_id=current_request_id()
    )


@sandbox_router.get("/ledger", response_model=SandboxLedgerResponse)
def get_sandbox_ledger(
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT")),
) -> SandboxLedgerResponse:
    """SANDBOX-ONLY, staff-only read of the SIMULATED ledger: every in-memory
    entry of the mock provider, plus the simulated limit. Never real funds."""
    provider = get_payment_provider()
    snapshot = provider.ledger_snapshot()
    return SandboxLedgerResponse(
        simulated=True,
        initial_limit=MockPaymentProvider.INITIAL_LIMIT,
        available_limit=provider.available_limit,
        currency="BDT",
        entries=[
            SandboxLedgerEntryOut(
                transaction_id=e.get("transaction_id", ""),
                held_amount=float(e.get("held_amount") or 0),
                released_amount=float(e.get("released_amount") or 0),
                currency=e.get("currency") or "BDT",
                provider_reference=e.get("provider_reference"),
                status=e.get("status"),
            )
            for e in snapshot
        ],
    )


@audit_router.get("/audit", response_model=AuditResponse)
def get_audit(
    action: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN")),
) -> AuditResponse:
    """Security audit trail, newest-first. SYSTEM/ADMIN only — audit rows
    name roles and key names, but the trail itself is ops-sensitive."""
    stmt = db.query(SecurityAuditRecord).order_by(
        desc(SecurityAuditRecord.created_at), desc(SecurityAuditRecord.id)
    )
    if action:
        stmt = stmt.filter(SecurityAuditRecord.action == action)
    rows = stmt.limit(limit).all()
    return AuditResponse(
        count=len(rows),
        rows=[
            AuditRow(
                audit_id=r.audit_id,
                created_at=r.created_at,
                actor_type=r.actor_type,
                actor_id=r.actor_id,
                action=r.action,
                resource_type=r.resource_type,
                resource_id=r.resource_id,
                request_id=r.request_id,
                result=r.result,
                reason=r.reason,
            )
            for r in rows
        ],
    )

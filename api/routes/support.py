"""
api/routes/support.py — customer-care workspace endpoints.

Roles:
  * reads  (overview / search / profile / transactions / cases / stream):
    SYSTEM, ADMIN, SUPPORT — the same staff read surface the per-transaction
    evidence endpoints already grant SUPPORT;
  * writes (create / update case): SYSTEM, ADMIN, SUPPORT — case routing is
    the support team's OWN write domain; the engine (recovery, risk,
    sandbox) stays SYSTEM/ADMIN only.
"""

from __future__ import annotations

import asyncio
import secrets

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from api.core.exceptions import NotFoundError
from api.core.security import AuthContext, get_auth_context, require_roles
from api.db.database import get_db
from api.schemas.support import (
    CustomerProfileResponse,
    CustomerSearchResponse,
    StreamTicketResponse,
    SupportCaseCreate,
    SupportCaseListResponse,
    SupportCaseResponse,
    SupportCaseUpdate,
    SupportOverviewResponse,
    TransactionListResponse,
)
from api.services import support_service
from api.services.support_stream import (
    HEARTBEAT_SECONDS,
    BusToAsyncBridge,
    TICKET_TTL_SECONDS,
    envelope_to_sse,
    get_ticket_store,
)
from sqlalchemy.orm import Session

STAFF_ROLES = ("SYSTEM", "ADMIN", "SUPPORT")

router = APIRouter(prefix="/api/v1/support", tags=["support"])


@router.get("/overview", response_model=SupportOverviewResponse)
def get_support_overview(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(*STAFF_ROLES)),
):
    """The queue: case counts, work that needs attention, recent activity."""
    return support_service.get_overview(db)


@router.get("/customers/search", response_model=CustomerSearchResponse)
def search_customers(
    q: str = Query(min_length=1, max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(*STAFF_ROLES)),
):
    return support_service.search_customers(db, q, limit=limit)


@router.get("/customers/{customer_id}", response_model=CustomerProfileResponse)
def get_customer_profile(
    customer_id: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(*STAFF_ROLES)),
):
    profile = support_service.get_customer_profile(db, customer_id)
    if profile is None:
        raise NotFoundError(f"customer not found: {customer_id}")
    support_service.audit_customer_view(db, auth, customer_id)
    return profile


@router.get("/transactions", response_model=TransactionListResponse)
def list_transactions(
    q: str | None = Query(default=None, max_length=120),
    user_id: str | None = Query(default=None, max_length=64),
    state: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(*STAFF_ROLES)),
):
    return support_service.list_transactions(
        db, q=q, user_id=user_id, state=state, limit=limit, offset=offset
    )


@router.get("/cases", response_model=SupportCaseListResponse)
def list_cases(
    status: str | None = Query(default=None, max_length=24),
    customer_id: str | None = Query(default=None, max_length=64),
    transaction_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(*STAFF_ROLES)),
):
    return support_service.list_cases(
        db, status=status, customer_id=customer_id, transaction_id=transaction_id,
        limit=limit, offset=offset,
    )


@router.post("/cases", response_model=SupportCaseResponse, status_code=201)
def create_case(
    payload: SupportCaseCreate,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(*STAFF_ROLES)),
):
    return support_service.create_case(db, auth, payload)


@router.patch("/cases/{case_id}", response_model=SupportCaseResponse)
def update_case(
    case_id: str,
    payload: SupportCaseUpdate,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles(*STAFF_ROLES)),
):
    case = support_service.update_case(db, auth, case_id, payload)
    if case is None:
        raise NotFoundError(f"support case not found: {case_id}")
    return case


# --------------------------------------------------------------------------------------
# Live stream (SSE)
# --------------------------------------------------------------------------------------


@router.post("/stream-ticket", response_model=StreamTicketResponse)
def issue_stream_ticket(
    auth: AuthContext = Depends(require_roles(*STAFF_ROLES)),
):
    """Single-use ticket so the browser's EventSource (which cannot send the
    X-API-Key header) can authenticate the stream handshake."""
    ticket = get_ticket_store().issue(auth.key_name, auth.role)
    return StreamTicketResponse(ticket=ticket, expires_in_seconds=TICKET_TTL_SECONDS)


@router.get("/stream")
async def support_stream(
    request: Request,
    ticket: str = Query(min_length=16, max_length=128),
):
    """SSE endpoint authenticated by the single-use ticket (NOT by header —
    EventSource cannot send one). Role authorization happened when the
    ticket was ISSUED; the ticket is bound to that identity and redeemed
    atomically here."""
    from api.services.eventbus.in_memory import get_event_bus

    redeemed = get_ticket_store().redeem(ticket)
    if redeemed is None:
        from api.core.exceptions import AuthenticationError

        raise AuthenticationError("invalid or expired stream ticket")
    key_name, _role = redeemed  # role was authorized at issue time

    bus = get_event_bus()
    bridge = BusToAsyncBridge(asyncio.get_running_loop())
    subscriber_name = f"support-sse:{key_name}:{secrets.token_hex(8)}"
    bus.subscribe(bridge.handle_envelope, name=subscriber_name)

    async def _heartbeat():
        """Keepalive comments so proxies and the browser know the stream is
        alive even when no domain events flow."""
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await bridge.put_nowait("heartbeat")

    async def event_stream():
        heartbeat_task = asyncio.create_task(_heartbeat())
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                item = await bridge.get()  # EventEnvelope | "heartbeat" | None(close)
                if item is None:
                    break
                if item == "heartbeat":
                    yield ": heartbeat\n\n"
                    continue
                yield envelope_to_sse(item)
        finally:
            heartbeat_task.cancel()
            bus.unsubscribe(subscriber_name)
            bridge.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

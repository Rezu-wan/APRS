"""
api/services/support_stream.py — SSE plumbing for the support workspace.

Browsers' EventSource cannot send the X-API-Key header, so the flow is:

    1. POST /support/stream-ticket  (normal header auth, staff roles)
       -> single-use short-lived ticket bound to the caller's identity;
    2. GET  /support/stream?ticket=...  (EventSource) — the ticket is
       redeemed atomically (one connection per ticket), then the connection
       streams live envelopes from the Stage 11A event bus as
       `text/event-stream`.

Realtime only — the bus is the notification seam, the database stays the
source of truth. A dropped connection is surfaced to the agent (the UI
shows a disconnected state); nothing is buffered to fake continuity. The
ticket store is in-process (same scope as the rate limiter / bus — a
documented sandbox limitation).
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
import time

from api.services.eventbus.base import EventEnvelope

logger = logging.getLogger("payment_recovery.support_stream")

TICKET_TTL_SECONDS = 30
MAX_TICKETS = 1_000
HEARTBEAT_SECONDS = 15

# event types the support stream forwards (domain vocabulary, not secrets)
STREAM_EVENT_TYPES = {
    "SUPPORT_CASE_CREATED",
    "SUPPORT_CASE_UPDATED",
    "TRANSACTION_CREATED",
    "PAYMENT_PROCESSING",
    "PAYMENT_SUCCEEDED",
    "PAYMENT_FAILED",
    "PAYMENT_STALLED",
    "ML_RISK_ASSESSED",
    "RECOVERY_CHECKED",
    "LIMIT_RELEASED",
    "MANUAL_REVIEW_TRIGGERED",
    "RECOVERY_REJECTED",
}


class StreamTicketStore:
    """Thread-safe single-use short-lived tickets for SSE handshakes."""

    def __init__(self, ttl_seconds: int = TICKET_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        # ticket -> (key_name, role, expires_at)
        self._tickets: dict[str, tuple[str, str, float]] = {}

    def issue(self, key_name: str, role: str) -> str:
        with self._lock:
            now = time.monotonic()
            if len(self._tickets) >= MAX_TICKETS:
                # drop the oldest — bounded state, same policy family as the
                # bus replay store and the rate limiter
                for stale in sorted(self._tickets.items(), key=lambda kv: kv[1][2])[:1]:
                    self._tickets.pop(stale[0], None)
            ticket = secrets.token_urlsafe(32)
            self._tickets[ticket] = (key_name, role, now + self._ttl)
            return ticket

    def redeem(self, ticket: str) -> tuple[str, str] | None:
        """Atomically consume a ticket; None when unknown, expired, or
        already used (single use — a replayed URL must not reconnect)."""
        with self._lock:
            entry = self._tickets.pop(ticket, None)
        if entry is None:
            return None
        key_name, role, expires_at = entry
        if time.monotonic() > expires_at:
            return None
        return key_name, role


_ticket_store: StreamTicketStore | None = None


def get_ticket_store() -> StreamTicketStore:
    global _ticket_store
    if _ticket_store is None:
        _ticket_store = StreamTicketStore()
    return _ticket_store


def reset_ticket_store() -> None:
    """Test hook."""
    global _ticket_store
    _ticket_store = None


def envelope_to_sse(envelope: EventEnvelope) -> str:
    """Format one bus envelope as an SSE frame with a JSON data payload."""
    data = json.dumps(
        {
            "event_id": envelope.event_id,
            "event_type": envelope.event_type,
            "transaction_id": envelope.transaction_id,
            "occurred_at": envelope.event_timestamp.isoformat(),
            "data": dict(envelope.payload),
        },
        default=str,
    )
    return f"event: support\ndata: {data}\n\n"


class BusToAsyncBridge:
    """Bus handlers run synchronously in the PUBLISHER's thread; the SSE
    response consumes from an asyncio queue on the event loop. This bridge is
    the thread-safe handoff (loop.call_soon_threadsafe).

    Queue items: EventEnvelope (domain event), "heartbeat" (keepalive), or
    None (stream closed).
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, maxsize: int = 200) -> None:
        self._loop = loop
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    def put_nowait(self, item) -> None:
        """Thread-safe enqueue (safe from the publisher's thread AND the
        loop). Drops on overflow — a slow SSE consumer must never build up
        unbounded state; the durable record stays in the database."""
        if self._closed:
            return

        def _put() -> None:
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                logger.warning("support stream queue full — dropping item")

        self._loop.call_soon_threadsafe(_put)

    def handle_envelope(self, envelope: EventEnvelope) -> None:
        """Bus subscriber entry point — forwards only support-relevant types."""
        if envelope.event_type in STREAM_EVENT_TYPES:
            self.put_nowait(envelope)

    def close(self) -> None:
        def _put_close() -> None:
            # runs on the loop AFTER any previously scheduled puts, so the
            # closed flag can never drop our own sentinel
            self._closed = True
            try:
                self._queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

        self._loop.call_soon_threadsafe(_put_close)

    async def get(self):
        return await self._queue.get()

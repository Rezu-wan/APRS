"""
api/services/eventbus/base.py — contracts for the Stage 11A event bus.

The bus is an IN-PROCESS notification seam, not a message broker: subscribers
are called synchronously in the publisher's context, and the replay store is
bounded in-memory state. The DURABLE event record remains the database tables
(payment_events / digital_twin_events) — the bus must never be treated as a
source of truth.

Contracts pinned here:

- EventEnvelope is FROZEN (immutability is contractual) and carries domain
  time (event_timestamp) separately from processing time (created_at).
- payload is copied on construction and must be treated as immutable by all
  parties — handlers must never mutate it.
- A failing subscriber NEVER breaks the publisher or other subscribers
  (implementation detail of InMemoryEventBus, but part of the deal).
- causation_id is the event_id of the event that caused this one; it is None
  for provider-observed events (there is no internal cause).
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable

ENVELOPE_SCHEMA_VERSION = "1"


class SubscriberError(Exception):
    """Raised by the bus bookkeeping itself (e.g. duplicate subscriber
    name). Handler exceptions are NOT this — they are caught and logged by
    the bus so one bad subscriber cannot break delivery."""


@dataclass(frozen=True)
class EventEnvelope:
    """Immutable unit of notification on the event bus.

    event_id       — 32-hex uuid4, unique per envelope; the idempotency anchor
                     for delivery (a subscriber never sees the same event_id
                     twice).
    event_timestamp— tz-aware DOMAIN time (when the thing happened).
    created_at     — tz-aware PROCESSING time (when the envelope was built).
    payload        — Mapping, copied on construction; treat as immutable.
    correlation_id — groups related events; defaults to transaction_id.
    causation_id   — event_id of the causing event, or None for
                     provider-observed events.
    """

    transaction_id: str
    event_type: str
    event_timestamp: datetime
    source: str
    payload: Mapping = field(default_factory=dict)
    provider_event_id: str | None = None
    correlation_id: str = ""  # filled in __post_init__ with transaction_id
    causation_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    schema_version: str = ENVELOPE_SCHEMA_VERSION
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        if not self.correlation_id:
            # frozen dataclass: use object.__setattr__ for the default only
            object.__setattr__(self, "correlation_id", self.transaction_id)
        object.__setattr__(self, "payload", dict(self.payload))


# handler signature: called with the envelope; must not raise (raises are
# caught and logged by the bus)
Handler = Callable[[EventEnvelope], None]


class EventBus(ABC):
    """Publisher/subscriber seam. See InMemoryEventBus for the only shipped
    implementation."""

    @abstractmethod
    def publish(self, envelope: EventEnvelope) -> None:
        """Dispatch an envelope to all matching subscribers. Must never raise
        because of a subscriber failure."""

    @abstractmethod
    def subscribe(
        self,
        handler: Handler,
        *,
        name: str,
        event_types: Iterable[str] | None = None,
    ) -> None:
        """Register a handler. event_types=None means wildcard; otherwise an
        iterable of event_type values to match exactly."""

    @abstractmethod
    def replay(self, transaction_id: str | None = None) -> list[EventEnvelope]:
        """Return stored envelopes (optionally filtered by transaction).
        IN-MEMORY and bounded — NOT durable; the durable record remains
        payment_events / digital_twin_events."""

    @abstractmethod
    def close(self) -> None:
        """Release subscribers. After close, publish is a no-op."""

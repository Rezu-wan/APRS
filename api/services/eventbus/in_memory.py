"""
api/services/eventbus/in_memory.py — the only Stage 11A bus implementation.

Synchronous, in-process, thread-safe. Design guarantees:

- a failing subscriber is logged (logger "payment_recovery.eventbus",
  event_id + subscriber name only) and NEVER breaks the publisher or other
  subscribers;
- delivery is IDEMPOTENT per subscriber: a per-subscriber bounded seen-set
  (last 50_000 event_ids) silently drops duplicate delivery, so a replayed
  provider event cannot cause duplicate business effects;
- the replay store is a bounded deque (default 10_000, oldest dropped) and is
  NOT durable — the durable record remains payment_events /
  digital_twin_events;
- after close(), publish is a no-op and subscribers are cleared.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict, deque

from api.core.config import Settings, get_settings
from api.services.eventbus.base import (
    EventEnvelope,
    EventBus,
    SubscriberError,
)

logger = logging.getLogger("payment_recovery.eventbus")

DEFAULT_REPLAY_MAXLEN = 10_000
SEEN_MAXLEN = 50_000


class InMemoryEventBus(EventBus):
    """Thread-safe in-process bus with idempotent per-subscriber delivery."""

    def __init__(self, replay_maxlen: int = DEFAULT_REPLAY_MAXLEN) -> None:
        self._lock = threading.Lock()
        self._closed = False
        self._replay: deque[EventEnvelope] = deque(maxlen=replay_maxlen)
        # name -> (handler, frozenset(event_types) | None, seen OrderedDict)
        self._subscribers: dict[str, tuple] = {}

    # -- subscription ------------------------------------------------------

    def subscribe(self, handler, *, name, event_types=None) -> None:
        types = frozenset(event_types) if event_types is not None else None
        with self._lock:
            if name in self._subscribers:
                raise SubscriberError(f"subscriber already registered: {name}")
            self._subscribers[name] = (handler, types, OrderedDict())

    # -- publish -----------------------------------------------------------

    def publish(self, envelope: EventEnvelope) -> None:
        with self._lock:
            if self._closed:
                return  # documented choice: publish after close is a no-op
            snapshot = list(self._subscribers.items())
            self._replay.append(envelope)
        for name, (handler, types, seen) in snapshot:
            if types is not None and envelope.event_type not in types:
                continue
            # mark seen ATOMICALLY under the lock, before dispatching: two
            # concurrent publishes of the same event_id must not both invoke
            # the handler (the lock is released during the handler call so a
            # subscriber may publish without deadlocking)
            with self._lock:
                if envelope.event_id in seen:
                    continue  # idempotent delivery: duplicate silently dropped
                seen[envelope.event_id] = None
                while len(seen) > SEEN_MAXLEN:
                    seen.popitem(last=False)
            try:
                handler(envelope)
            except Exception:  # noqa: BLE001 — subscriber failure is isolated
                logger.warning(
                    "eventbus subscriber failed: subscriber=%s event_id=%s",
                    name,
                    envelope.event_id,
                )

    # -- replay ------------------------------------------------------------

    def replay(self, transaction_id=None) -> list[EventEnvelope]:
        with self._lock:
            envelopes = list(self._replay)
        if transaction_id is None:
            return envelopes
        return [e for e in envelopes if e.transaction_id == transaction_id]

    # -- shutdown ----------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._subscribers.clear()


# --- singleton + factory ---------------------------------------------------------------------------------------------

_bus: EventBus | None = None


def get_event_bus(settings: Settings | None = None) -> EventBus:
    """Return the process-wide bus, creating it on first use. Mirrors the
    payment_provider pattern: selection by name, unknown names are a hard
    configuration error."""
    global _bus
    if _bus is None:
        settings = settings or get_settings()
        if settings.event_bus == "in_memory":
            _bus = InMemoryEventBus()
        else:
            raise ValueError(f"unknown event_bus: {settings.event_bus!r}")
    return _bus


def reset_event_bus() -> None:
    """Test hook: discard the singleton (call close() first if needed)."""
    global _bus
    _bus = None

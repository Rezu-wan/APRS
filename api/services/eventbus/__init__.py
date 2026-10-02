"""
api/services/eventbus — Stage 11A in-process event bus.

Public surface (import from here, not the submodules):

    EventEnvelope, EventBus, SubscriberError   (contracts, base.py)
    InMemoryEventBus, get_event_bus,
    reset_event_bus                            (implementation, in_memory.py)
"""

from api.services.eventbus.base import (
    EventEnvelope,
    EventBus,
    SubscriberError,
)
from api.services.eventbus.in_memory import (
    InMemoryEventBus,
    get_event_bus,
    reset_event_bus,
)

__all__ = [
    "EventEnvelope",
    "EventBus",
    "SubscriberError",
    "InMemoryEventBus",
    "get_event_bus",
    "reset_event_bus",
]

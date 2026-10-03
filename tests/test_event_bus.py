"""Stage 11A Phase 11A: in-process event bus — envelope contract,
idempotent delivery, subscriber isolation, replay bounds, factory, and
end-to-end publication from payment-event ingestion."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import pytest

from api.db.models import PaymentEvent
from api.services.eventbus import (
    EventEnvelope,
    InMemoryEventBus,
    SubscriberError,
    get_event_bus,
    reset_event_bus,
)
from tests.conftest import SYSTEM_KEY, make_event

EVENT_URL = "/api/v1/transaction/event"
PE_URL = "/api/v1/transactions/{tid}/payment-events"


def _envelope(**overrides) -> EventEnvelope:
    defaults = dict(
        transaction_id="TXN-1",
        event_type="GATEWAY_TIMEOUT",
        event_timestamp=datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
        source="GATEWAY",
        payload={"status": "TIMEOUT"},
        provider_event_id="PE-1",
    )
    defaults.update(overrides)
    return EventEnvelope(**defaults)


@pytest.fixture(autouse=True)
def _fresh_bus():
    reset_event_bus()
    yield
    bus = get_event_bus()
    bus.close()
    reset_event_bus()


# --- envelope contract ------------------------------------------------------


def test_envelope_is_frozen():
    env = _envelope()
    with pytest.raises(Exception):
        env.event_type = "OTHER"  # type: ignore[misc]


def test_envelope_defaults():
    env = _envelope()
    assert len(env.event_id) == 32
    int(env.event_id, 16)  # 32-hex uuid4
    assert env.correlation_id == "TXN-1"  # defaults to transaction_id
    assert env.causation_id is None
    assert env.schema_version == "1"
    assert env.created_at.tzinfo is not None


def test_envelope_payload_is_copied():
    payload = {"status": "TIMEOUT"}
    env = _envelope(payload=payload)
    payload["status"] = "MUTATED"
    assert env.payload["status"] == "TIMEOUT"


# --- subscription + dispatch -------------------------------------------------


def test_typed_and_wildcard_both_receive():
    bus = InMemoryEventBus()
    seen: list[str] = []
    bus.subscribe(lambda e: seen.append("typed"), name="typed",
                  event_types=["GATEWAY_TIMEOUT"])
    bus.subscribe(lambda e: seen.append("wild"), name="wild")
    bus.publish(_envelope())
    assert sorted(seen) == ["typed", "wild"]


def test_typed_subscription_filters_other_types():
    bus = InMemoryEventBus()
    seen: list[EventEnvelope] = []
    bus.subscribe(seen.append, name="typed", event_types=["BANK_DEBIT_FAILED"])
    bus.publish(_envelope())  # GATEWAY_TIMEOUT — no match
    assert seen == []


def test_duplicate_delivery_is_idempotent_per_subscriber():
    bus = InMemoryEventBus()
    calls: list[str] = []
    bus.subscribe(lambda e: calls.append("a"), name="a")
    bus.subscribe(lambda e: calls.append("b"), name="b")
    env = _envelope()
    bus.publish(env)
    bus.publish(env)  # same event_id replayed
    # subscriber "a" sees it once; the DIFFERENT subscriber still receives it
    assert calls == ["a", "b"]


def test_failing_subscriber_does_not_break_others(caplog):
    bus = InMemoryEventBus()
    called: list[bool] = []

    def boom(_: EventEnvelope) -> None:
        raise RuntimeError("subscriber exploded")

    bus.subscribe(boom, name="boom")
    bus.subscribe(lambda e: called.append(True), name="healthy")
    with caplog.at_level(logging.WARNING, logger="payment_recovery.eventbus"):
        bus.publish(_envelope())  # must not raise
    assert called == [True]
    assert any("boom" in r.message and r.levelno == logging.WARNING
               for r in caplog.records)


def test_duplicate_subscriber_name_rejected():
    bus = InMemoryEventBus()
    bus.subscribe(lambda e: None, name="dup")
    with pytest.raises(SubscriberError):
        bus.subscribe(lambda e: None, name="dup")


# --- replay -------------------------------------------------------------------


def test_replay_returns_envelopes_and_filters_by_transaction():
    bus = InMemoryEventBus()
    bus.publish(_envelope(transaction_id="TXN-A", provider_event_id="P-A"))
    bus.publish(_envelope(transaction_id="TXN-B", provider_event_id="P-B"))
    assert [e.transaction_id for e in bus.replay()] == ["TXN-A", "TXN-B"]
    assert [e.transaction_id for e in bus.replay(transaction_id="TXN-A")] == ["TXN-A"]
    assert bus.replay(transaction_id="TXN-NOPE") == []


def test_replay_store_is_bounded():
    bus = InMemoryEventBus(replay_maxlen=3)
    for i in range(5):
        bus.publish(_envelope(provider_event_id=f"P-{i}"))
    stored = bus.replay()
    assert len(stored) == 3
    assert [e.provider_event_id for e in stored] == ["P-2", "P-3", "P-4"]


# --- close ----------------------------------------------------------------------


def test_close_clears_subscribers_and_publish_is_noop():
    bus = InMemoryEventBus()
    calls: list[EventEnvelope] = []
    bus.subscribe(calls.append, name="cap")
    bus.close()
    bus.publish(_envelope())  # must not raise, must not deliver
    assert calls == []
    assert bus.replay() == []
    bus.close()  # double close is safe


# --- factory ----------------------------------------------------------------------


def test_factory_unknown_name_raises_value_error():
    from api.core.config import Settings

    with pytest.raises(ValueError):
        get_event_bus(Settings(event_bus="kafka"))


def test_factory_singleton_identity():
    bus1 = get_event_bus()
    bus2 = get_event_bus()
    assert bus1 is bus2
    assert isinstance(bus1, InMemoryEventBus)


# --- end-to-end: ingestion publishes ----------------------------------------------


def _unique_tx() -> str:
    return f"TXN-EVB-{uuid.uuid4().hex[:12]}"


def test_ingestion_publishes_envelope_and_sets_columns(client, schema):
    from api.db.database import SessionLocal

    tid = _unique_tx()
    resp = client.post(
        EVENT_URL, json=make_event(tid, amount=50.00, status="SUCCESS"),
        headers=SYSTEM_KEY,
    )
    assert resp.status_code == 200

    captured: list[EventEnvelope] = []
    get_event_bus().subscribe(captured.append, name="test-capture")

    peid = f"PE-EVB-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        PE_URL.format(tid=tid),
        json={
            "events": [{
                "provider_event_id": peid,
                "event_type": "GATEWAY_TIMEOUT",
                "source": "GATEWAY",
                "status": "TIMEOUT",
                "event_timestamp": datetime(
                    2026, 10, 2, 12, 30, tzinfo=timezone.utc
                ).isoformat(),
            }]
        },
        headers=SYSTEM_KEY,
    )
    assert resp.status_code == 200
    assert resp.json()["created"] == 1

    assert len(captured) == 1
    env = captured[0]
    assert env.transaction_id == tid
    assert env.event_type == "GATEWAY_TIMEOUT"
    assert env.correlation_id == tid
    assert env.provider_event_id == peid
    assert env.event_timestamp.tzinfo is not None
    assert set(env.payload) == {"status", "latency_ms", "reference_id"}

    # duplicate replay of the same provider_event_id -> NO new envelope
    resp = client.post(
        PE_URL.format(tid=tid),
        json={
            "events": [{
                "provider_event_id": peid,
                "event_type": "GATEWAY_TIMEOUT",
                "source": "GATEWAY",
                "status": "TIMEOUT",
                "event_timestamp": datetime(
                    2026, 10, 2, 12, 30, tzinfo=timezone.utc
                ).isoformat(),
            }]
        },
        headers=SYSTEM_KEY,
    )
    assert resp.status_code == 200
    assert resp.json()["duplicates"] == 1
    assert len(captured) == 1

    # model columns on the stored row
    with SessionLocal() as db:
        row = (
            db.query(PaymentEvent)
            .filter(PaymentEvent.provider_event_id == peid)
            .one()
        )
        assert row.correlation_id == tid
        assert row.causation_id is None
        assert row.schema_version == "1"

"""Reconstruction ENGINE tests (pure, no DB): scenario -> root cause,
stage statuses, confidence formula, ordering, duplicate tolerance."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import NamedTuple

import pytest

from api.core.payment_lifecycle import (
    EVENT_TYPE_INFO,
    HAPPY_PATH_EVENTS,
    PaymentStage,
)
from api.services.event_reconstruction import reconstruct_from_events

NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
BASE = NOW - timedelta(minutes=30)


class FakeEvent(NamedTuple):
    """Lightweight PaymentEvent stand-in (the engine reads attributes only)."""

    id: int
    event_id: str
    transaction_id: str
    provider_event_id: str
    event_type: str
    source: str
    status: str
    event_timestamp: datetime
    reference_id: str | None = None
    latency_ms: int | None = None
    event_metadata: dict | None = None


_counter = 0


def ev(event_type: str, minutes: float, *, provider_event_id: str | None = None) -> FakeEvent:
    """Deterministic fake event for an event type at BASE + minutes."""
    global _counter
    _counter += 1
    info = EVENT_TYPE_INFO[event_type]
    return FakeEvent(
        id=_counter,
        event_id=f"EVT-{_counter:04d}",
        transaction_id="TXN-T",
        provider_event_id=provider_event_id or f"P-{event_type}-{_counter}",
        event_type=event_type,
        source=info["source"],
        status=info["outcome"],
        event_timestamp=BASE + timedelta(minutes=minutes),
    )


def happy(*skip: str, delay_step: float = 1.0) -> list[FakeEvent]:
    """The 7 happy-path events in order, minus the named skips."""
    types = [et for et in HAPPY_PATH_EVENTS if et not in skip]
    return [ev(et, i * delay_step) for i, et in enumerate(types)]


def prefix_through(event_type: str, *skip: str) -> list[FakeEvent]:
    """Happy-path events UP TO AND INCLUDING event_type (flow stops at the
    failure), minus the named skips. A timeout means nothing downstream was
    ever observed."""
    idx = HAPPY_PATH_EVENTS.index(event_type)
    types = [
        et for et in HAPPY_PATH_EVENTS[: idx + 1] if et not in skip
    ]
    return [ev(et, i * 1.0) for i, et in enumerate(types)]


def make(events: list[FakeEvent]):
    return reconstruct_from_events("TXN-T", events, NOW)


def test_success_reconstruction():
    result = make(happy())
    assert result.root_cause == "NONE"
    assert result.failure_stage is None
    assert result.last_successful_stage == PaymentStage.SETTLEMENT
    assert result.current_stage == PaymentStage.SETTLEMENT
    assert result.customer_debit_status == "CONFIRMED"
    assert result.gateway_status == "CONFIRMED"
    assert result.merchant_confirmation_status == "CONFIRMED"
    assert result.settlement_status == "CONFIRMED"
    assert result.reconstruction_confidence == 1.0
    assert result.missing_events == []
    assert "Payment completed successfully." in result.evidence_summary


def test_gateway_timeout_reconstruction():
    events = prefix_through("GATEWAY_REQUEST_SENT") + [
        ev("GATEWAY_TIMEOUT", 2.5)
    ]
    result = make(events)
    assert result.root_cause == "GATEWAY_TIMEOUT"
    assert result.failure_stage == PaymentStage.GATEWAY
    assert result.last_successful_stage == PaymentStage.BANK_DEBIT
    assert result.current_stage == PaymentStage.GATEWAY
    assert result.customer_debit_status == "CONFIRMED"
    assert result.gateway_status == "TIMEOUT"
    assert result.settlement_status == "NOT_OBSERVED"
    assert "GATEWAY_RESPONSE_RECEIVED" in result.missing_events
    assert "MERCHANT_CONFIRMATION_REQUESTED" in result.missing_events
    assert "Gateway timeout occurred." in result.evidence_summary
    assert "Settlement evidence was not observed." in result.evidence_summary
    assert result.evidence_summary[-1] == "Root cause: gateway timeout."


def test_gateway_error_reconstruction():
    events = prefix_through("GATEWAY_REQUEST_SENT") + [ev("GATEWAY_ERROR", 2.5)]
    result = make(events)
    assert result.root_cause == "GATEWAY_ERROR"
    assert result.failure_stage == PaymentStage.GATEWAY
    assert result.last_successful_stage == PaymentStage.BANK_DEBIT
    assert "Gateway error occurred." in result.evidence_summary


def test_merchant_timeout_reconstruction():
    events = prefix_through("MERCHANT_CONFIRMATION_REQUESTED") + [
        ev("MERCHANT_CONFIRMATION_TIMEOUT", 5.5)
    ]
    result = make(events)
    assert result.root_cause == "MERCHANT_CONFIRMATION_TIMEOUT"
    assert result.failure_stage == PaymentStage.MERCHANT_CONFIRMATION
    assert result.last_successful_stage == PaymentStage.GATEWAY
    assert result.gateway_status == "CONFIRMED"
    assert result.merchant_confirmation_status == "TIMEOUT"
    assert result.settlement_status == "NOT_OBSERVED"
    assert "MERCHANT_CONFIRMATION_RECEIVED" in result.missing_events


def test_merchant_error_reconstruction():
    events = prefix_through("MERCHANT_CONFIRMATION_REQUESTED") + [
        ev("MERCHANT_ERROR", 5.5)
    ]
    result = make(events)
    assert result.root_cause == "MERCHANT_ERROR"
    assert result.failure_stage == PaymentStage.MERCHANT_CONFIRMATION
    assert result.gateway_status == "CONFIRMED"


def test_settlement_failure_reconstruction():
    events = happy("SETTLEMENT_CONFIRMED") + [ev("SETTLEMENT_FAILED", 7.5)]
    result = make(events)
    assert result.root_cause == "SETTLEMENT_FAILED"
    assert result.failure_stage == PaymentStage.SETTLEMENT
    assert result.last_successful_stage == PaymentStage.MERCHANT_CONFIRMATION
    assert result.merchant_confirmation_status == "CONFIRMED"
    assert result.settlement_status == "FAILED"


def test_settlement_not_confirmed_reconstruction():
    events = happy("SETTLEMENT_CONFIRMED") + [
        ev("SETTLEMENT_NOT_CONFIRMED", 7.5)
    ]
    result = make(events)
    assert result.root_cause == "SETTLEMENT_NOT_CONFIRMED"
    assert result.failure_stage == PaymentStage.SETTLEMENT
    assert result.merchant_confirmation_status == "CONFIRMED"
    assert "Settlement not confirmed." in result.evidence_summary


def test_debit_failure_reconstruction():
    result = make([ev("CUSTOMER_DEBIT_FAILED", 0.5)])
    assert result.root_cause == "CUSTOMER_DEBIT_FAILED"
    assert result.failure_stage == PaymentStage.BANK_DEBIT
    assert result.current_stage == PaymentStage.BANK_DEBIT
    assert result.last_successful_stage is None
    assert result.customer_debit_status == "FAILED"
    assert result.gateway_status == "NOT_OBSERVED"
    assert result.merchant_confirmation_status == "NOT_OBSERVED"
    assert result.settlement_status == "NOT_OBSERVED"
    assert result.evidence_summary[-1] == "Root cause: customer debit failed."


def test_events_sorted_by_timestamp():
    events = happy()
    shuffled = [events[i] for i in (5, 1, 6, 0, 4, 2, 3)]
    result = make(shuffled)
    stamps = [e.event_timestamp for e in result.ordered_events]
    assert stamps == sorted(stamps)
    assert [e.event_type for e in result.ordered_events] == list(HAPPY_PATH_EVENTS)


def test_out_of_order_events():
    events = [
        ev("SETTLEMENT_CONFIRMED", 7.0),
        ev("CUSTOMER_DEBIT_CONFIRMED", 0.0),
        ev("GATEWAY_RESPONSE_RECEIVED", 2.0),
        ev("GATEWAY_REQUEST_SENT", 1.0),
        ev("MERCHANT_CONFIRMATION_RECEIVED", 4.0),
        ev("MERCHANT_CONFIRMATION_REQUESTED", 3.0),
        ev("SETTLEMENT_REQUESTED", 5.0),
    ]
    result = make(events)
    stamps = [e.event_timestamp for e in result.ordered_events]
    assert stamps == sorted(stamps)
    assert result.root_cause == "NONE"
    assert result.reconstruction_confidence == 1.0


def test_duplicate_events():
    """Provider redelivery: the same provider_event_id twice. The engine
    tolerates it — both copies are kept in ordered_events, but the stage
    status is unaffected because a redelivery carries the same outcome."""
    first = ev("CUSTOMER_DEBIT_CONFIRMED", 0.0)
    dup = FakeEvent(
        id=99,
        event_id="EVT-DUP",
        transaction_id="TXN-T",
        provider_event_id=first.provider_event_id,  # SAME provider id
        event_type=first.event_type,
        source=first.source,
        status=first.status,
        event_timestamp=first.event_timestamp + timedelta(seconds=1),
    )
    events = [first, dup, *happy("CUSTOMER_DEBIT_CONFIRMED")]
    result = make(events)
    assert len(result.ordered_events) == 8
    assert result.customer_debit_status == "CONFIRMED"
    assert result.root_cause == "NONE"
    assert result.reconstruction_confidence == 1.0


def test_missing_events():
    result = make([])
    assert result.root_cause == "INCOMPLETE"
    assert result.failure_stage is None
    assert result.current_stage == "UNAVAILABLE"
    assert result.last_successful_stage is None
    for status in (
        result.customer_debit_status,
        result.gateway_status,
        result.merchant_confirmation_status,
        result.settlement_status,
    ):
        assert status == "NOT_OBSERVED"
    assert result.reconstruction_confidence == 0.0
    assert result.missing_events == list(HAPPY_PATH_EVENTS)
    assert (
        "Payment flow is incomplete; final outcome unknown from available evidence."
        in result.evidence_summary
    )
    assert "Bank debit evidence was not observed." in result.evidence_summary


def test_progress_only_stage_is_observed():
    """Evidence exists but no terminal outcome anywhere -> INCOMPLETE, never
    a guessed failure (Step-16 integrity rule)."""
    events = prefix_through("GATEWAY_REQUEST_SENT")  # request sent, no response
    result = make(events)
    assert result.root_cause == "INCOMPLETE"
    assert result.failure_stage is None
    assert result.gateway_status == "OBSERVED"
    assert result.last_successful_stage == PaymentStage.BANK_DEBIT
    assert (
        "Gateway request was sent, but no response was observed."
        in result.evidence_summary
    )


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        (happy(), 1.0),
        # debit confirmed + request sent + GATEWAY_TIMEOUT: (2 + 1) / 7
        (
            prefix_through("GATEWAY_REQUEST_SENT") + [ev("GATEWAY_TIMEOUT", 2.5)],
            0.43,
        ),
        # through merchant requested + MERCHANT_CONFIRMATION_TIMEOUT: (4 + 1) / 7
        (
            prefix_through("MERCHANT_CONFIRMATION_REQUESTED")
            + [ev("MERCHANT_CONFIRMATION_TIMEOUT", 5.5)],
            0.71,
        ),
        # debit failure alone: (0 + 1) / 7
        ([ev("CUSTOMER_DEBIT_FAILED", 0.5)], 0.14),
        ([], 0.0),
    ],
)
def test_confidence_values(scenario, expected):
    assert make(scenario).reconstruction_confidence == expected

"""Stage 8 SAFETY-GATE tests (pure): fresh facts must re-derive every
conclusion. The gate takes FRESH events/reconstruction, so a settlement that
arrived after the assessment blocks execution (spec section 10)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from api.core.payment_lifecycle import EVENT_TYPE_INFO
from api.schemas.recovery_autonomous import (
    BLOCK_ALREADY_RECOVERED,
    BLOCK_ALREADY_SUCCESS,
    BLOCK_DOUBLE_DEDUCTION,
    BLOCK_INSUFFICIENT_EVIDENCE,
    BLOCK_NEW_SUCCESSFUL_SETTLEMENT,
)
from api.services.event_reconstruction import reconstruct_from_events
from api.services.recovery_safety import check_safety

NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)
BASE = NOW - timedelta(minutes=30)


class FakeEvent(NamedTuple):
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


class FakeTx(NamedTuple):
    transaction_id: str = "TXN-T"
    current_state: str = "RISK_ASSESSED"


class FakeAssessment(NamedTuple):
    recovery_candidate: bool = True


class FakeRecoveryRow(NamedTuple):
    status: str = "PENDING"


_counter = 0


def ev(event_type: str, minutes: float, *,
       provider_event_id: str | None = None) -> FakeEvent:
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


def prefix_through(event_type: str) -> list[FakeEvent]:
    from api.core.payment_lifecycle import HAPPY_PATH_EVENTS

    idx = HAPPY_PATH_EVENTS.index(event_type)
    return [
        ev(et, i * 1.0)
        for i, et in enumerate(HAPPY_PATH_EVENTS[: idx + 1])
    ]


def failure_events() -> list[FakeEvent]:
    """Debit confirmed, gateway timeout, nothing downstream."""
    return prefix_through("GATEWAY_REQUEST_SENT") + [ev("GATEWAY_TIMEOUT", 2.5)]


def gate(fresh_events, *, tx=None, existing=None):
    reconstruction = reconstruct_from_events("TXN-T", fresh_events, NOW)
    return check_safety(
        tx or FakeTx(),
        fresh_events,
        reconstruction,
        FakeAssessment(),
        existing,
        now=NOW,
    )


def test_successful_settlement_blocks_recovery():
    """The race-condition check (spec section 10): the payment settled on
    its own after the assessment -> BLOCK_NEW_SUCCESSFUL_SETTLEMENT."""
    result = gate(failure_events() + [ev("SETTLEMENT_CONFIRMED", 5.0)])
    assert result.allowed is False
    assert result.blocked_reason == BLOCK_NEW_SUCCESSFUL_SETTLEMENT
    assert result.checks[-1]["passed"] is False


def test_new_event_rechecked_before_execution():
    """The gate uses FRESH events, not assessment-time ones: the same
    transaction is allowed on stale-clean facts but blocked once fresh
    events show a settlement."""
    stale = gate(failure_events())
    assert stale.allowed is True
    assert stale.blocked_reason is None

    fresh = gate(failure_events() + [ev("SETTLEMENT_CONFIRMED", 5.0)])
    assert fresh.allowed is False
    assert fresh.blocked_reason == BLOCK_NEW_SUCCESSFUL_SETTLEMENT


def test_existing_recovery_blocks_duplicate():
    """A recovery row already in flight -> BLOCK_ALREADY_RECOVERED."""
    result = gate(failure_events(), existing=FakeRecoveryRow(status="PENDING"))
    assert result.allowed is False
    assert result.blocked_reason == BLOCK_ALREADY_RECOVERED


def test_completed_recovery_blocks_duplicate():
    result = gate(failure_events(), existing=FakeRecoveryRow(status="COMPLETED"))
    assert result.allowed is False
    assert result.blocked_reason == BLOCK_ALREADY_RECOVERED


def test_blocked_row_does_not_block():
    """A BLOCKED row is dead history — it must not block a fresh attempt."""
    result = gate(failure_events(), existing=FakeRecoveryRow(status="BLOCKED"))
    assert result.allowed is True


def test_duplicate_deduction_blocks_recovery():
    """Two debit confirmations in the FRESH stream -> BLOCK_DOUBLE_DEDUCTION."""
    events = [
        ev("CUSTOMER_DEBIT_CONFIRMED", 0.0, provider_event_id="P-DEBIT-A"),
        ev("CUSTOMER_DEBIT_CONFIRMED", 1.0, provider_event_id="P-DEBIT-B"),
        ev("GATEWAY_REQUEST_SENT", 2.0),
        ev("GATEWAY_TIMEOUT", 2.5),
    ]
    result = gate(events)
    assert result.allowed is False
    assert result.blocked_reason == BLOCK_DOUBLE_DEDUCTION


def test_already_success_blocked():
    result = gate(
        failure_events(), tx=FakeTx(current_state="SUCCESS")
    )
    assert result.allowed is False
    assert result.blocked_reason == BLOCK_ALREADY_SUCCESS


def test_already_limit_released_blocked():
    result = gate(
        failure_events(), tx=FakeTx(current_state="LIMIT_RELEASED")
    )
    assert result.allowed is False
    assert result.blocked_reason == BLOCK_ALREADY_RECOVERED


def test_no_assessment_blocks_on_insufficient_evidence():
    reconstruction = reconstruct_from_events("TXN-T", failure_events(), NOW)
    result = check_safety(
        FakeTx(), failure_events(), reconstruction, None, None, now=NOW
    )
    assert result.allowed is False
    assert result.blocked_reason == BLOCK_INSUFFICIENT_EVIDENCE

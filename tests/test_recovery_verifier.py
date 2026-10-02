"""Stage 8 VERIFIER tests (pure): post-execution evidence must prove the
release happened exactly as requested, with no competing recovery and no
post-execution settlement (spec section 19)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from api.services.recovery_verifier import verify_release

NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)


class FakeTx(NamedTuple):
    transaction_id: str = "TXN-T"
    current_state: str = "RECOVERY_PENDING"


class FakeRecoveryRow(NamedTuple):
    requested_amount: float = 1000.0
    provider_reference: str | None = "PRV-REL-001"
    status: str = "VERIFICATION_PENDING"


class FakeEvent(NamedTuple):
    id: int
    event_type: str
    event_timestamp: datetime


def ledger(
    *, status: str = "RELEASED", released_amount: float = 1000.0,
    provider_reference: str | None = "PRV-REL-001",
) -> dict:
    return {
        "status": status,
        "released_amount": released_amount,
        "provider_reference": provider_reference,
    }


def base_verify(**kwargs) -> object:
    params = dict(
        tx=FakeTx(),
        recovery_row=FakeRecoveryRow(),
        ledger_entry=ledger(),
        fresh_events=[],
        released_after=NOW - timedelta(minutes=1),
        all_recovery_rows=[],
        now=NOW,
    )
    params.update(kwargs)
    return verify_release(**params)


def test_successful_release_verified():
    row = FakeRecoveryRow()
    result = base_verify(
        all_recovery_rows=[row],
    )
    assert result.passed is True
    assert all(c["passed"] for c in result.checks)


def test_wrong_amount_fails_verification():
    result = base_verify(ledger_entry=ledger(released_amount=999.0))
    assert result.passed is False
    amount_check = next(
        c for c in result.checks if c["name"] == "amount_matches"
    )
    assert amount_check["passed"] is False


def test_amount_within_tolerance_passes():
    result = base_verify(ledger_entry=ledger(released_amount=1000.0005))
    assert result.passed is True


def test_missing_provider_reference_fails_verification():
    result = base_verify(ledger_entry=ledger(provider_reference=None))
    assert result.passed is False
    ref_check = next(
        c for c in result.checks if c["name"] == "provider_reference"
    )
    assert ref_check["passed"] is False


def test_mismatched_provider_reference_fails_verification():
    result = base_verify(ledger_entry=ledger(provider_reference="PRV-OTHER"))
    assert result.passed is False


def test_missing_ledger_entry_fails_verification():
    result = base_verify(ledger_entry=None)
    assert result.passed is False


def test_settlement_after_execution_requires_review():
    """Spec section 19: the provider settled the payment AFTER the release
    — releasing on top risks double payment; verification MUST fail."""
    late = FakeEvent(
        id=1, event_type="SETTLEMENT_CONFIRMED",
        event_timestamp=NOW + timedelta(seconds=30),
    )
    result = base_verify(fresh_events=[late])
    assert result.passed is False
    settle_check = next(
        c for c in result.checks if c["name"] == "no_settlement_after_release"
    )
    assert settle_check["passed"] is False


def test_settlement_before_execution_is_fine():
    early = FakeEvent(
        id=1, event_type="SETTLEMENT_CONFIRMED",
        event_timestamp=NOW - timedelta(hours=1),
    )
    result = base_verify(fresh_events=[early])
    assert result.passed is True


def test_success_state_fails_verification():
    result = base_verify(tx=FakeTx(current_state="SUCCESS"))
    assert result.passed is False


def test_concurrent_recovery_fails_verification():
    row = FakeRecoveryRow()
    rival = FakeRecoveryRow(provider_reference="PRV-REL-002")
    result = base_verify(all_recovery_rows=[row, rival])
    assert result.passed is False
    single = next(
        c for c in result.checks if c["name"] == "single_active_recovery"
    )
    assert single["passed"] is False


def test_blocked_rival_row_is_ignored():
    row = FakeRecoveryRow()
    dead = FakeRecoveryRow(provider_reference="PRV-OLD", status="BLOCKED")
    result = base_verify(all_recovery_rows=[row, dead])
    assert result.passed is True

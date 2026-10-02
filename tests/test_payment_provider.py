"""tests/test_payment_provider.py — Stage 8 sandbox provider seam (spec §52).

Verifies the mock's REAL ledger semantics: holds and releases must actually
move the simulated money-adjacent state, provider-level idempotency must
replay the original result, and failure injection must fail safely (no
ledger change) and auto-clear.
"""

from __future__ import annotations

import pytest

from api.core.config import Settings
from api.services.payment_provider import (
    MockPaymentProvider,
    PaymentProvider,
    get_payment_provider,
)

TXN = "txn-provider-test-1"


@pytest.fixture()
def provider() -> MockPaymentProvider:
    p = MockPaymentProvider()
    p.reset()
    return p


def _hold(provider: MockPaymentProvider, amount: float = 500.0) -> None:
    provider.ensure_hold(TXN, amount, "BDT")


def test_hold_then_release_changes_ledger(provider: MockPaymentProvider) -> None:
    _hold(provider, 500.0)
    entry = provider.get_ledger_entry(TXN)
    assert entry is not None
    assert entry["held_amount"] == 500.0
    assert entry["released_amount"] == 0.0

    before_available = provider.available_limit
    result = provider.release_limit(
        transaction_id=TXN,
        amount=500.0,
        currency="BDT",
        idempotency_key="key-ledger-1",
    )
    assert result.success is True

    entry = provider.get_ledger_entry(TXN)
    # spec §52: the release must ACTUALLY move held -> released
    assert entry["held_amount"] == 500.0
    assert entry["released_amount"] == 500.0
    assert provider.available_limit == before_available + 500.0


def test_release_returns_reference(provider: MockPaymentProvider) -> None:
    _hold(provider, 300.0)
    result = provider.release_limit(
        transaction_id=TXN,
        amount=300.0,
        currency="BDT",
        idempotency_key="key-ref-1",
    )
    assert result.success is True
    assert result.provider == "mock"  # sandbox honesty at the data level
    assert result.operation == "RELEASE_LIMIT"
    assert result.amount == 300.0
    assert result.currency == "BDT"
    assert result.provider_reference is not None
    assert result.provider_reference.startswith("REL-")
    assert len(result.provider_reference) == len("REL-") + 8
    assert result.error_code is None
    assert result.already_processed is False


def test_provider_idempotent_replay(provider: MockPaymentProvider) -> None:
    _hold(provider, 400.0)
    first = provider.release_limit(
        transaction_id=TXN,
        amount=400.0,
        currency="BDT",
        idempotency_key="key-replay",
    )
    assert first.success is True
    ledger_after_first = provider.get_ledger_entry(TXN)
    available_after_first = provider.available_limit

    second = provider.release_limit(
        transaction_id=TXN,
        amount=400.0,
        currency="BDT",
        idempotency_key="key-replay",
    )
    # same key -> original result replayed with already_processed=True
    assert second.already_processed is True
    assert second.provider_reference == first.provider_reference
    assert second.success is True
    # exactly ONE ledger change across both calls
    assert provider.get_ledger_entry(TXN) == ledger_after_first
    assert provider.available_limit == available_after_first


def test_timeout_injection_fails_safely(provider: MockPaymentProvider) -> None:
    _hold(provider, 250.0)
    provider.set_failure("TIMEOUT")
    result = provider.release_limit(
        transaction_id=TXN,
        amount=250.0,
        currency="BDT",
        idempotency_key="key-timeout",
    )
    assert result.success is False
    assert result.error_code == "PROVIDER_TIMEOUT"
    assert result.provider_reference is None
    # no ledger change
    entry = provider.get_ledger_entry(TXN)
    assert entry["released_amount"] == 0.0
    assert entry["provider_reference"] is None

    # one-shot: auto-clears, next call succeeds
    retry = provider.release_limit(
        transaction_id=TXN,
        amount=250.0,
        currency="BDT",
        idempotency_key="key-timeout-retry",
    )
    assert retry.success is True
    assert retry.error_code is None
    assert provider.get_ledger_entry(TXN)["released_amount"] == 250.0


def test_error_injection(provider: MockPaymentProvider) -> None:
    _hold(provider, 150.0)
    provider.set_failure("ERROR")
    result = provider.release_limit(
        transaction_id=TXN,
        amount=150.0,
        currency="BDT",
        idempotency_key="key-error",
    )
    assert result.success is False
    assert result.error_code == "PROVIDER_ERROR"
    assert result.error_message is not None
    # no ledger change
    assert provider.get_ledger_entry(TXN)["released_amount"] == 0.0

    # after a failed attempt the SAME key may be retried — the key was never
    # recorded because no success happened
    retry = provider.release_limit(
        transaction_id=TXN,
        amount=150.0,
        currency="BDT",
        idempotency_key="key-error",
    )
    assert retry.success is True
    assert retry.already_processed is False


def test_factory_returns_mock() -> None:
    settings = Settings(payment_provider="mock", _env_file=None)
    provider = get_payment_provider(settings)
    assert isinstance(provider, PaymentProvider)
    assert isinstance(provider, MockPaymentProvider)
    # factory is a singleton — same instance on repeat calls
    assert get_payment_provider(settings) is provider

    with pytest.raises(ValueError):
        get_payment_provider(Settings(payment_provider="stripe", _env_file=None))

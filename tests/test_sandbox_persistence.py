"""tests/test_sandbox_persistence.py — Stage 9 write-through sandbox ledger.

The in-memory mock remains the LIVE state; the store is a write-through
mirror that a fresh provider instance can restore from (restart semantics).
Persistence failures must never break the sandbox operation itself.
"""

from __future__ import annotations

from api.services.payment_provider import MockPaymentProvider

TXN = "txn-persist-1"


class FakeStore:
    """Records every upsert; load_all returns the last captured state."""

    def __init__(self) -> None:
        self.entries: dict[str, dict] = {}

    def upsert(self, entry: dict) -> None:
        self.entries[entry["transaction_id"]] = dict(entry)

    def load_all(self) -> list[dict]:
        return [dict(v) for v in self.entries.values()]


class RaisingStore:
    def upsert(self, entry: dict) -> None:
        raise RuntimeError("simulated persistence outage")

    def load_all(self) -> list[dict]:
        raise RuntimeError("simulated persistence outage")


def test_hold_and_release_persist_final_state() -> None:
    store = FakeStore()
    provider = MockPaymentProvider(store=store)
    provider.ensure_hold(TXN, 500.0, "BDT")
    assert store.entries[TXN]["held_amount"] == 500.0
    assert store.entries[TXN]["released_amount"] == 0.0
    assert store.entries[TXN]["status"] == "HELD"

    result = provider.release_limit(
        transaction_id=TXN, amount=500.0, currency="BDT",
        idempotency_key="key-persist-1",
    )
    assert result.success is True
    entry = store.entries[TXN]
    assert entry["held_amount"] == 500.0
    assert entry["released_amount"] == 500.0
    assert entry["status"] == "RELEASED"
    assert entry["provider_reference"] == result.provider_reference
    assert entry["currency"] == "BDT"


def test_restart_semantics_restore_from_store() -> None:
    store = FakeStore()
    provider = MockPaymentProvider(store=store)
    provider.ensure_hold(TXN, 400.0, "BDT")
    provider.release_limit(
        transaction_id=TXN, amount=150.0, currency="BDT",
        idempotency_key="key-restart-1",
    )

    # "restart": a NEW provider instance seeded from the same store
    rebooted = MockPaymentProvider(store=store)
    entry = rebooted.get_ledger_entry(TXN)
    assert entry is not None, "held state must survive a provider restart"
    assert entry["held_amount"] == 400.0
    assert entry["released_amount"] == 150.0
    assert rebooted.available_limit == MockPaymentProvider.INITIAL_LIMIT - 400.0

    # a subsequent release on the restored instance moves held -> released
    result = rebooted.release_limit(
        transaction_id=TXN, amount=250.0, currency="BDT",
        idempotency_key="key-restart-2",
    )
    assert result.success is True
    assert rebooted.get_ledger_entry(TXN)["released_amount"] == 400.0


def test_persistence_failure_does_not_break_release() -> None:
    provider = MockPaymentProvider(store=RaisingStore())
    provider.ensure_hold(TXN, 300.0, "BDT")  # store.upsert raises — swallowed
    assert provider.get_ledger_entry(TXN)["held_amount"] == 300.0

    result = provider.release_limit(
        transaction_id=TXN, amount=300.0, currency="BDT",
        idempotency_key="key-outage-1",
    )
    # the sandbox operation still succeeds
    assert result.success is True
    assert result.provider_reference is not None
    assert provider.get_ledger_entry(TXN)["released_amount"] == 300.0


def test_restore_survives_failing_store() -> None:
    # construction must not raise even when the store cannot be read
    provider = MockPaymentProvider(store=RaisingStore())
    assert provider.available_limit == MockPaymentProvider.INITIAL_LIMIT
    assert provider.get_ledger_entry(TXN) is None

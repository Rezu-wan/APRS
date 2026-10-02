"""
api/services/payment_provider.py — Stage 8 payment-provider SEAM + sandbox.

SANDBOX ONLY — no real money movement. The only implementation is
MockPaymentProvider, a deterministic in-memory simulator whose result fields
(`provider="mock"`) and docstrings make the simulation explicit at every
step. Nothing in this module may use wording implying real funds; real
providers are a post-hackathon concern — the PaymentProvider ABC is the
contract they would have to satisfy.

The ledger is a module-level singleton keyed by transaction_id. It resets on
process restart — that is documented sandbox behavior, not a bug: a real
provider's ledger is durable on their side; here, durability ends with the
process. Persistence of what happened lives in recovery_actions (DB), not in
the mock.

Thread-safety: every ledger mutation and read runs under a module-level
threading.Lock (cheap, correct — the FastAPI app may serve concurrent
requests and tests may exercise the provider from multiple threads).
"""

from __future__ import annotations

import dataclasses
import secrets
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

from api.core.config import Settings, get_settings

# failure-injection modes for tests (set via MockPaymentProvider.set_failure)
FAILURE_MODES = (None, "TIMEOUT", "ERROR")

OPERATION_RELEASE_LIMIT = "RELEASE_LIMIT"


@dataclass(frozen=True)
class ProviderResult:
    """Outcome of one sandbox provider operation. Frozen — replayed results
    (provider-level idempotency) must be immutable snapshots of the original."""

    success: bool
    provider: str  # always "mock" in Stage 8 — simulation made explicit
    operation: str  # "RELEASE_LIMIT"
    provider_reference: str | None  # "REL-<8hex>" on success
    amount: float
    currency: str
    error_code: str | None = None  # PROVIDER_TIMEOUT | PROVIDER_ERROR
    error_message: str | None = None
    already_processed: bool = False  # provider-level idempotent replay


class PaymentProvider(ABC):
    """The seam README promised: swap sandbox for a real provider without
    touching the executor/verifier code."""

    @abstractmethod
    def ensure_hold(self, transaction_id: str, amount: float, currency: str) -> None:
        """Idempotently place (or confirm an existing) hold on the simulated
        limit. Must be safe to call multiple times per transaction."""

    @abstractmethod
    def release_limit(
        self,
        *,
        transaction_id: str,
        amount: float,
        currency: str,
        idempotency_key: str,
    ) -> ProviderResult:
        """Idempotently release up to `amount` from the held funds."""

    @abstractmethod
    def get_ledger_entry(self, transaction_id: str) -> dict | None:
        """REAL simulated state for the verifier: held/released/reference."""

    @abstractmethod
    def reset(self) -> None:
        """Test hook — wipe the sandbox ledger, keys, and failure injection."""


class MockPaymentProvider(PaymentProvider):
    """Deterministic in-memory sandbox. Simulated limit: 10,000.00 BDT."""

    INITIAL_LIMIT = 10_000.00

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    # -- test hooks -------------------------------------------------------

    def set_failure(self, mode: str | None) -> None:
        """Arm a ONE-SHOT failure for the next release_limit call, then the
        mode auto-clears. mode in (None, "TIMEOUT", "ERROR")."""
        if mode not in FAILURE_MODES:
            raise ValueError(f"unknown failure mode: {mode!r}")
        with self._lock:
            self._failure_mode = mode

    def reset(self) -> None:
        with self._lock:
            self._ledger: dict[str, dict] = {}
            self._processed_keys: dict[str, ProviderResult] = {}
            self._available_limit = self.INITIAL_LIMIT
            self._failure_mode: str | None = None

    # -- provider operations ----------------------------------------------

    def ensure_hold(self, transaction_id: str, amount: float, currency: str) -> None:
        """Idempotent: a hold is recorded (and the simulated available limit
        reduced) only on the FIRST call per transaction; repeat calls are
        no-ops. Raises ValueError if the sandbox limit is exhausted."""
        with self._lock:
            entry = self._ledger.get(transaction_id)
            if entry is not None:
                return  # already held — idempotent replay
            if amount > self._available_limit:
                raise ValueError(
                    f"sandbox limit exhausted: need {amount}, "
                    f"available {self._available_limit}"
                )
            self._available_limit -= amount
            self._ledger[transaction_id] = {
                "held_amount": amount,
                "released_amount": 0.0,
                "provider_reference": None,
                "status": "HELD",
                "currency": currency,
            }

    def release_limit(
        self,
        *,
        transaction_id: str,
        amount: float,
        currency: str,
        idempotency_key: str,
    ) -> ProviderResult:
        with self._lock:
            # (a) provider-level idempotency: same key → replay the ORIGINAL
            # result flagged already_processed. This is the crash-between-
            # provider-and-db safety net: the sandbox ledger is never
            # double-released for one idempotency key.
            original = self._processed_keys.get(idempotency_key)
            if original is not None:
                return dataclasses.replace(original, already_processed=True)

            # (b) one-shot failure injection — no ledger change, auto-clears
            if self._failure_mode is not None:
                code = (
                    "PROVIDER_TIMEOUT"
                    if self._failure_mode == "TIMEOUT"
                    else "PROVIDER_ERROR"
                )
                message = (
                    f"simulated provider {self._failure_mode.lower()} "
                    "(sandbox failure injection)"
                )
                self._failure_mode = None  # one-shot
                return ProviderResult(
                    success=False,
                    provider="mock",
                    operation=OPERATION_RELEASE_LIMIT,
                    provider_reference=None,
                    amount=amount,
                    currency=currency,
                    error_code=code,
                    error_message=message,
                )

            # (c) success — actually move held→released (spec §52: the
            # verifier must be able to trust get_ledger_entry as REAL state)
            entry = self._ledger.get(transaction_id)
            if entry is None:
                return ProviderResult(
                    success=False,
                    provider="mock",
                    operation=OPERATION_RELEASE_LIMIT,
                    provider_reference=None,
                    amount=amount,
                    currency=currency,
                    error_code="PROVIDER_ERROR",
                    error_message="no hold exists for transaction (sandbox)",
                )

            reference = f"REL-{secrets.token_hex(4)}"
            released = min(amount, entry["held_amount"] - entry["released_amount"])
            entry["released_amount"] += released
            self._available_limit += released
            entry["provider_reference"] = reference
            if entry["released_amount"] >= entry["held_amount"]:
                entry["status"] = "RELEASED"

            result = ProviderResult(
                success=True,
                provider="mock",
                operation=OPERATION_RELEASE_LIMIT,
                provider_reference=reference,
                amount=amount,
                currency=currency,
            )
            self._processed_keys[idempotency_key] = result
            return result

    def get_ledger_entry(self, transaction_id: str) -> dict | None:
        with self._lock:
            entry = self._ledger.get(transaction_id)
            return dict(entry) if entry is not None else None

    @property
    def available_limit(self) -> float:
        with self._lock:
            return self._available_limit


# Module-level singleton — sandbox behavior: the ledger resets on process
# restart. Callers use get_payment_provider(); tests may reach the singleton
# to reset/inject failures.
_provider: MockPaymentProvider | None = None
_provider_lock = threading.Lock()


def get_payment_provider(settings: Settings | None = None) -> PaymentProvider:
    """Factory keyed on settings.payment_provider. Only "mock" exists in
    Stage 8; unknown names fail fast rather than silently simulating."""
    global _provider
    chosen = (settings or get_settings()).payment_provider
    if chosen != "mock":
        raise ValueError(
            f"unsupported payment_provider {chosen!r}: Stage 8 is sandbox-only "
            "('mock' is the only implementation)"
        )
    with _provider_lock:
        if _provider is None:
            _provider = MockPaymentProvider()
        return _provider

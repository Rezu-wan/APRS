"""
api/services/payment_provider.py — Stage 8 payment-provider SEAM + sandbox.

SANDBOX ONLY — no real money movement. The only implementation is
MockPaymentProvider, a deterministic in-memory simulator whose result fields
(`provider="mock"`) and docstrings make the simulation explicit at every
step. Nothing in this module may use wording implying real funds; real
providers are a post-hackathon concern — the PaymentProvider ABC is the
contract they would have to satisfy.

The ledger is a module-level singleton keyed by transaction_id. Stage 9 adds
write-through persistence (sandbox_ledger_entries + restore at startup), so
the simulated ledger now survives process restarts; the in-memory ledger
remains the live state. Persistence of what happened also lives in
recovery_actions (DB), not only in the mock.

Thread-safety: every ledger mutation and read runs under a module-level
threading.Lock (cheap, correct — the FastAPI app may serve concurrent
requests and tests may exercise the provider from multiple threads).
"""

from __future__ import annotations

import dataclasses
import logging
import secrets
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from api.core.config import Settings, get_settings

# failure-injection modes for tests (set via MockPaymentProvider.set_failure)
FAILURE_MODES = (None, "TIMEOUT", "ERROR")

OPERATION_RELEASE_LIMIT = "RELEASE_LIMIT"

logger = logging.getLogger("payment_recovery.payment_provider")


class SQLAlchemyLedgerStore:
    """Write-through persistence for the SIMULATED sandbox ledger
    (sandbox_ledger_entries table). The in-memory ledger remains the LIVE
    state — this store only survives process restarts so recovery references
    stay verifiable. Still 100% simulated; DB sessions are created lazily
    (one per operation) to keep this importable before the app is up."""

    def load_all(self) -> list[dict]:
        from api.db.database import SessionLocal  # lazy: avoid import cycles

        from api.db.models import SandboxLedgerEntry

        db = SessionLocal()
        try:
            rows = db.query(SandboxLedgerEntry).all()
            return [
                {
                    "transaction_id": r.transaction_id,
                    "held_amount": float(r.held_amount or 0),
                    "released_amount": float(r.released_amount or 0),
                    "currency": r.currency,
                    "provider_reference": r.provider_reference,
                    "status": r.status,
                }
                for r in rows
            ]
        finally:
            db.close()

    def upsert(self, entry: dict) -> None:
        from api.db.database import SessionLocal  # lazy: avoid import cycles

        from api.db.models import SandboxLedgerEntry

        db = SessionLocal()
        try:
            row = db.get(SandboxLedgerEntry, entry["transaction_id"])
            if row is None:
                row = SandboxLedgerEntry(
                    transaction_id=entry["transaction_id"], held_amount=Decimal("0"),
                    released_amount=Decimal("0"),
                )
                db.add(row)
            row.held_amount = Decimal(str(entry.get("held_amount", 0)))
            row.released_amount = Decimal(str(entry.get("released_amount", 0)))
            row.currency = entry.get("currency") or "BDT"
            row.provider_reference = entry.get("provider_reference")
            row.status = entry.get("status")
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


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

    def __init__(self, store=None) -> None:
        """`store` is optional write-through persistence: an object with
        load_all() -> list[dict] and upsert(entry: dict) -> None. None (the
        default, used by unit tests) keeps the pure in-memory sandbox."""
        self._store = store
        self._lock = threading.Lock()
        self.reset()
        if store is not None:
            self.restore_from(store)

    # -- test hooks -------------------------------------------------------

    def set_failure(self, mode: str | None) -> None:
        """Arm a ONE-SHOT failure for the next release_limit call, then the
        mode auto-clears. mode in (None, "TIMEOUT", "ERROR")."""
        if mode not in FAILURE_MODES:
            raise ValueError(f"unknown failure mode: {mode!r}")
        with self._lock:
            self._failure_mode = mode

    def reset(self) -> None:
        """Wipe the in-memory sandbox state ONLY — persisted store rows are
        untouched (the /sandbox/reset route owns deleting those)."""
        with self._lock:
            self._ledger: dict[str, dict] = {}
            self._processed_keys: dict[str, ProviderResult] = {}
            self._available_limit = self.INITIAL_LIMIT
            self._failure_mode: str | None = None

    # -- write-through persistence ----------------------------------------

    def _persist(self, entry: dict) -> None:
        """Best-effort write-through: a persistence failure is logged and
        swallowed — the sandbox operation itself must never break."""
        if self._store is None:
            return
        try:
            self._store.upsert(entry)
        except Exception:  # noqa: BLE001 — persistence must not break sandbox
            logger.warning(
                "sandbox ledger persistence failed for %s (non-fatal)",
                entry.get("transaction_id"), exc_info=True,
            )

    def restore_from(self, store) -> None:
        """Seed the in-memory ledger from a persistence store (startup
        restore / restart semantics). Held amounts are restored as-is;
        available_limit = INITIAL - sum(held - released), mirroring live
        operation where release_limit returns freed funds to the available
        limit (Stage 10 fix: a restart no longer loses the releases and the
        balance cannot silently drift negative). Entries already in memory
        win — a live hold is never overwritten by a stale persisted row."""
        try:
            entries = store.load_all()
        except Exception:  # noqa: BLE001 — restore failure must not kill startup
            logger.warning("sandbox ledger restore failed (non-fatal)",
                           exc_info=True)
            return
        with self._lock:
            for raw in entries:
                tid = raw.get("transaction_id")
                if not tid or tid in self._ledger:
                    continue
                entry = {
                    "held_amount": float(raw.get("held_amount") or 0),
                    "released_amount": float(raw.get("released_amount") or 0),
                    "provider_reference": raw.get("provider_reference"),
                    "status": raw.get("status") or "HELD",
                    "currency": raw.get("currency") or "BDT",
                }
                self._ledger[tid] = entry
                self._available_limit -= (
                    entry["held_amount"] - entry["released_amount"]
                )

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
            entry = {
                "transaction_id": transaction_id,
                "held_amount": amount,
                "released_amount": 0.0,
                "provider_reference": None,
                "status": "HELD",
                "currency": currency,
            }
            self._ledger[transaction_id] = entry
            self._persist(entry)

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
            self._persist({"transaction_id": transaction_id, **entry})

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

    def ledger_snapshot(self) -> list[dict]:
        """Read accessor for ALL in-memory SIMULATED ledger entries (one dict
        per transaction, copied under the lock). Defined on the mock only,
        NOT the ABC: the factory (get_payment_provider) can only ever return
        a MockPaymentProvider — "mock" is the sole implementation — so the
        demo/sandbox routes can rely on it without widening the provider
        contract real providers would have to satisfy."""
        with self._lock:
            return [dict(entry) for entry in self._ledger.values()]

    @property
    def available_limit(self) -> float:
        with self._lock:
            return self._available_limit


# Module-level singleton — sandbox behavior: the ledger resets on process
# restart. Callers use get_payment_provider(); tests may reach the singleton
# to reset/inject failures.
_provider: MockPaymentProvider | None = None
_provider_lock = threading.Lock()


def restore_sandbox_ledger() -> None:
    """Lifespan startup hook: load persisted sandbox ledger entries into the
    fresh in-memory ledger so holds/references survive process restarts.
    Defensive by contract — never raises, never blocks startup."""
    try:
        provider = get_payment_provider()
        if isinstance(provider, MockPaymentProvider) and provider._store is None:
            provider.restore_from(SQLAlchemyLedgerStore())
        logger.info("sandbox ledger restore complete")
    except Exception:  # noqa: BLE001 — startup must not fail on restore
        logger.warning("sandbox ledger restore skipped", exc_info=True)


def get_payment_provider(settings: Settings | None = None) -> PaymentProvider:
    """Factory keyed on settings.payment_provider. Only "mock" exists in
    Stage 8; unknown names fail fast rather than silently simulating.
    The singleton is constructed ONCE with a SQLAlchemy write-through store
    (Stage 9 restart persistence); direct MockPaymentProvider() construction
    (unit tests) keeps the pure in-memory behavior via store=None."""
    global _provider
    chosen = (settings or get_settings()).payment_provider
    if chosen != "mock":
        raise ValueError(
            f"unsupported payment_provider {chosen!r}: Stage 8 is sandbox-only "
            "('mock' is the only implementation)"
        )
    with _provider_lock:
        if _provider is None:
            _provider = MockPaymentProvider(store=SQLAlchemyLedgerStore())
        return _provider

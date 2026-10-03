"""
api/services/chaos.py — Stage 11 Phase 11F CHAOS & SAFETY runner (SANDBOX ONLY).

Deterministic chaos scenarios that exercise the REAL recovery pipeline —
every scenario that touches money goes through
autonomous_recovery.process_transaction (the no-bypass invariant: no code
path here fabricates a successful recovery). What varies per scenario is the
EVIDENCE SHAPE and the environment:

  GATEWAY_TIMEOUT      gateway-timeout terminal evidence -> auto-recover
  GATEWAY_ERROR        gateway-error terminal evidence   -> auto-recover
  MERCHANT_TIMEOUT     the standard merchant-timeout chain -> auto-recover
  LATE_SETTLEMENT      settlement confirmed AFTER the assessment -> BLOCKED
  DUPLICATE_EVENT      the same batch ingested twice -> one release
  OUT_OF_ORDER_EVENT   events ingested reversed -> same reconstruction
  PROVIDER_TIMEOUT     one-shot provider TIMEOUT injection -> FAILED/BLOCKED
  PROVIDER_ERROR       one-shot provider ERROR injection  -> FAILED/BLOCKED
  CONCURRENT_RECOVERY  10 threads race process_transaction -> exactly once
  DB_FAILURE_SIMULATION -> SKIP (covered by unit tests; not injectable live)

Rerun safety: every scenario purges ITS OWN fixture first — DB rows for the
scenario transaction (FK-safe child-first order, mirroring
demo_scenarios.purge_demo_rows), the persisted sandbox_ledger_entries row,
and the in-memory provider entry (MockPaymentProvider.purge_transactions,
which also restores the held amount to the simulated limit and drops the
provider-level idempotency replays so a rerun executes fresh).

Determinism: fixed timestamps derived from CHAOS_BASE (no RNG, no wall
clock in the event chains); ids are pinned as CHAOS-<scenario>-1.

Honesty contract: invariants NEVER raise — a violated invariant records
held=False and flips the verdict to FAIL; the endpoint still returns 200
with the honest FAIL result. Provider failure injection is always cleared
in a finally block.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from api.core.exceptions import AppError
from api.db.models import (
    RecoveryActionRecord,
    SandboxLedgerEntry,
    Transaction,
)
from api.schemas.reconstruction import (
    ROOT_CAUSE_MERCHANT_CONFIRMATION_TIMEOUT,
)
from api.services.autonomous_recovery import process_transaction
from api.services.demo_scenarios import (
    build_events,
    build_late_settlement_event,
)
from api.services.payment_event_service import get_payment_events, ingest_events

logger = logging.getLogger("payment_recovery.chaos")

CHAOS_TRANSACTION_PREFIX = "CHAOS-"

# Deterministic domain-time base for all chaos event chains (fixed, UTC —
# never datetime.now: reruns must produce identical evidence).
CHAOS_BASE = datetime(2026, 10, 3, 13, 0, 0, tzinfo=timezone.utc)

# Scenario catalog order also fixes each scenario's deterministic start
# offset (one minute apart, so chains can never collide on timestamps).
SCENARIO_ORDER = (
    "GATEWAY_TIMEOUT",
    "GATEWAY_ERROR",
    "MERCHANT_TIMEOUT",
    "LATE_SETTLEMENT",
    "DUPLICATE_EVENT",
    "OUT_OF_ORDER_EVENT",
    "PROVIDER_TIMEOUT",
    "PROVIDER_ERROR",
    "CONCURRENT_RECOVERY",
    "DB_FAILURE_SIMULATION",
)

CATALOG: dict[str, dict] = {
    "GATEWAY_TIMEOUT": {
        "description": "A genuine failure whose terminal evidence is a "
        "gateway TIMEOUT — the engine must auto-recover exactly once.",
        "expected": {"decision": "AUTO_RECOVERED", "status": "VERIFIED"},
    },
    "GATEWAY_ERROR": {
        "description": "A genuine failure whose terminal evidence is a "
        "gateway ERROR — the engine must auto-recover exactly once.",
        "expected": {"decision": "AUTO_RECOVERED", "status": "VERIFIED"},
    },
    "MERCHANT_TIMEOUT": {
        "description": "The standard merchant-confirmation-timeout chain — "
        "the canonical auto-recovery path.",
        "expected": {"decision": "AUTO_RECOVERED", "status": "VERIFIED"},
    },
    "LATE_SETTLEMENT": {
        "description": "A settlement confirmation ingested AFTER the risk "
        "assessment — the fresh-evidence safety gate must block the release.",
        "expected": {"decision": "RECOVERY_BLOCKED", "status": "BLOCKED"},
    },
    "DUPLICATE_EVENT": {
        "description": "The same event batch ingested twice — duplicates are "
        "counted, not re-inserted, and the recovery releases exactly once.",
        "expected": {"decision": "AUTO_RECOVERED", "status": "VERIFIED"},
    },
    "OUT_OF_ORDER_EVENT": {
        "description": "The merchant-timeout chain ingested in REVERSED "
        "order — reconstruction is order-independent and must match the "
        "in-order reconstruction.",
        "expected": {"decision": "AUTO_RECOVERED", "status": "VERIFIED"},
    },
    "PROVIDER_TIMEOUT": {
        "description": "One-shot simulated provider TIMEOUT at release time "
        "— the executor records a FAILED row, nothing is released.",
        "expected": {"decision": "RECOVERY_BLOCKED", "status": "FAILED"},
    },
    "PROVIDER_ERROR": {
        "description": "One-shot simulated provider ERROR at release time — "
        "the executor records a FAILED row, nothing is released.",
        "expected": {"decision": "RECOVERY_BLOCKED", "status": "FAILED"},
    },
    "CONCURRENT_RECOVERY": {
        "description": "10 threads race process_transaction on one "
        "transaction through the shared provider — exactly one release, "
        "one provider reference, one non-replay decision.",
        "expected": {"decision": "AUTO_RECOVERED", "status": "VERIFIED"},
    },
    "DB_FAILURE_SIMULATION": {
        "description": "Database failure during recovery — not injectable "
        "through the live service; covered by unit tests.",
        "expected": {"verdict": "SKIP"},
    },
}

# Layered block codes accepted for the LATE_SETTLEMENT scenario (the exact
# set the Stage 8 E2E established for this shape).
_LATE_SETTLEMENT_BLOCK_CODES = (
    "NEW_SUCCESSFUL_SETTLEMENT",
    "ALREADY_SUCCESS",
    "INSUFFICIENT_EVIDENCE",
    "NOT_ELIGIBLE",
    "RISK_NO_LONGER_PERMITS",
)

# Transaction fixture copied from demo_scenarios._CLEAN_FIXTURE (kept local
# so chaos does not depend on a demo-private constant).
_CHAOS_FIXTURE = {
    "user_id": "USER-CHAOS",
    "merchant_id": "MERCHANT-CHAOS",
    "amount": 1200.00,
    "currency": "BDT",
    "gateway_latency_ms": 2800,
    "retry_count": 2,
    "network_quality": "Good",
    "previous_failures": 1,
    "account_age_days": 450,
    "status": "FAILED",
    "failure_reason": "Gateway Error",
}

# Custom chain gaps for the gateway-terminal scenarios (same builder style
# as demo_scenarios._STEPS merchant_timeout: fixed gaps, fixed latencies).
_GATEWAY_CHAIN_GAP_MS = 3_000
_BASE_LATENCY_MS = 120
_TERMINAL_EVENT_MS = 90

# Expected reconstruction for the merchant_timeout chain (order-independent):
# the OUT_OF_ORDER_EVENT scenario asserts its reconstruction equals this.
_MERCHANT_TIMEOUT_EXPECTED = {
    "root_cause": ROOT_CAUSE_MERCHANT_CONFIRMATION_TIMEOUT,
    "BANK_DEBIT": "CONFIRMED",
    "GATEWAY": "CONFIRMED",
    "MERCHANT_CONFIRMATION": "TIMEOUT",
    "SETTLEMENT": "NOT_OBSERVED",
}

# SQLite lock-retry for the threaded scenario (same fingerprints/pattern as
# tests/test_recovery_concurrency_hard.py).
_LOCKED_SUBSTRINGS = ("database is locked", "database table is locked")
_LOCK_RETRIES = 8
_THREADS = 10


class ChaosScenarioNotFoundError(AppError):
    """Unknown chaos scenario key (422-style: the payload named nothing the
    catalog knows; the message lists the valid catalog)."""

    status_code = 422
    code = "CHAOS_UNKNOWN_SCENARIO"


@dataclass
class ChaosInvariant:
    name: str
    held: bool
    detail: str


@dataclass
class ChaosOutcome:
    decision: str | None
    status: str | None
    recovery_id: str | None
    provider_reference: str | None
    blocked_reason: str | None


@dataclass
class ChaosRunResult:
    scenario: str
    transaction_id: str | None
    verdict: str  # PASS | FAIL | SKIP
    outcome: ChaosOutcome | None
    invariants: list[ChaosInvariant] = field(default_factory=list)
    note: str | None = None


def scenario_transaction_id(scenario: str) -> str:
    return f"{CHAOS_TRANSACTION_PREFIX}{scenario}-1"


def scenario_start(scenario: str) -> datetime:
    """Deterministic domain-time base for one scenario's event chain."""
    return CHAOS_BASE + timedelta(
        minutes=SCENARIO_ORDER.index(scenario)
    )


# --------------------------------------------------------------------------
# purge / cleanup (rerun safety)
# --------------------------------------------------------------------------

def _purge_fixture(db: Session, provider, transaction_id: str) -> None:
    """Purge ONE scenario fixture: DB child rows + transaction, the persisted
    sandbox_ledger_entries row, and the in-memory provider entry (which also
    restores the simulated limit and drops idempotency replays)."""
    from api.db.models import (
        AIExplanation,
        DigitalTwinEvent,
        PaymentEvent,
        RecoveryActionRecord,
        RecoveryDecision,
        RiskAssessmentRecord,
    )

    for model in (
        AIExplanation,
        RiskAssessmentRecord,
        RecoveryActionRecord,
        RecoveryDecision,
        DigitalTwinEvent,
        PaymentEvent,
    ):
        db.query(model).filter(
            model.transaction_id == transaction_id
        ).delete(synchronize_session=False)
    db.query(Transaction).filter(
        Transaction.transaction_id == transaction_id
    ).delete(synchronize_session=False)
    db.query(SandboxLedgerEntry).filter(
        SandboxLedgerEntry.transaction_id == transaction_id
    ).delete(synchronize_session=False)
    db.commit()
    provider.purge_transactions([transaction_id])


def purge_chaos_rows(db: Session, provider, prefix: str = CHAOS_TRANSACTION_PREFIX) -> int:
    """Purge ALL chaos fixtures for a prefix (full-cycle cleanup used by the
    available_limit restoration test). Returns the number of transactions
    deleted from the DB."""
    from api.db.models import (
        AIExplanation,
        DigitalTwinEvent,
        PaymentEvent,
        RecoveryActionRecord,
        RecoveryDecision,
        RiskAssessmentRecord,
    )

    deleted = 0
    for model in (
        AIExplanation,
        RiskAssessmentRecord,
        RecoveryActionRecord,
        RecoveryDecision,
        DigitalTwinEvent,
        PaymentEvent,
    ):
        deleted += (
            db.query(model)
            .filter(model.transaction_id.like(f"{prefix}%"))
            .delete(synchronize_session=False)
        )
    transactions_deleted = (
        db.query(Transaction)
        .filter(Transaction.transaction_id.like(f"{prefix}%"))
        .delete(synchronize_session=False)
    )
    db.query(SandboxLedgerEntry).filter(
        SandboxLedgerEntry.transaction_id.like(f"{prefix}%")
    ).delete(synchronize_session=False)
    db.commit()
    provider.purge_transactions(
        [
            e["transaction_id"]
            for e in provider.ledger_snapshot()
            if str(e.get("transaction_id", "")).startswith(prefix)
        ]
    )
    logger.info("chaos purge: transactions=%d child_rows=%d",
                transactions_deleted, deleted)
    return transactions_deleted


# --------------------------------------------------------------------------
# fixture builders (real ingestion only)
# --------------------------------------------------------------------------


def _build_gateway_chain(tid: str, start: datetime,
                         terminal: str = "GATEWAY_TIMEOUT") -> list[dict]:
    """Deterministic debit -> gateway-request -> gateway-terminal chain (same
    event-shape conventions as demo_scenarios.build_events)."""
    from api.core.payment_lifecycle import EVENT_TYPE_INFO

    chain = (
        "CUSTOMER_DEBIT_CONFIRMED",
        "GATEWAY_REQUEST_SENT",
        terminal,
    )
    events: list[dict] = []
    elapsed_ms = 0.0
    for seq, event_type in enumerate(chain):
        info = EVENT_TYPE_INFO[event_type]
        latency_ms = (
            _TERMINAL_EVENT_MS if info["outcome"] in ("TIMEOUT", "ERROR")
            else _BASE_LATENCY_MS + seq * 40
        )
        if seq > 0:
            elapsed_ms += _GATEWAY_CHAIN_GAP_MS
        elapsed_ms += latency_ms
        events.append({
            "provider_event_id": f"{tid}-{event_type}-{seq:03d}",
            "event_type": event_type,
            "source": info["source"],
            "status": info["outcome"],
            "event_timestamp": _fmt_ts(start + timedelta(milliseconds=elapsed_ms)),
            "reference_id": f"{tid}-{info['stage'].lower()}-ref",
            "latency_ms": latency_ms,
        })
    return events


def _fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _create_transaction(db: Session, tid: str) -> None:
    """Create the fixture transaction through the REAL service (create-or-
    adopt; commits, like the production route)."""
    from api.core.config import get_settings
    from api.schemas.transaction import TransactionEventRequest
    from api.services.ml_service import get_ml_service
    from api.services.transaction_service import get_transaction, record_event

    if get_transaction(db, tid) is not None:
        return
    payload = TransactionEventRequest(transaction_id=tid, **_CHAOS_FIXTURE)
    record_event(db, payload, get_ml_service(), get_settings())


def _ingest(db: Session, tid: str, events: list[dict]) -> dict:
    """REAL ingestion — duplicate-safe per provider_event_id."""
    from api.schemas.payment_events import PaymentEventIn

    return ingest_events(db, tid, [PaymentEventIn(**e) for e in events])


def _prepare_chain(db: Session, ml, tid: str, events: list[dict]) -> None:
    """tx create + evidence ingest + risk assessment — the same REAL-service
    steps as demo_scenarios.prepare_scenario, minus the process call."""
    from api.services.risk_engine import (
        persist_assessment,
        record_anomaly_classified,
        run_assessment,
    )
    from api.services.transaction_service import get_transaction

    _create_transaction(db, tid)
    _ingest(db, tid, events)

    tx = get_transaction(db, tid)
    assessment, fingerprint, reused = run_assessment(
        db, tx, ml, customer_reported_failure=False
    )
    if not reused:
        persist_assessment(db, tx, assessment, fingerprint)
        record_anomaly_classified(db, tx, assessment, fingerprint)
        db.commit()


# --------------------------------------------------------------------------
# invariant helpers
# --------------------------------------------------------------------------


def _inv(name: str, held: bool, detail: str) -> ChaosInvariant:
    return ChaosInvariant(name=name, held=held, detail=detail)


def _rows_for(db: Session, tid: str) -> list[RecoveryActionRecord]:
    return (
        db.query(RecoveryActionRecord)
        .filter(RecoveryActionRecord.transaction_id == tid)
        .order_by(RecoveryActionRecord.id)
        .all()
    )


def _fresh_tx(db: Session, tid: str) -> Transaction | None:
    db.expire_all()  # see COMMITTED state, not this session's cached writes
    return (
        db.query(Transaction)
        .filter(Transaction.transaction_id == tid)
        .one_or_none()
    )


def _outcome_of(response: dict) -> ChaosOutcome:
    return ChaosOutcome(
        decision=response.get("decision"),
        status=response.get("status"),
        recovery_id=response.get("recovery_id"),
        provider_reference=response.get("provider_reference"),
        blocked_reason=None,
    )


def _blocked_reason(db: Session, tid: str) -> str | None:
    rows = _rows_for(db, tid)
    return rows[-1].blocked_reason if rows else None


def _release_invariants(db: Session, provider, tid: str,
                        response: dict) -> list[ChaosInvariant]:
    """The shared exactly-once auto-recovery invariants."""
    inv: list[ChaosInvariant] = []
    rows = _rows_for(db, tid)
    inv.append(_inv(
        "one_recovery_row", len(rows) == 1,
        f"{len(rows)} recovery rows for {tid}",
    ))
    inv.append(_inv(
        "provider_reference_present",
        bool(response.get("provider_reference")),
        f"provider_reference={response.get('provider_reference')!r}",
    ))
    entry = provider.get_ledger_entry(tid)
    released_ok = bool(entry) and float(
        entry.get("released_amount") or 0
    ) == float(entry.get("held_amount") or 0) > 0
    inv.append(_inv(
        "ledger_released_once", released_ok,
        f"ledger entry {entry}",
    ))
    tx = _fresh_tx(db, tid)
    inv.append(_inv(
        "tx_limit_released",
        tx is not None and tx.current_state == "LIMIT_RELEASED",
        f"tx state {tx.current_state if tx else None!r}",
    ))
    return inv


def _not_released_invariants(db: Session, provider, tid: str,
                             response: dict) -> list[ChaosInvariant]:
    """The shared nothing-moved invariants for blocked/failed outcomes."""
    inv: list[ChaosInvariant] = []
    rows = _rows_for(db, tid)
    inv.append(_inv(
        "provider_reference_null_on_row",
        bool(rows) and all(r.provider_reference is None for r in rows),
        f"row references {[r.provider_reference for r in rows]}",
    ))
    tx = _fresh_tx(db, tid)
    inv.append(_inv(
        "tx_never_limit_released",
        tx is None or tx.current_state != "LIMIT_RELEASED",
        f"tx state {tx.current_state if tx else None!r}",
    ))
    entry = provider.get_ledger_entry(tid)
    inv.append(_inv(
        "ledger_not_released",
        entry is None or float(entry.get("released_amount") or 0) == 0.0,
        f"ledger entry {entry}",
    ))
    inv.append(_inv(
        "decision_blocked",
        response.get("decision") == "RECOVERY_BLOCKED",
        f"decision={response.get('decision')!r} status={response.get('status')!r}",
    ))
    return inv


# --------------------------------------------------------------------------
# scenario implementations (each returns (outcome, invariants))
# --------------------------------------------------------------------------


def _run_gateway_terminal(db: Session, ml, provider, scenario: str,
                          tid: str, start: datetime):
    events = _build_gateway_chain(
        tid, start,
        terminal="GATEWAY_TIMEOUT" if scenario == "GATEWAY_TIMEOUT"
        else "GATEWAY_ERROR",
    )
    _prepare_chain(db, ml, tid, events)
    response = process_transaction(db, ml, provider, tid)
    db.commit()
    return _outcome_of(response), _release_invariants(db, provider, tid, response)


def _run_merchant_timeout(db: Session, ml, provider, tid: str, start: datetime):
    _prepare_chain(db, ml, tid, build_events(tid, "merchant_timeout", start))
    response = process_transaction(db, ml, provider, tid)
    db.commit()
    return _outcome_of(response), _release_invariants(db, provider, tid, response)


def _run_late_settlement(db: Session, ml, provider, tid: str, start: datetime):
    _prepare_chain(db, ml, tid, build_events(tid, "merchant_timeout", start))
    # the race: the settlement confirmation lands AFTER the assessment —
    # ingested through the REAL ingestion service
    ingest_result = _ingest(
        db, tid, [build_late_settlement_event(tid, start)]
    )
    response = process_transaction(db, ml, provider, tid)
    db.commit()
    inv = [
        _inv(
            "late_settlement_ingested",
            ingest_result.get("created") == 1,
            f"ingest result {ingest_result}",
        ),
        _inv(
            "blocked_by_fresh_evidence_gate",
            response.get("decision") == "RECOVERY_BLOCKED"
            and _blocked_reason(db, tid) in _LATE_SETTLEMENT_BLOCK_CODES,
            f"decision={response.get('decision')!r} "
            f"blocked_reason={_blocked_reason(db, tid)!r}",
        ),
    ]
    inv.extend(_not_released_invariants(db, provider, tid, response))
    return _outcome_of(response), inv


def _run_duplicate_event(db: Session, ml, provider, tid: str, start: datetime):
    events = build_events(tid, "merchant_timeout", start)
    _create_transaction(db, tid)
    first = _ingest(db, tid, events)
    second = _ingest(db, tid, events)  # the SAME batch, again
    response = process_transaction(db, ml, provider, tid)
    db.commit()
    inv = [
        _inv(
            "second_ingest_all_duplicates",
            first.get("created") == len(events)
            and second.get("created") == 0
            and second.get("duplicates") == len(events),
            f"first={first} second={second}",
        ),
    ]
    inv.extend(_release_invariants(db, provider, tid, response))
    return _outcome_of(response), inv


def _run_out_of_order(db: Session, ml, provider, tid: str, start: datetime):
    from api.services.event_reconstruction import reconstruct_from_events

    events = build_events(tid, "merchant_timeout", start)
    _create_transaction(db, tid)
    _ingest(db, tid, list(reversed(events)))  # REVERSED arrival order

    stored = get_payment_events(db, tid)
    reconstruction = reconstruct_from_events(tid, stored, datetime.now(timezone.utc))
    inv = [
        _inv(
            "reconstruction_order_independent",
            reconstruction.root_cause == _MERCHANT_TIMEOUT_EXPECTED["root_cause"]
            and reconstruction.customer_debit_status
            == _MERCHANT_TIMEOUT_EXPECTED["BANK_DEBIT"]
            and reconstruction.gateway_status == _MERCHANT_TIMEOUT_EXPECTED["GATEWAY"]
            and reconstruction.merchant_confirmation_status
            == _MERCHANT_TIMEOUT_EXPECTED["MERCHANT_CONFIRMATION"]
            and reconstruction.settlement_status
            == _MERCHANT_TIMEOUT_EXPECTED["SETTLEMENT"],
            f"root_cause={reconstruction.root_cause!r} "
            f"stages=({reconstruction.customer_debit_status}, "
            f"{reconstruction.gateway_status}, "
            f"{reconstruction.merchant_confirmation_status}, "
            f"{reconstruction.settlement_status})",
        ),
    ]
    response = process_transaction(db, ml, provider, tid)
    db.commit()
    inv.extend(_release_invariants(db, provider, tid, response))
    return _outcome_of(response), inv


def _run_provider_failure(db: Session, ml, provider, scenario: str,
                          tid: str, start: datetime):
    mode = "TIMEOUT" if scenario == "PROVIDER_TIMEOUT" else "ERROR"
    _prepare_chain(db, ml, tid, build_events(tid, "merchant_timeout", start))
    response: dict | None = None
    inv: list[ChaosInvariant] = []
    try:
        provider.set_failure(mode)
        response = process_transaction(db, ml, provider, tid)
        db.commit()
        inv.extend(_not_released_invariants(db, provider, tid, response))
        rows = _rows_for(db, tid)
        inv.append(_inv(
            "row_failed_with_provider_code",
            bool(rows) and rows[-1].status == "FAILED"
            and rows[-1].failure_reason == f"PROVIDER_{mode}",
            f"row status={rows[-1].status if rows else None!r} "
            f"failure_reason={rows[-1].failure_reason if rows else None!r}",
        ))
    finally:
        provider.set_failure(None)  # ALWAYS disarm the one-shot injection
    inv.append(_inv(
        "failure_mode_cleared",
        provider.get_ledger_entry(tid) is None
        or float(
            (provider.get_ledger_entry(tid) or {}).get("released_amount") or 0
        ) == 0.0,
        "provider failure injection disarmed in finally",
    ))
    assert response is not None
    return _outcome_of(response), inv


def _run_concurrent_recovery(db: Session, ml, provider, tid: str,
                             start: datetime):
    from api.db.database import SessionLocal

    _prepare_chain(db, ml, tid, build_events(tid, "merchant_timeout", start))
    db.commit()  # release this session's write lock before the race
    db.rollback()

    barrier = threading.Barrier(_THREADS)
    responses: list[dict] = []
    errors: list[str] = []

    def _worker() -> None:
        session = SessionLocal()
        try:
            barrier.wait(timeout=15)
            local_response = None
            for attempt in range(_LOCK_RETRIES):
                try:
                    local_response = process_transaction(
                        session, ml, provider, tid
                    )
                    session.commit()
                    break
                except Exception as exc:  # noqa: BLE001 — collected below
                    session.rollback()
                    if not any(s in str(exc).lower() for s in _LOCKED_SUBSTRINGS):
                        raise
                    time.sleep(0.1 * (attempt + 1))
            if local_response is None:
                errors.append("locked after retries")
            else:
                responses.append(local_response)
        except Exception as exc:  # noqa: BLE001 — collected for assertion
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            session.close()

    threads = [threading.Thread(target=_worker) for _ in range(_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    hung = any(t.is_alive() for t in threads)

    # the main session verifies the converged DB state
    rows = _rows_for(db, tid)
    tx = _fresh_tx(db, tid)
    entry = provider.get_ledger_entry(tid)

    with_ref = [r for r in rows if r.provider_reference]
    response_decisions = [r.get("decision") for r in responses]
    inv = [
        _inv(
            "no_thread_errors", not errors and not hung,
            f"errors={errors} hung={hung}",
        ),
        _inv(
            "exactly_one_provider_reference",
            len(with_ref) == 1,
            f"{len(with_ref)} of {len(rows)} rows carry a reference",
        ),
        _inv(
            "released_exactly_once",
            bool(entry)
            and abs(
                float(entry.get("released_amount") or 0)
                - float(tx.amount if tx else 0)
            ) <= 0.001
            and abs(
                sum(float(r.released_amount or 0) for r in rows)
                - float(tx.amount if tx else 0)
            ) <= 0.001,
            f"ledger released={entry and entry.get('released_amount')} "
            f"rows released sum={sum(float(r.released_amount or 0) for r in rows)} "
            f"tx amount={tx.amount if tx else None}",
        ),
        _inv(
            "exactly_one_non_replay_decision",
            response_decisions.count("AUTO_RECOVERED") == 1,
            f"thread decisions={response_decisions}",
        ),
        _inv(
            "tx_limit_released",
            tx is not None and tx.current_state == "LIMIT_RELEASED",
            f"tx state {tx.current_state if tx else None!r}",
        ),
    ]
    primary = next((r for r in with_ref), rows[-1] if rows else None)
    outcome = ChaosOutcome(
        decision="AUTO_RECOVERED" if with_ref else (
            (responses[0].get("decision") if responses else None)
        ),
        status=primary.status if primary else None,
        recovery_id=primary.recovery_id if primary else None,
        provider_reference=primary.provider_reference if primary else None,
        blocked_reason=None,
    )
    return outcome, inv


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------


def run_chaos_scenario(
    db: Session,
    ml,
    provider,
    scenario: str,
    *,
    start: datetime,
) -> ChaosRunResult:
    """Run ONE chaos scenario end to end. Purge-first (rerun-safe), always
    collects invariants (a violated invariant flips the verdict to FAIL — it
    never raises), always disarms provider failure injection."""
    if scenario not in CATALOG:
        raise ChaosScenarioNotFoundError(
            f"unknown chaos scenario {scenario!r}; valid scenarios: "
            + ", ".join(SCENARIO_ORDER)
        )

    if scenario == "DB_FAILURE_SIMULATION":
        # Honest skip — NOT injectable through the live service without
        # monkeypatching a running session (no engine call is made).
        return ChaosRunResult(
            scenario=scenario,
            transaction_id=None,
            verdict="SKIP",
            outcome=None,
            invariants=[],
            note=(
                "covered by unit tests (tests/test_recovery_concurrency_hard.py "
                "IntegrityError race paths + executor failure-injection units); "
                "cannot be injected through the running API without "
                "monkeypatching a live session"
            ),
        )

    tid = scenario_transaction_id(scenario)
    _purge_fixture(db, provider, tid)

    try:
        if scenario in ("GATEWAY_TIMEOUT", "GATEWAY_ERROR"):
            outcome, inv = _run_gateway_terminal(
                db, ml, provider, scenario, tid, start
            )
        elif scenario == "MERCHANT_TIMEOUT":
            outcome, inv = _run_merchant_timeout(db, ml, provider, tid, start)
        elif scenario == "LATE_SETTLEMENT":
            outcome, inv = _run_late_settlement(db, ml, provider, tid, start)
        elif scenario == "DUPLICATE_EVENT":
            outcome, inv = _run_duplicate_event(db, ml, provider, tid, start)
        elif scenario == "OUT_OF_ORDER_EVENT":
            outcome, inv = _run_out_of_order(db, ml, provider, tid, start)
        elif scenario in ("PROVIDER_TIMEOUT", "PROVIDER_ERROR"):
            outcome, inv = _run_provider_failure(
                db, ml, provider, scenario, tid, start
            )
        else:  # CONCURRENT_RECOVERY
            outcome, inv = _run_concurrent_recovery(
                db, ml, provider, tid, start
            )
        verdict = "PASS" if all(i.held for i in inv) else "FAIL"
        return ChaosRunResult(
            scenario=scenario,
            transaction_id=tid,
            verdict=verdict,
            outcome=outcome,
            invariants=inv,
            note=None,
        )
    except Exception as exc:  # noqa: BLE001 — chaos reports, never 500s
        db.rollback()
        logger.warning("chaos scenario %s crashed: %s", scenario, exc,
                       exc_info=True)
        return ChaosRunResult(
            scenario=scenario,
            transaction_id=tid,
            verdict="FAIL",
            outcome=None,
            invariants=[],
            note=f"{type(exc).__name__}: {exc}",
        )
    finally:
        provider.set_failure(None)

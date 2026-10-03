"""
api/services/recovery_executor.py — Stage 8 EXECUTION slice
(EXECUTOR_VERSION "v1").

The executor is the ONLY module that calls the payment provider. It implements
the pinned pipeline:

    idempotency -> SAFETY GATE (fresh, re-derived HERE) -> provider release
    -> VERIFICATION -> state transition

Safety invariants (spec sections 10 and 19):

  * The safety gate runs INSIDE the executor on freshly reloaded events and a
    freshly rebuilt reconstruction — never on the assessment-time view. A
    settlement that lands between decision and execution blocks the release.
  * A release is NEVER reflected in transaction state (LIMIT_RELEASED)
    without passing verification. A failed verification leaves the row FAILED
    and the transaction state untouched.
  * Idempotency: idempotency_key is UNIQUE at the DB level. Replays of the
    same evidence return the existing row without calling the provider
    (already_recovered=True). A FAILED row may be retried, but only up to
    EXECUTOR_MAX_ATTEMPTS attempts — a business refusal to retry forever (an
    execution that failed 3 times needs human attention, not an infinite
    loop). A concurrent INSERT that loses the UNIQUE race is rolled back and
    the winner's row is returned (same race style as recovery_service /
    payment_event_service).

Transactional convention: every DB write is flushed here; the FINAL COMMIT
is owned by the caller (the route layer), matching the rest of the codebase.

Digital Twin vocabulary used (all OBSERVATIONS except the LIMIT_RELEASED
transition — previous_state == new_state, the state machine is untouched):

    RECOVERY_APPROVED  RECOVERY_STARTED  RECOVERY_EXECUTED
    RECOVERY_VERIFIED  RECOVERY_FAILED   RECOVERY_BLOCKED
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.core.exceptions import (
    ConflictError,
    InvalidStateTransitionError,
)
from api.core.state_machine import TransactionState, validate_transition
from api.db.models import RecoveryActionRecord, RecoveryDecision, Transaction
from api.schemas.recovery_autonomous import (
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_EXECUTING,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_VERIFICATION_PENDING,
    STATUS_VERIFIED,
    RecoveryDecision as AutonomousDecision,
    SafetyGateResult,
    VerificationResult,
)
from api.services.digital_twin import append_event
from api.services.event_reconstruction import reconstruct_from_events
from api.services.metrics import (
    METRICS_PROVIDER_CALLS_TOTAL,
    METRICS_PROVIDER_ERRORS_TOTAL,
    METRICS_SAFETY_GATE_BLOCKS_TOTAL,
    METRICS_VERIFICATION_LATENCY,
    record_counter,
    record_latency,
)
from api.services.payment_event_service import get_payment_events
from api.services.recovery_safety import check_safety
from api.services.recovery_verifier import VERIFIER_VERSION, verify_release
from api.services.risk_engine import assessment_from_record, get_latest_record

logger = logging.getLogger("payment_recovery.recovery_executor")

EXECUTOR_VERSION = "v1"

# Bounded retries: a FAILED row may be re-attempted at most this many times.
EXECUTOR_MAX_ATTEMPTS = 3

# Twin event vocabulary (observations).
EVENT_APPROVED = "RECOVERY_APPROVED"
EVENT_STARTED = "RECOVERY_STARTED"
EVENT_EXECUTED = "RECOVERY_EXECUTED"
EVENT_VERIFIED = "RECOVERY_VERIFIED"
EVENT_FAILED = "RECOVERY_FAILED"
EVENT_BLOCKED = "RECOVERY_BLOCKED"


def compute_idempotency_key(
    transaction_id: str,
    action: str,
    policy_version: str,
    evidence_fingerprint: str,
) -> str:
    """sha256 hex over the recovery's identity: the same transaction + action
    + policy + evidence fingerprint is THE SAME recovery and must never
    execute twice (documented composition, mirrors risk_engine._fingerprint)."""
    payload = "\x1f".join(
        (transaction_id, action, policy_version, evidence_fingerprint)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_row_by_key(db: Session, idempotency_key: str) -> RecoveryActionRecord | None:
    return db.scalars(
        select(RecoveryActionRecord).where(
            RecoveryActionRecord.idempotency_key == idempotency_key
        )
    ).one_or_none()


def _twin_observation(
    db: Session,
    tx: Transaction,
    event_type: str,
    reason: str,
    metadata: dict,
) -> None:
    """Append a recovery OBSERVATION to the twin: previous_state ==
    new_state — the state machine is only ever moved by the explicit,
    validated transition at the very end."""
    append_event(
        db,
        tx,
        previous_state=tx.current_state,
        new_state=tx.current_state,
        event_type=event_type,
        reason=reason,
        event_metadata=metadata,
    )


def _provider_result_json(result) -> dict:
    return {
        "success": result.success,
        "provider": result.provider,
        "operation": result.operation,
        "provider_reference": result.provider_reference,
        "amount": result.amount,
        "currency": result.currency,
        "error_code": result.error_code,
        "error_message": result.error_message,
        "already_processed": result.already_processed,
    }


def _verification_json(result: VerificationResult) -> dict:
    return {
        "passed": result.passed,
        "checks": result.checks,
        "verified_at": result.verified_at.isoformat(),
    }


def _safety_json(result: SafetyGateResult) -> dict:
    return {
        "allowed": result.allowed,
        "blocked_reason": result.blocked_reason,
        "checks": result.checks,
        "checked_at": result.checked_at.isoformat(),
    }


def _other_active_row(
    db: Session, tx: Transaction, exclude_id: int | None
) -> RecoveryActionRecord | None:
    """Another non-BLOCKED recovery row for this transaction (a concurrent
    recovery the safety gate must know about)."""
    stmt = select(RecoveryActionRecord).where(
        RecoveryActionRecord.transaction_id == tx.transaction_id,
        RecoveryActionRecord.status != STATUS_BLOCKED,
    )
    if exclude_id is not None:
        stmt = stmt.where(RecoveryActionRecord.id != exclude_id)
    return db.scalars(stmt.order_by(RecoveryActionRecord.id).limit(1)).one_or_none()


def _run_safety_gate(
    db: Session,
    tx: Transaction,
    existing_row: RecoveryActionRecord | None,
    *,
    now: datetime,
) -> SafetyGateResult:
    """Fresh safety gate INSIDE the executor (spec section 10): reload the
    events, rebuild the reconstruction, take the latest stored assessment —
    nothing trusted from decision time."""
    fresh_events = get_payment_events(db, tx.transaction_id)
    fresh_reconstruction = (
        reconstruct_from_events(tx.transaction_id, fresh_events, now)
        if fresh_events
        else None
    )
    latest_record = get_latest_record(db, tx.transaction_id)
    latest_assessment = (
        assessment_from_record(latest_record) if latest_record else None
    )
    return check_safety(
        tx,
        fresh_events,
        fresh_reconstruction,
        latest_assessment,
        existing_row,
        now=now,
    )


def execute_recovery(
    db: Session,
    tx: Transaction,
    decision: AutonomousDecision,
    assessment,
    evidence_fingerprint: str,
    provider,
    *,
    now: datetime,
) -> tuple[RecoveryActionRecord, dict]:
    """Execute one autonomous recovery end to end. Returns
    (row, info) with info = {"already_recovered": bool,
    "digital_twin_events": [event types appended]}.

    Flushes every write; the CALLER owns the final commit.
    """
    key = compute_idempotency_key(
        tx.transaction_id,
        decision.action,
        decision.policy_version,
        evidence_fingerprint,
    )
    twin_events: list[str] = []

    # ---- (a) idempotent replay ------------------------------------------
    row = get_row_by_key(db, key)
    if row is not None:
        if row.status == STATUS_FAILED and row.attempt_count < EXECUTOR_MAX_ATTEMPTS:
            # bounded retry: reuse the FAILED row for one more attempt (b)
            row.status = STATUS_PENDING
            row.attempt_count += 1
            row.failure_reason = None
            row.provider = None
            row.provider_reference = None
            row.provider_result = None
            row.verification_result = None
            row.started_at = None
            row.completed_at = None
            row.verified_at = None
            row.decision_reason = decision.decision_reason
            row.risk_assessment_id = decision.risk_assessment_id
            db.flush()
            logger.info(
                "recovery retry: transaction_id=%s recovery_id=%s attempt=%d",
                tx.transaction_id, row.recovery_id, row.attempt_count,
            )
        else:
            # same evidence = same recovery: replay, NO provider call, no twin
            # events. Covers done/in-flight rows, BLOCKED rows, and FAILED rows
            # that exhausted EXECUTOR_MAX_ATTEMPTS (business refusal to retry
            # forever — see module docstring).
            logger.info(
                "recovery idempotent replay: transaction_id=%s recovery_id=%s "
                "status=%s",
                tx.transaction_id, row.recovery_id, row.status,
            )
            return row, {"already_recovered": True, "digital_twin_events": []}
    else:
        # ---- (b) create the row; UNIQUE(idempotency_key) is the DB-level
        # race protection against a concurrent winner
        row = RecoveryActionRecord(
            transaction_id=tx.transaction_id,
            action=decision.action,
            status=STATUS_PENDING,
            idempotency_key=key,
            attempt_count=1,
            requested_amount=tx.amount,
            currency=tx.currency,
            policy_version=decision.policy_version,
            executor_version=EXECUTOR_VERSION,
            risk_assessment_id=decision.risk_assessment_id,
            decision_reason=decision.decision_reason,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            # lost the race — the concurrent winner's row is the answer for
            # both callers (rollback discards our partial writes; the caller's
            # remaining work must tolerate a rolled-back session)
            db.rollback()
            winner = get_row_by_key(db, key)
            if winner is None:  # pragma: no cover — winner committed but invisible
                raise ConflictError(
                    f"recovery idempotency race lost for {tx.transaction_id}"
                )
            logger.info(
                "recovery concurrent race lost: transaction_id=%s key=%s",
                tx.transaction_id, key,
            )
            return winner, {"already_recovered": True, "digital_twin_events": []}

    # ---- (c) SAFETY GATE — fresh, inside the executor --------------------
    gate = _run_safety_gate(
        db, tx, _other_active_row(db, tx, row.id), now=now
    )
    if not gate.allowed:
        record_counter(METRICS_SAFETY_GATE_BLOCKS_TOTAL)  # Stage 11G
        row.status = STATUS_BLOCKED
        row.blocked_reason = gate.blocked_reason
        row.decision_reason = (
            f"{row.decision_reason}; safety gate blocked: "
            f"{gate.blocked_reason}"
        )
        row.verification_result = _safety_json(gate)
        _twin_observation(
            db,
            tx,
            EVENT_BLOCKED,
            f"Recovery blocked by safety gate: {gate.blocked_reason}",
            {
                "blocked_reason": gate.blocked_reason,
                "recovery_id": row.recovery_id,
                "policy_version": decision.policy_version,
                "safety_checks": gate.checks,
            },
        )
        twin_events.append(EVENT_BLOCKED)
        db.flush()
        logger.info(
            "recovery blocked by safety gate: transaction_id=%s recovery_id=%s "
            "blocked_reason=%s policy_version=%s risk_assessment_id=%s",
            tx.transaction_id, row.recovery_id, gate.blocked_reason,
            decision.policy_version, decision.risk_assessment_id,
        )
        return row, {
            "already_recovered": False,
            "digital_twin_events": twin_events,
        }

    # ---- (d) APPROVED ----------------------------------------------------
    _twin_observation(
        db,
        tx,
        EVENT_APPROVED,
        f"Recovery approved: {decision.decision_reason}",
        {
            "recovery_id": row.recovery_id,
            "policy_version": decision.policy_version,
            "risk_assessment_id": decision.risk_assessment_id,
        },
    )
    twin_events.append(EVENT_APPROVED)

    row.status = STATUS_EXECUTING
    row.started_at = now
    db.flush()
    _twin_observation(
        db,
        tx,
        EVENT_STARTED,
        "Recovery execution started (sandbox provider)",
        {"recovery_id": row.recovery_id, "attempt": row.attempt_count},
    )
    twin_events.append(EVENT_STARTED)
    logger.info(
        "recovery started: transaction_id=%s recovery_id=%s "
        "policy_version=%s risk_assessment_id=%s",
        tx.transaction_id, row.recovery_id, decision.policy_version,
        decision.risk_assessment_id,
    )

    provider.ensure_hold(str(tx.transaction_id), float(tx.amount), tx.currency)
    record_counter(METRICS_PROVIDER_CALLS_TOTAL)  # Stage 11G
    logger.info(
        "provider called: transaction_id=%s recovery_id=%s operation="
        "RELEASE_LIMIT amount=%s currency=%s",
        tx.transaction_id, row.recovery_id, tx.amount, tx.currency,
    )
    result = provider.release_limit(
        transaction_id=str(tx.transaction_id),
        amount=float(tx.amount),
        currency=tx.currency,
        idempotency_key=key,
    )
    if not result.success:
        record_counter(METRICS_PROVIDER_ERRORS_TOTAL)  # Stage 11G
    logger.info(
        "provider result: transaction_id=%s recovery_id=%s success=%s "
        "error_code=%s provider_reference=%s",
        tx.transaction_id, row.recovery_id, result.success, result.error_code,
        result.provider_reference,
    )

    if not result.success:
        row.status = STATUS_FAILED
        row.failure_reason = result.error_code
        row.provider = result.provider
        row.provider_result = _provider_result_json(result)
        _twin_observation(
            db,
            tx,
            EVENT_FAILED,
            f"Provider release failed: {result.error_code}",
            {
                "recovery_id": row.recovery_id,
                "error_code": result.error_code,
                "attempt": row.attempt_count,
            },
        )
        twin_events.append(EVENT_FAILED)
        db.flush()
        logger.info(
            "recovery failed: transaction_id=%s recovery_id=%s "
            "failure_reason=%s",
            tx.transaction_id, row.recovery_id, result.error_code,
        )
        return row, {
            "already_recovered": False,
            "digital_twin_events": twin_events,
        }

    # ---- (e) provider success — pending verification ---------------------
    row.status = STATUS_VERIFICATION_PENDING
    row.provider = result.provider
    row.provider_reference = result.provider_reference
    row.provider_result = _provider_result_json(result)
    row.released_amount = Decimal(str(result.amount))
    db.flush()
    _twin_observation(
        db,
        tx,
        EVENT_EXECUTED,
        f"Provider released limit (reference {result.provider_reference}) — "
        "awaiting verification",
        {
            "recovery_id": row.recovery_id,
            "provider_reference": result.provider_reference,
            "released_amount": float(result.amount),
        },
    )
    twin_events.append(EVENT_EXECUTED)

    # ---- (f) VERIFICATION — never LIMIT_RELEASED without it (spec §19) ---
    fresh_events = get_payment_events(db, tx.transaction_id)
    ledger_entry = provider.get_ledger_entry(str(tx.transaction_id))
    all_rows = list(
        db.scalars(
            select(RecoveryActionRecord).where(
                RecoveryActionRecord.transaction_id == tx.transaction_id
            )
        )
    )
    _verify_t0 = time.perf_counter()
    verification = verify_release(
        tx,
        row,
        ledger_entry,
        fresh_events,
        released_after=row.started_at,
        all_recovery_rows=all_rows,
        now=now,
    )
    record_latency(
        METRICS_VERIFICATION_LATENCY,
        (time.perf_counter() - _verify_t0) * 1000,
    )  # Stage 11G
    row.verifier_version = VERIFIER_VERSION
    row.verification_result = _verification_json(verification)

    if not verification.passed:
        row.status = STATUS_FAILED
        row.failure_reason = "verification failed"
        _twin_observation(
            db,
            tx,
            EVENT_FAILED,
            "Release verification FAILED — state unchanged",
            {
                "recovery_id": row.recovery_id,
                "verification_checks": verification.checks,
            },
        )
        twin_events.append(EVENT_FAILED)
        db.flush()
        logger.info(
            "recovery verification failed: transaction_id=%s recovery_id=%s",
            tx.transaction_id, row.recovery_id,
        )
        return row, {
            "already_recovered": False,
            "digital_twin_events": twin_events,
        }

    row.status = STATUS_VERIFIED
    row.verified_at = now
    row.completed_at = now
    _twin_observation(
        db,
        tx,
        EVENT_VERIFIED,
        "Release verified against the provider ledger",
        {
            "recovery_id": row.recovery_id,
            "provider_reference": row.provider_reference,
            "verification_checks": verification.checks,
        },
    )
    twin_events.append(EVENT_VERIFIED)
    logger.info(
        "recovery verified: transaction_id=%s recovery_id=%s "
        "provider_reference=%s",
        tx.transaction_id, row.recovery_id, row.provider_reference,
    )

    # transactional state transition — LAST, only after verification passed
    try:
        validate_transition(tx.current_state, TransactionState.LIMIT_RELEASED)
    except InvalidStateTransitionError as exc:
        # safe: the release happened in the sandbox but the lifecycle forbids
        # the transition — surface it as a FAILED row, leave the state alone
        row.status = STATUS_FAILED
        row.failure_reason = f"state transition blocked: {exc.message}"
        _twin_observation(
            db,
            tx,
            EVENT_FAILED,
            row.failure_reason,
            {"recovery_id": row.recovery_id},
        )
        twin_events.append(EVENT_FAILED)
        db.flush()
        return row, {
            "already_recovered": False,
            "digital_twin_events": twin_events,
        }

    previous_state = tx.current_state
    tx.current_state = TransactionState.LIMIT_RELEASED
    append_event(
        db,
        tx,
        previous_state=previous_state,
        new_state=TransactionState.LIMIT_RELEASED,
        reason=f"Autonomous recovery {row.recovery_id} verified",
        event_metadata={
            "recovery_id": row.recovery_id,
            "policy_version": decision.policy_version,
            "executor_version": EXECUTOR_VERSION,
            "simulated": True,
        },
    )
    twin_events.append(TransactionState.LIMIT_RELEASED)

    # Stage-3 decision row so the existing manual endpoint replays
    # idempotently (UNIQUE transaction_id; guarded + race-tolerant)
    _ensure_stage3_decision(db, tx, decision, assessment)
    db.flush()

    return row, {"already_recovered": False, "digital_twin_events": twin_events}


def _ensure_stage3_decision(
    db: Session,
    tx: Transaction,
    decision: AutonomousDecision,
    assessment,
) -> None:
    existing = db.scalars(
        select(RecoveryDecision).where(
            RecoveryDecision.transaction_id == tx.transaction_id
        )
    ).one_or_none()
    if existing is not None:
        return
    row = RecoveryDecision(
        transaction_id=tx.transaction_id,
        decision="LIMIT_RELEASED",
        safe_to_release=bool(getattr(assessment, "recovery_candidate", False)),
        safe_to_release_probability=float(getattr(assessment, "risk_score", 0.0)),
        risk_score=float(getattr(assessment, "risk_score", 0.0)),
        reason=decision.decision_reason,
        decided_by="AUTONOMOUS_ENGINE",
        policy_snapshot={
            "policy_version": decision.policy_version,
            "executor_version": EXECUTOR_VERSION,
            "simulated": True,
        },
    )
    db.add(row)
    # a bare rollback here would discard the completed execution above, so a
    # savepoint contains any UNIQUE(transaction_id) race: the winner's row is
    # the answer for both callers
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        logger.info(
            "stage-3 decision row race lost: transaction_id=%s", tx.transaction_id
        )

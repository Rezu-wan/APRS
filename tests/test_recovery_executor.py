"""Stage 8 executor tests: direct service-level tests of
api/services/recovery_executor.execute_recovery against the real risk engine
and the real sandbox provider.

Seeding style matches test_risk_api.py: the transaction is created through
the normal ingestion endpoint (so the lifecycle state machine chains it to
RECOVERY_PENDING), payment events are inserted through a SessionLocal
session, and the assessment is produced by the real risk_engine.run_assessment.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from api.core.payment_lifecycle import EVENT_TYPE_INFO
from api.db.database import SessionLocal
from api.db.models import (
    DigitalTwinEvent,
    PaymentEvent,
    RecoveryActionRecord,
    Transaction,
)
from api.services.payment_provider import get_payment_provider
from api.services.recovery_decision_policy import decide
from api.services.recovery_executor import (
    EXECUTOR_VERSION,
    compute_idempotency_key,
    execute_recovery,
)
from api.services.event_reconstruction import reconstruct_from_events
from api.services.risk_engine import run_assessment
from api.services.transaction_service import get_transaction

from tests.conftest import CLEAN_FAILED_TX, SYSTEM_KEY, make_event

EVENT_URL = "/api/v1/transaction/event"

_MERCHANT_TIMEOUT_EVENTS = [
    "CUSTOMER_DEBIT_CONFIRMED",
    "GATEWAY_RESPONSE_RECEIVED",
    "MERCHANT_CONFIRMATION_TIMEOUT",
]


def _unique_id() -> str:
    return f"TXN-EXEC-{uuid.uuid4().hex[:12]}"


def _seed_tx(client, tid: str) -> None:
    resp = client.post(
        EVENT_URL, json=make_event(tid, **CLEAN_FAILED_TX), headers=SYSTEM_KEY
    )
    assert resp.status_code == 200


def _seed_events(tid: str, event_types: list[str]) -> None:
    db = SessionLocal()
    try:
        base = datetime.now(timezone.utc) - timedelta(hours=1)
        for i, event_type in enumerate(event_types):
            info = EVENT_TYPE_INFO[event_type]
            db.add(
                PaymentEvent(
                    transaction_id=tid,
                    provider_event_id=f"{tid}-{event_type}-{i}",
                    event_type=event_type,
                    source=info["source"],
                    status=info["outcome"],
                    event_timestamp=base + timedelta(minutes=i),
                )
            )
        db.commit()
    finally:
        db.close()


def _seed_settlement(tid: str) -> None:
    """Insert a SETTLEMENT_CONFIRMED event with a domain timestamp AFTER the
    seeding events (the race window)."""
    db = SessionLocal()
    try:
        info = EVENT_TYPE_INFO["SETTLEMENT_CONFIRMED"]
        db.add(
            PaymentEvent(
                transaction_id=tid,
                provider_event_id=f"{tid}-SETTLEMENT_CONFIRMED-late",
                event_type="SETTLEMENT_CONFIRMED",
                source=info["source"],
                status=info["outcome"],
                event_timestamp=datetime.now(timezone.utc) - timedelta(minutes=1),
            )
        )
        db.commit()
    finally:
        db.close()


def _prepare(client, tid: str, *, events=None):
    """Seed tx + events, run the real assessment, and produce the decision.
    Returns (db, tx, assessment, fingerprint, decision, provider)."""
    _seed_tx(client, tid)
    _seed_events(tid, events or _MERCHANT_TIMEOUT_EVENTS)

    db = SessionLocal()
    tx = get_transaction(db, tid)
    assert tx is not None and tx.current_state == "RECOVERY_PENDING"

    from api.services.ml_service import get_ml_service

    assessment, fingerprint, _reused = run_assessment(
        db, tx, get_ml_service(), customer_reported_failure=False
    )
    # the real flow persists the assessment evidence before executing
    # (risk_assessment route semantics) — the safety gate reads the latest
    # stored record
    from api.services.risk_engine import persist_assessment

    persist_assessment(db, tx, assessment, fingerprint)
    db.commit()
    now = datetime.now(timezone.utc)
    recon_events = list(_db_events(db, tid))
    reconstruction = reconstruct_from_events(tid, recon_events, now)
    decision = decide(tx, assessment, reconstruction, now=now)
    return db, tx, assessment, fingerprint, decision, get_payment_provider()


def _db_events(db, tid):
    from sqlalchemy import select

    from api.db.models import PaymentEvent as PE

    return db.scalars(
        select(PE)
        .where(PE.transaction_id == tid)
        .order_by(PE.event_timestamp.asc(), PE.id.asc())
    )


def _twin_types(db, tid: str) -> list[str]:
    from sqlalchemy import select

    rows = db.scalars(
        select(DigitalTwinEvent).where(DigitalTwinEvent.transaction_id == tid)
    )
    return [r.event_type for r in rows]


def _ledger(provider, tid: str) -> dict | None:
    return provider.get_ledger_entry(tid)


def test_release_limit_end_to_end(client):
    tid = _unique_id()
    db, tx, assessment, fingerprint, decision, provider = _prepare(client, tid)
    assert decision.eligible and decision.action == "RELEASE_LIMIT"

    now = datetime.now(timezone.utc)
    row, info = execute_recovery(
        db, tx, decision, assessment, fingerprint, provider, now=now
    )
    db.commit()

    assert row.status == "VERIFIED"
    assert row.executor_version == EXECUTOR_VERSION
    assert row.verified_at is not None and row.completed_at is not None
    assert row.failure_reason is None
    assert row.provider_reference == _ledger(provider, tid)["provider_reference"]
    assert _ledger(provider, tid)["status"] == "RELEASED"

    db.refresh(tx)
    assert tx.current_state == "LIMIT_RELEASED"

    types = _twin_types(db, tid)
    for event in (
        "RECOVERY_APPROVED",
        "RECOVERY_STARTED",
        "RECOVERY_EXECUTED",
        "RECOVERY_VERIFIED",
        "LIMIT_RELEASED",
    ):
        assert event in types
    db.close()


def test_provider_timeout(client):
    tid = _unique_id()
    db, tx, assessment, fingerprint, decision, provider = _prepare(client, tid)
    provider.set_failure("TIMEOUT")

    row, info = execute_recovery(
        db, tx, decision, assessment, fingerprint, provider,
        now=datetime.now(timezone.utc),
    )
    db.commit()

    assert row.status == "FAILED"
    assert row.failure_reason == "PROVIDER_TIMEOUT"
    assert row.verified_at is None

    db.refresh(tx)
    assert tx.current_state != "LIMIT_RELEASED"
    ledger = _ledger(provider, tid)
    assert ledger is None or ledger["released_amount"] == 0.0

    assert "RECOVERY_FAILED" in _twin_types(db, tid)
    assert "RECOVERY_VERIFIED" not in _twin_types(db, tid)
    db.close()


def test_provider_error(client):
    tid = _unique_id()
    db, tx, assessment, fingerprint, decision, provider = _prepare(client, tid)
    provider.set_failure("ERROR")

    row, _info = execute_recovery(
        db, tx, decision, assessment, fingerprint, provider,
        now=datetime.now(timezone.utc),
    )
    db.commit()

    assert row.status == "FAILED"
    assert row.failure_reason == "PROVIDER_ERROR"
    db.refresh(tx)
    assert tx.current_state != "LIMIT_RELEASED"
    db.close()


def test_idempotent_execution(client):
    tid = _unique_id()
    db, tx, assessment, fingerprint, decision, provider = _prepare(client, tid)
    now = datetime.now(timezone.utc)

    row1, info1 = execute_recovery(
        db, tx, decision, assessment, fingerprint, provider, now=now
    )
    db.commit()
    assert row1.status == "VERIFIED"
    assert info1["already_recovered"] is False

    released_after_first = _ledger(provider, tid)["released_amount"]
    types_after_first = _twin_types(db, tid)

    # second execution with the SAME evidence = same idempotency key
    row2, info2 = execute_recovery(
        db, tx, decision, assessment, fingerprint, provider, now=now
    )
    db.commit()

    assert info2["already_recovered"] is True
    assert row2.recovery_id == row1.recovery_id
    assert row2.status == "VERIFIED"
    assert _ledger(provider, tid)["released_amount"] == released_after_first
    assert _twin_types(db, tid) == types_after_first
    db.close()


def test_duplicate_execution_concurrent(client):
    """A pre-inserted row holding the same idempotency key = the concurrent
    winner: execute_recovery must replay it (no provider call, no error)."""
    tid = _unique_id()
    db, tx, assessment, fingerprint, decision, provider = _prepare(client, tid)
    now = datetime.now(timezone.utc)

    key = compute_idempotency_key(
        tid, decision.action, decision.policy_version, fingerprint
    )
    pre = RecoveryActionRecord(
        transaction_id=tid,
        action=decision.action,
        status="PENDING",
        idempotency_key=key,
        attempt_count=1,
        requested_amount=tx.amount,
        currency=tx.currency,
        policy_version=decision.policy_version,
        decision_reason=decision.decision_reason,
    )
    db.add(pre)
    db.commit()

    row, info = execute_recovery(
        db, tx, decision, assessment, fingerprint, provider, now=now
    )
    db.commit()

    assert info["already_recovered"] is True
    assert row.recovery_id == pre.recovery_id
    assert row.status == "PENDING"  # untouched replay
    ledger = _ledger(provider, tid)
    assert ledger is None or ledger["released_amount"] == 0.0
    db.close()


def test_safety_gate_blocks_after_settlement(client):
    """THE race (spec section 10): the settlement confirms AFTER the
    assessment; the executor re-derives fresh facts and must block with
    NEW_SUCCESSFUL_SETTLEMENT instead of double-paying."""
    tid = _unique_id()
    db, tx, assessment, fingerprint, decision, provider = _prepare(client, tid)
    assert decision.eligible  # the decision was made on stale evidence

    _seed_settlement(tid)  # lands between decision and execution
    db.expire_all()
    tx = get_transaction(db, tid)

    row, info = execute_recovery(
        db, tx, decision, assessment, fingerprint, provider,
        now=datetime.now(timezone.utc),
    )
    db.commit()

    assert info["already_recovered"] is False
    assert row.status == "BLOCKED"
    assert row.blocked_reason == "NEW_SUCCESSFUL_SETTLEMENT"

    db.refresh(tx)
    assert tx.current_state != "LIMIT_RELEASED"
    ledger = _ledger(provider, tid)
    assert ledger is None or ledger["released_amount"] == 0.0
    assert "RECOVERY_BLOCKED" in _twin_types(db, tid)
    assert "RECOVERY_APPROVED" not in _twin_types(db, tid)
    db.close()


def test_failed_row_retries_bounded(client):
    """FAILED rows retry with attempt_count incrementing; after
    EXECUTOR_MAX_ATTEMPTS the same key refuses to retry (bounded retries)."""
    from api.services.recovery_executor import EXECUTOR_MAX_ATTEMPTS

    tid = _unique_id()
    db, tx, assessment, fingerprint, decision, provider = _prepare(client, tid)
    now = datetime.now(timezone.utc)

    row = None
    for _ in range(EXECUTOR_MAX_ATTEMPTS):
        provider.set_failure("ERROR")  # one-shot injection — re-arm per attempt
        row, info = execute_recovery(
            db, tx, decision, assessment, fingerprint, provider, now=now
        )
        db.commit()
        assert info["already_recovered"] is False
        assert row.status == "FAILED"

    attempts_seen = row.attempt_count
    # one more invocation with the same evidence: bounded — replay, not retry
    row, info = execute_recovery(
        db, tx, decision, assessment, fingerprint, provider, now=now
    )
    db.commit()
    assert info["already_recovered"] is True
    assert row.attempt_count == attempts_seen
    db.close()

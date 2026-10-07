"""Innovation demonstration — the fresh-evidence safety gate, end to end.

One deterministic story, told in five ordered steps through the REAL
pipeline (no mocks of business logic — every step calls the production
services exactly as the routes/chaos runner do):

  1. An initial recovery decision appears ELIGIBLE (pure policy + pure
     safety gate both say GO on the evidence known at decision time T1).
  2. Fresh evidence arrives that contradicts the decision: a late
     SETTLEMENT_CONFIRMED — the payment succeeded on its own while the
     decision was in flight.
  3. The fresh-evidence safety gate re-evaluates from FRESH state and
     vetoes — passing the STALE pre-settlement assessment with FRESH
     events/reconstruction still blocks (the gate never trusts the
     assessment; spec section 10).
  4. Unsafe execution is blocked: process_transaction returns
     RECOVERY_BLOCKED, the provider is NEVER called, nothing is released,
     the transaction never reaches LIMIT_RELEASED, exactly one recovery
     row exists.
  5. The whole story stays auditable (append-only Digital Twin timeline)
     and replayable (an idempotent replay returns the same blocked outcome
     with no new provider call, no second row, no new twin events; the
     temporal twin reconstructs what the system knew before vs after the
     settlement landed).

Determinism: ids are pinned under the INNOV- prefix and all domain
timestamps derive from INNOV_BASE — chosen apart from DEMO_BASE and
CHAOS_BASE so this corpus can never collide with the demo/chaos fixtures.
The module fixture purges the INNOV- corpus before and after (same
rerun-safety contract as tests/test_chaos.py::chaos_env).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from api.core.config import get_settings
from api.db.database import SessionLocal
from api.db.models import RecoveryActionRecord
from api.schemas.payment_events import PaymentEventIn
from api.schemas.recovery_autonomous import (
    BLOCK_ALREADY_SUCCESS,
    BLOCK_INSUFFICIENT_EVIDENCE,
    BLOCK_NEW_SUCCESSFUL_SETTLEMENT,
    BLOCK_NOT_ELIGIBLE,
    BLOCK_RISK_NO_LONGER_PERMITS,
)
from api.schemas.transaction import TransactionEventRequest
from api.services.autonomous_recovery import process_transaction
from api.services.chaos import purge_chaos_rows
from api.services.demo_scenarios import (
    LATE_SETTLEMENT_OFFSET_MS,
    build_events,
    build_late_settlement_event,
)
from api.services.digital_twin import get_timeline
from api.services.event_reconstruction import reconstruct_from_events
from api.services.ml_service import get_ml_service
from api.services.payment_event_service import get_payment_events, ingest_events
from api.services.payment_provider import get_payment_provider
from api.services.recovery_decision_policy import decide
from api.services.recovery_safety import check_safety
from api.services.risk_engine import (
    persist_assessment,
    record_anomaly_classified,
    run_assessment,
)
from api.services.temporal import state_at
from api.services.transaction_service import get_transaction, record_event

INNOV_PREFIX = "INNOV-"
TID = "INNOV-DEMO-1"

# Deterministic domain-time base (fixed, UTC — never wall clock). DEMO_BASE
# is 2026-10-03 09:00 and CHAOS_BASE is 2026-10-03 13:00; this base is a full
# day apart so ids/timestamps can never collide with those fixtures.
INNOV_BASE = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)

# The merchant_timeout chain finishes ~3.8 s after INNOV_BASE and the late
# SETTLEMENT_CONFIRMED lands at INNOV_BASE + LATE_SETTLEMENT_OFFSET_MS
# (+10 min) — so these three view-points are deterministic:
T1_DECISION = INNOV_BASE + timedelta(minutes=1)  # decision time: chain done, settlement NOT yet
T_GATE = INNOV_BASE + timedelta(minutes=11)  # fresh-evidence time: settlement confirmed
T_BEFORE_SETTLEMENT = INNOV_BASE + timedelta(minutes=5)  # temporal query, pre-settlement view
T_AFTER_SETTLEMENT = INNOV_BASE + timedelta(minutes=11)  # temporal query, post-settlement view

# Layered block codes accepted for this evidence shape — the same family as
# chaos._LATE_SETTLEMENT_BLOCK_CODES (expressed through the stable schema
# constants): the end-to-end block may land at the policy layer or the gate
# layer depending on how the fresh re-assessment classifies. Step 3's direct
# pure-gate call asserts the EXACT gate code instead.
BLOCK_CODE_FAMILY = (
    BLOCK_NEW_SUCCESSFUL_SETTLEMENT,
    BLOCK_ALREADY_SUCCESS,
    BLOCK_INSUFFICIENT_EVIDENCE,
    BLOCK_NOT_ELIGIBLE,
    BLOCK_RISK_NO_LONGER_PERMITS,
)

# Transaction fixture mirrored from chaos._CHAOS_FIXTURE (identical shape;
# user/merchant renamed so the INNOV corpus is identifiable at a glance).
_INNOV_FIXTURE = {
    "user_id": "USER-INNOV",
    "merchant_id": "MERCHANT-INNOV",
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


@pytest.fixture(scope="module")
def innov_env(client):
    """The real singletons + a fresh INNOV corpus, purged again on the way
    out. `client` is a fixture argument so the session-scoped lifespan (the
    one-time ML model load) has run before any assessment. Artifacts built by
    earlier steps (the stale T1 assessment, ingest results, the blocked
    process response) are cached on this namespace for later steps — pytest
    runs the tests of one module in file order, the same sequential style
    tests/test_chaos.py uses."""
    db = SessionLocal()
    provider = get_payment_provider()
    purge_chaos_rows(db, provider, prefix=INNOV_PREFIX)
    env = SimpleNamespace(
        db=db,
        ml=get_ml_service(),
        provider=provider,
        tid=TID,
        chain_ready=False,
        late_ready=False,
        processed=False,
    )
    try:
        yield env
    finally:
        purge_chaos_rows(db, provider, prefix=INNOV_PREFIX)
        db.close()


# ---------------------------------------------------------------------------
# idempotent story-state builders (each stage runs at most once per module)
# ---------------------------------------------------------------------------


def _ensure_chain(env):
    """Step-1 state through the REAL services: transaction create (which
    walks INITIATED -> PROCESSING -> FAILED -> RISK_ASSESSED ->
    RECOVERY_PENDING and appends the birth twin events), merchant_timeout
    evidence via real ingestion, then the T1 risk assessment (persisted +
    ANOMALY_CLASSIFIED twin observation, committed like the routes do)."""
    if env.chain_ready:
        return
    db, ml = env.db, env.ml
    payload = TransactionEventRequest(transaction_id=env.tid, **_INNOV_FIXTURE)
    record_event(db, payload, ml, get_settings())

    events = build_events(env.tid, "merchant_timeout", INNOV_BASE)
    ingest_events(db, env.tid, [PaymentEventIn(**e) for e in events])

    tx = get_transaction(db, env.tid)
    assessment, fingerprint, _reused = run_assessment(
        db, tx, ml, customer_reported_failure=False
    )
    persist_assessment(db, tx, assessment, fingerprint)
    record_anomaly_classified(db, tx, assessment, fingerprint)
    db.commit()

    env.assessment_t1 = assessment  # the STALE pre-settlement view
    env.chain_ready = True


def _ensure_late_settlement(env):
    """Step-2 state: ingest the late SETTLEMENT_CONFIRMED through the real
    (duplicate-safe) ingestion service."""
    _ensure_chain(env)
    if env.late_ready:
        return
    event = build_late_settlement_event(env.tid, INNOV_BASE)
    env.late_event = event
    env.late_ingest = ingest_events(env.db, env.tid, [PaymentEventIn(**event)])
    env.late_ready = True


def _ensure_processed(env):
    """Step-4 state: the full autonomous-recovery pipeline on the contradicted
    evidence (commits after, exactly like the chaos runner does)."""
    _ensure_late_settlement(env)
    if env.processed:
        return
    env.response = process_transaction(env.db, env.ml, env.provider, env.tid)
    env.db.commit()
    env.processed = True


def _recovery_rows(db, tid: str) -> list[RecoveryActionRecord]:
    return (
        db.query(RecoveryActionRecord)
        .filter(RecoveryActionRecord.transaction_id == tid)
        .order_by(RecoveryActionRecord.id)
        .all()
    )


def _fresh_tx(db, tid: str):
    db.expire_all()  # see COMMITTED state, not this session's cached writes
    return get_transaction(db, tid)


def _unreleased(entry) -> bool:
    """The provider never moved money for this transaction (no ledger entry
    at all, or an entry whose released amount is zero)."""
    return entry is None or float(entry.get("released_amount") or 0) == 0.0


# ------------------------------------------------------------------- steps


def test_01_decision_appears_eligible(client, innov_env):
    """Step 1 — on the evidence known at T1 the policy says ELIGIBLE
    (RELEASE_LIMIT, autonomous-v1) and the safety gate would allow it."""
    env = innov_env
    _ensure_chain(env)
    db = env.db

    tx = get_transaction(db, env.tid)
    assessment = env.assessment_t1
    # the canonical genuine-failure verdict on the merchant_timeout chain.
    # The deterministic rules verdict is LOW; with the ML model loaded the
    # hybrid blend (rule 3: one-step rise at blended > 0.75) may land MEDIUM —
    # either way it must stay inside the policy's autonomous band.
    assert assessment.anomaly_type == "GENUINE_FAILURE"
    assert assessment.risk_level in ("LOW", "MEDIUM")
    assert assessment.recovery_candidate is True

    stored = get_payment_events(db, env.tid)
    reconstruction_t1 = reconstruct_from_events(env.tid, stored, T1_DECISION)
    # at T1 no settlement was ever observed
    assert reconstruction_t1.settlement_status == "NOT_OBSERVED"
    assert reconstruction_t1.root_cause == "MERCHANT_CONFIRMATION_TIMEOUT"

    decision = decide(tx, assessment, reconstruction_t1, now=T1_DECISION)
    assert decision.eligible is True
    assert decision.action == "RELEASE_LIMIT"
    assert decision.policy_version == "autonomous-v1"

    # the pure gate agrees at T1: with no recovery row in flight and no
    # settlement in the fresh stream, nothing blocks
    gate_t1 = check_safety(
        tx, stored, reconstruction_t1, assessment, None, now=T1_DECISION
    )
    assert gate_t1.allowed is True
    assert gate_t1.blocked_reason is None


def test_02_fresh_evidence_contradicts(client, innov_env):
    """Step 2 — a SETTLEMENT_CONFIRMED lands AFTER the assessment: the
    payment succeeded on its own while the decision was in flight."""
    env = innov_env
    _ensure_late_settlement(env)

    ingest = env.late_ingest
    assert ingest["created"] == 1
    assert ingest["duplicates"] == 0

    stored = get_payment_events(env.db, env.tid)
    assert len(stored) == 6
    settlement = stored[-1]
    assert settlement.event_type == "SETTLEMENT_CONFIRMED"
    # deterministic domain time: INNOV_BASE + LATE_SETTLEMENT_OFFSET_MS —
    # well after the merchant chain (which finished within ~4 s of the base)
    ts = settlement.event_timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    assert ts - INNOV_BASE == timedelta(milliseconds=LATE_SETTLEMENT_OFFSET_MS)


def test_03_gate_vetoes_on_fresh_state(client, innov_env):
    """Step 3 — the pure safety gate re-derived from FRESH events vetoes even
    though the STALE pre-settlement assessment still says recovery_candidate:
    the gate never trusts the assessment (spec section 10)."""
    env = innov_env
    _ensure_late_settlement(env)
    db = env.db

    tx = get_transaction(db, env.tid)
    fresh_events = get_payment_events(db, env.tid)
    fresh_reconstruction = reconstruct_from_events(env.tid, fresh_events, T_GATE)
    assert fresh_reconstruction.settlement_status == "CONFIRMED"

    stale_assessment = env.assessment_t1
    assert stale_assessment.recovery_candidate is True  # the stale view says GO

    gate = check_safety(
        tx,
        fresh_events,
        fresh_reconstruction,
        stale_assessment,
        None,
        now=T_GATE,
    )
    assert gate.allowed is False
    assert gate.blocked_reason == BLOCK_NEW_SUCCESSFUL_SETTLEMENT
    # the veto came from the race-condition check, and only from it
    failed = [c for c in gate.checks if not c["passed"]]
    assert [c["name"] for c in failed] == ["fresh_settlement"]


def test_04_execution_blocked_provider_never_called(client, innov_env):
    """Step 4 — the full pipeline refuses to execute: RECOVERY_BLOCKED, a
    layered block code, exactly one recovery row, no provider call, nothing
    released, the transaction never reaches LIMIT_RELEASED."""
    env = innov_env
    _ensure_processed(env)
    db = env.db

    response = env.response
    assert response["decision"] == "RECOVERY_BLOCKED"
    assert response["status"] == "BLOCKED"
    assert response["provider_reference"] is None

    rows = _recovery_rows(db, env.tid)
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "BLOCKED"
    # the fresh evidence must veto — whichever layer lands the block
    assert row.blocked_reason in BLOCK_CODE_FAMILY
    assert row.provider_reference is None

    env.blocked_reason = row.blocked_reason  # cross-checked by step 5

    tx = _fresh_tx(db, env.tid)
    assert tx is not None
    assert tx.current_state != "LIMIT_RELEASED"

    # the provider was NEVER called: no hold, no release, no reference
    assert _unreleased(env.provider.get_ledger_entry(env.tid))


def test_05_audit_trail_and_replay(client, innov_env):
    """Step 5 — the story is auditable (append-only twin timeline records the
    whole chain including the veto) and replayable (idempotent replay returns
    the SAME blocked outcome with no new row/provider call/twin events), and
    the temporal twin reconstructs the two historical views (before the
    settlement: unconfirmed and one event excluded; after: confirmed)."""
    env = innov_env
    _ensure_processed(env)
    db = env.db

    # ---- append-only audit trail ----------------------------------------
    timeline = get_timeline(db, env.tid)
    types_before = [e.event_type for e in timeline]
    # the birth chain from the real transaction ingestion...
    assert types_before[0] == "TRANSACTION_CREATED"
    assert "PAYMENT_PROCESSING" in types_before
    assert "PAYMENT_FAILED" in types_before
    assert "ML_RISK_ASSESSED" in types_before
    assert "RECOVERY_CHECKED" in types_before
    # ...the T1 assessment AND the fresh post-settlement re-assessment...
    assert types_before.count("ANOMALY_CLASSIFIED") == 2
    # ...and the blocked eligibility verdict (blocked-not-eligible path)
    assert types_before.count("RECOVERY_ELIGIBILITY_ASSESSED") == 1

    eligibility = next(
        e for e in timeline if e.event_type == "RECOVERY_ELIGIBILITY_ASSESSED"
    )
    meta = eligibility.event_metadata or {}
    assert meta["eligible"] is False
    assert meta["blocked_reason"] == env.blocked_reason
    assert meta["policy_version"] == "autonomous-v1"

    # ---- idempotent replay: same blocked outcome, zero new effects -------
    response2 = process_transaction(db, env.ml, env.provider, env.tid)
    db.commit()
    assert response2["decision"] == "RECOVERY_BLOCKED"
    assert response2["status"] == "BLOCKED"
    assert response2["recovery_id"] == env.response["recovery_id"]
    assert response2["provider_reference"] is None

    assert len(_recovery_rows(db, env.tid)) == 1  # no second recovery row
    timeline_after = get_timeline(db, env.tid)
    assert [e.event_type for e in timeline_after] == types_before  # appended nothing
    assert _unreleased(env.provider.get_ledger_entry(env.tid))  # still no release

    # ---- temporal reconstruction: what the system knew at T --------------
    tx = _fresh_tx(db, env.tid)
    before = state_at(db, tx, T_BEFORE_SETTLEMENT)
    assert before.observed_event_count == 5
    assert before.excluded_event_count == 1  # the late settlement exists but is EXCLUDED
    assert before.reconstruction.stages["settlement"] == "NOT_OBSERVED"
    assert before.reconstruction.stages["bank_debit"] == "CONFIRMED"
    assert before.reconstruction.root_cause == "MERCHANT_CONFIRMATION_TIMEOUT"
    assert before.uncertainty_note is not None
    assert "EXCLUDED" in before.uncertainty_note

    after = state_at(db, tx, T_AFTER_SETTLEMENT)
    assert after.observed_event_count == 6
    assert after.excluded_event_count == 0
    assert after.reconstruction.stages["settlement"] == "CONFIRMED"
    assert after.uncertainty_note is None

    # temporal reads are PURE: the two historical queries appended nothing
    assert len(get_timeline(db, env.tid)) == len(types_before)

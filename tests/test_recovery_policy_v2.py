"""Stage 8 DECISION-policy tests (pure, no DB): scenario -> RecoveryDecision.

Uses the FakeEvent/FakeTx pattern with a REAL Stage-6 reconstruct_from_events
pass, so every reconstruction the policy sees is genuine. Where the rules
engine cannot produce a risk level / anomaly (e.g. HIGH genuine failure —
only a hybrid ML layer could raise it), the Stage-7 assessment is adjusted
via model_copy, never the reconstruction.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from api.core.payment_lifecycle import EVENT_TYPE_INFO
from api.schemas.recovery_autonomous import (
    ACTION_MANUAL_REVIEW,
    ACTION_NO_ACTION,
    ACTION_RELEASE_LIMIT,
    BLOCK_ALREADY_SUCCESS,
    BLOCK_DOUBLE_DEDUCTION,
    BLOCK_INSUFFICIENT_EVIDENCE,
    BLOCK_NOT_ELIGIBLE,
    BLOCK_RISK_NO_LONGER_PERMITS,
)
from api.services.anomaly_rules import assess_rules
from api.services.event_reconstruction import reconstruct_from_events
from api.services.recovery_decision_policy import (
    POLICY_VERSION,
    decide,
)

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
    amount: float = 1000.0
    retry_count: int = 0
    previous_failures: int = 0
    gateway_latency_ms: int = 0


_counter = 0


def ev(event_type: str, minutes: float, *, provider_event_id: str | None = None,
       reference_id: str | None = None) -> FakeEvent:
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
        reference_id=reference_id,
    )


def prefix_through(event_type: str, *skip: str) -> list[FakeEvent]:
    from api.core.payment_lifecycle import HAPPY_PATH_EVENTS

    idx = HAPPY_PATH_EVENTS.index(event_type)
    types = [et for et in HAPPY_PATH_EVENTS[: idx + 1] if et not in skip]
    return [ev(et, i * 1.0) for i, et in enumerate(types)]


def happy(*skip: str, reference_id: str | None = None) -> list[FakeEvent]:
    from api.core.payment_lifecycle import HAPPY_PATH_EVENTS

    types = [et for et in HAPPY_PATH_EVENTS if et not in skip]
    return [
        ev(et, i * 1.0, reference_id=reference_id)
        for i, et in enumerate(types)
    ]


def assess(events, tx=None, *, customer_reported_failure=False,
           reference_lookup=None):
    if tx is None:
        tx = FakeTx()
    reconstruction = reconstruct_from_events(tx.transaction_id, events, NOW)
    assessment = assess_rules(
        tx, events, reconstruction,
        customer_reported_failure=customer_reported_failure,
        now=NOW, reference_lookup=reference_lookup,
    )
    return assessment, reconstruction


def failure_events() -> list[FakeEvent]:
    """Spec Case A: debit confirmed, gateway timeout, nothing downstream."""
    return prefix_through("GATEWAY_REQUEST_SENT") + [ev("GATEWAY_TIMEOUT", 2.5)]


# ---------------------------------------------------------------------------
# R-A — the eligible path
# ---------------------------------------------------------------------------

def test_low_risk_genuine_failure_is_eligible():
    assessment, reconstruction = assess(failure_events())
    assert assessment.recovery_candidate is True
    decision = decide(FakeTx(), assessment, reconstruction, now=NOW)
    assert decision.eligible is True
    assert decision.action == ACTION_RELEASE_LIMIT
    assert decision.blocked_reason is None
    assert decision.policy_version == POLICY_VERSION
    assert decision.risk_assessment_id == assessment.assessment_id
    assert "DEBIT_CONFIRMED" in decision.required_evidence
    assert "GATEWAY_TIMEOUT" in decision.required_evidence


def test_medium_risk_genuine_failure_is_eligible_if_policy_allows():
    assessment, reconstruction = assess(failure_events())
    # simulate the hybrid agent raising risk to MEDIUM on the same evidence
    assessment = assessment.model_copy(update={"risk_level": "MEDIUM"})
    decision = decide(FakeTx(), assessment, reconstruction, now=NOW)
    assert decision.eligible is True
    assert decision.action == ACTION_RELEASE_LIMIT
    assert decision.risk_level == "MEDIUM"


def test_above_cap_amount_is_not_eligible():
    """Hard autonomy cap (RECOVERY_MAX_AMOUNT): the R-A evidence shape above
    the cap must NOT release — same rule as recovery_policy.evaluate_policy.
    Regression: a 9,831.98 BDT above-cap dataset transaction was autonomously
    released before this check existed."""
    assessment, reconstruction = assess(failure_events())
    decision = decide(
        FakeTx(amount=9831.98), assessment, reconstruction, now=NOW
    )
    assert decision.eligible is False
    assert decision.action == ACTION_NO_ACTION
    assert decision.blocked_reason == BLOCK_NOT_ELIGIBLE
    assert "auto-release cap" in decision.decision_reason


def test_previous_failures_above_cap_is_not_eligible():
    """Hard autonomy cap (RECOVERY_MAX_PREVIOUS_FAILURES): too many prior
    failures block the R-A shape from an autonomous release."""
    assessment, reconstruction = assess(failure_events())
    decision = decide(
        FakeTx(previous_failures=4), assessment, reconstruction, now=NOW
    )
    assert decision.eligible is False
    assert decision.action == ACTION_NO_ACTION
    assert decision.blocked_reason == BLOCK_NOT_ELIGIBLE
    assert "previous_failures" in decision.decision_reason


# ---------------------------------------------------------------------------
# R-B — genuine failure, risk too high
# ---------------------------------------------------------------------------

def test_high_risk_genuine_failure_needs_manual_review():
    assessment, reconstruction = assess(failure_events())
    assessment = assessment.model_copy(update={"risk_level": "HIGH"})
    decision = decide(FakeTx(), assessment, reconstruction, now=NOW)
    assert decision.eligible is False
    assert decision.action == ACTION_MANUAL_REVIEW
    assert decision.blocked_reason == BLOCK_RISK_NO_LONGER_PERMITS


# ---------------------------------------------------------------------------
# R-C — successful but unconfirmed
# ---------------------------------------------------------------------------

def test_successful_but_unconfirmed_needs_manual_review():
    events = happy(
        "MERCHANT_CONFIRMATION_REQUESTED", "MERCHANT_CONFIRMATION_RECEIVED"
    )
    assessment, reconstruction = assess(events)
    decision = decide(FakeTx(), assessment, reconstruction, now=NOW)
    assert decision.eligible is False
    assert decision.action == ACTION_MANUAL_REVIEW
    assert decision.blocked_reason == BLOCK_NOT_ELIGIBLE


# ---------------------------------------------------------------------------
# R-D — financial-integrity / fraud patterns
# ---------------------------------------------------------------------------

def test_double_deduction_blocked():
    events = [
        ev("CUSTOMER_DEBIT_CONFIRMED", 0.0, provider_event_id="P-DEBIT-A"),
        ev("CUSTOMER_DEBIT_CONFIRMED", 1.0, provider_event_id="P-DEBIT-B"),
    ]
    assessment, reconstruction = assess(events)
    decision = decide(FakeTx(), assessment, reconstruction, now=NOW)
    assert decision.eligible is False
    assert decision.action == ACTION_NO_ACTION
    assert decision.blocked_reason == BLOCK_DOUBLE_DEDUCTION


def test_duplicate_transaction_blocked():
    events = happy(reference_id="REF-SHARED")
    lookup = {"REF-SHARED": ["TXN-OTHER"]}
    assessment, reconstruction = assess(
        events, reference_lookup=lambda ref: lookup.get(ref, [])
    )
    decision = decide(FakeTx(), assessment, reconstruction, now=NOW)
    assert decision.eligible is False
    assert decision.action == ACTION_NO_ACTION
    assert decision.blocked_reason == BLOCK_NOT_ELIGIBLE


def test_false_complaint_blocked():
    assessment, reconstruction = assess(happy(), customer_reported_failure=True)
    decision = decide(FakeTx(), assessment, reconstruction, now=NOW)
    assert decision.eligible is False
    assert decision.action == ACTION_NO_ACTION
    assert decision.blocked_reason == BLOCK_NOT_ELIGIBLE
    assert "contradicted" in decision.decision_reason


def test_suspicious_blocked():
    events = prefix_through("GATEWAY_REQUEST_SENT")
    tx = FakeTx(retry_count=5, previous_failures=4)
    assessment, reconstruction = assess(events, tx)
    decision = decide(tx, assessment, reconstruction, now=NOW)
    assert decision.eligible is False
    assert decision.action == ACTION_NO_ACTION
    assert decision.blocked_reason == BLOCK_NOT_ELIGIBLE


# ---------------------------------------------------------------------------
# R-E / R-F — uncertainty and clean success
# ---------------------------------------------------------------------------

def test_incomplete_blocked():
    events = [ev("CUSTOMER_DEBIT_CONFIRMED", 0.0)]
    assessment, reconstruction = assess(events)
    decision = decide(FakeTx(), assessment, reconstruction, now=NOW)
    assert decision.eligible is False
    assert decision.action == ACTION_NO_ACTION
    assert decision.blocked_reason == BLOCK_INSUFFICIENT_EVIDENCE


def test_unknown_blocked():
    events = [ev("CUSTOMER_DEBIT_CONFIRMED", 0.0)]
    assessment, reconstruction = assess(events)
    # simulate an out-of-vocabulary / fallback classification
    assessment = assessment.model_copy(update={"anomaly_type": "UNKNOWN"})
    decision = decide(FakeTx(), assessment, reconstruction, now=NOW)
    assert decision.eligible is False
    assert decision.blocked_reason == BLOCK_INSUFFICIENT_EVIDENCE


def test_successful_transaction_blocked():
    assessment, reconstruction = assess(happy())
    decision = decide(FakeTx(), assessment, reconstruction, now=NOW)
    assert decision.eligible is False
    assert decision.action == ACTION_NO_ACTION
    assert decision.blocked_reason == BLOCK_ALREADY_SUCCESS

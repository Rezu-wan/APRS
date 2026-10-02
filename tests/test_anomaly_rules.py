"""Stage 7 RULES engine tests (pure, no DB): scenario -> anomaly type, risk,
recovery candidacy, evidence traceability, rules-only invariants."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from api.core.payment_lifecycle import (
    EVENT_TYPE_INFO,
    HAPPY_PATH_EVENTS,
)
from api.schemas.risk_assessment import (
    EVIDENCE_CODES,
    EVIDENCE_DEBIT_CONFIRMED,
    EVIDENCE_HIGH_RETRY_COUNT,
    EVIDENCE_MULTIPLE_DEBIT_CONFIRMATIONS,
    EVIDENCE_REPEATED_TRANSACTION_ATTEMPTS,
    EVIDENCE_SUCCESS_WITHOUT_MERCHANT_CONFIRMATION,
    SEVERITY_HIGH,
)
from api.services.anomaly_rules import RULE_VERSION, assess_rules
from api.services.event_reconstruction import reconstruct_from_events

NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)
BASE = NOW - timedelta(minutes=30)


class FakeEvent(NamedTuple):
    """Lightweight PaymentEvent stand-in (the engine reads attributes only)."""

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
    """Lightweight Transaction stand-in (the engine reads attributes only)."""

    transaction_id: str = "TXN-T"
    amount: float = 1000.0
    retry_count: int = 0
    previous_failures: int = 0
    gateway_latency_ms: int = 0


_counter = 0


def ev(event_type: str, minutes: float, *, provider_event_id: str | None = None,
       reference_id: str | None = None) -> FakeEvent:
    """Deterministic fake event for an event type at BASE + minutes."""
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


def happy(*skip: str, reference_id: str | None = None) -> list[FakeEvent]:
    """The 7 happy-path events in order, minus the named skips."""
    types = [et for et in HAPPY_PATH_EVENTS if et not in skip]
    return [
        ev(et, i * 1.0, reference_id=reference_id)
        for i, et in enumerate(types)
    ]


def prefix_through(event_type: str, *skip: str) -> list[FakeEvent]:
    """Happy-path events UP TO AND INCLUDING event_type, minus the skips."""
    idx = HAPPY_PATH_EVENTS.index(event_type)
    types = [et for et in HAPPY_PATH_EVENTS[: idx + 1] if et not in skip]
    return [ev(et, i * 1.0) for i, et in enumerate(types)]


def assess(
    events: list[FakeEvent],
    tx: FakeTx | None = None,
    *,
    customer_reported_failure: bool = False,
    reference_lookup=None,
):
    """Full rules pipeline: reconstruct (real Stage-6 engine), then classify."""
    if tx is None:
        tx = FakeTx()
    reconstruction = reconstruct_from_events(tx.transaction_id, events, NOW)
    return assess_rules(
        tx,
        events,
        reconstruction,
        customer_reported_failure=customer_reported_failure,
        now=NOW,
        reference_lookup=reference_lookup,
    )


# ---------------------------------------------------------------------------
# R1 / R2 — financial-integrity rules
# ---------------------------------------------------------------------------

def test_double_deduction():
    """Two DISTINCT provider events confirming the debit -> DOUBLE_DEDUCTION,
    HIGH, not a recovery candidate (manual financial review)."""
    events = [
        ev("CUSTOMER_DEBIT_CONFIRMED", 0.0, provider_event_id="P-DEBIT-A"),
        ev("CUSTOMER_DEBIT_CONFIRMED", 1.0, provider_event_id="P-DEBIT-B"),
    ]
    result = assess(events)
    assert result.anomaly_type == "DOUBLE_DEDUCTION"
    assert result.risk_level == "HIGH"
    assert result.recovery_candidate is False
    assert "manual financial review" in result.recovery_block_reason
    assert result.triggered_rules[0].rule_id == "R1"
    assert EVIDENCE_MULTIPLE_DEBIT_CONFIRMATIONS in [
        e.code for e in result.evidence
    ]


def test_duplicate_transaction():
    """A provider reference shared with ANOTHER transaction -> R2, HIGH,
    not a candidate."""
    events = happy(reference_id="REF-SHARED")
    lookup = {"REF-SHARED": ["TXN-OTHER"]}
    result = assess(
        events, reference_lookup=lambda ref: lookup.get(ref, [])
    )
    assert result.anomaly_type == "DUPLICATE_TRANSACTION"
    assert result.risk_level == "HIGH"
    assert result.recovery_candidate is False
    assert result.recovery_block_reason == (
        "same provider reference found on another transaction"
    )
    assert result.triggered_rules[0].rule_id == "R2"


def test_duplicate_event_delivery_is_NOT_duplicate_transaction():
    """The same provider_event_id redelivered WITHIN one transaction is an
    idempotent replay, not a duplicate: neither R1 (single distinct debit
    confirmation) nor R2 (reference not shared with another transaction)
    fires — clean success stands."""
    first = ev("CUSTOMER_DEBIT_CONFIRMED", 0.0)
    dup = FakeEvent(
        id=99,
        event_id="EVT-DUP",
        transaction_id="TXN-T",
        provider_event_id=first.provider_event_id,  # SAME provider id
        event_type=first.event_type,
        source=first.source,
        status=first.status,
        event_timestamp=first.event_timestamp + timedelta(seconds=1),
    )

    def fail_lookup(ref):
        raise AssertionError("reference_lookup must not decide this case")

    result = assess(
        [dup, *happy("CUSTOMER_DEBIT_CONFIRMED")],
        reference_lookup=fail_lookup,
    )
    assert result.anomaly_type == "NONE"
    assert result.triggered_rules[0].rule_id == "R7"


# ---------------------------------------------------------------------------
# R3 / R4 / R6 — genuine failure candidates
# ---------------------------------------------------------------------------

def test_genuine_gateway_failure():
    """Spec Case A: debit confirmed, gateway timeout, nothing downstream
    observed -> GENUINE_FAILURE, LOW risk, recovery candidate."""
    events = prefix_through("GATEWAY_REQUEST_SENT") + [
        ev("GATEWAY_TIMEOUT", 2.5)
    ]
    result = assess(events)
    assert result.anomaly_type == "GENUINE_FAILURE"
    assert result.risk_level == "LOW"
    assert result.recovery_candidate is True
    assert result.recovery_block_reason is None
    assert result.triggered_rules[0].rule_id == "R3"
    assert result.reconstruction_root_cause == "GATEWAY_TIMEOUT"


def test_genuine_merchant_failure():
    events = prefix_through("MERCHANT_CONFIRMATION_REQUESTED") + [
        ev("MERCHANT_CONFIRMATION_TIMEOUT", 5.5)
    ]
    result = assess(events)
    assert result.anomaly_type == "GENUINE_FAILURE"
    assert result.risk_level == "LOW"
    assert result.recovery_candidate is True
    assert result.triggered_rules[0].rule_id == "R4"


def test_settlement_failure():
    events = happy("SETTLEMENT_CONFIRMED") + [ev("SETTLEMENT_FAILED", 7.5)]
    result = assess(events)
    assert result.anomaly_type == "GENUINE_FAILURE"
    assert result.risk_level == "LOW"
    assert result.recovery_candidate is True
    assert result.triggered_rules[0].rule_id == "R6"


# ---------------------------------------------------------------------------
# R5 / R7 / R8 — success-chain outcomes
# ---------------------------------------------------------------------------

def test_successful_but_unconfirmed():
    """Funds settled but the merchant never confirmed -> MEDIUM, NOT a
    candidate (manual reconciliation, not recovery)."""
    events = happy(
        "MERCHANT_CONFIRMATION_REQUESTED", "MERCHANT_CONFIRMATION_RECEIVED"
    )
    result = assess(events)
    assert result.anomaly_type == "SUCCESSFUL_BUT_UNCONFIRMED"
    assert result.risk_level == "MEDIUM"
    assert result.recovery_candidate is False
    assert result.recovery_block_reason is not None
    assert "reconciliation" in result.recovery_block_reason
    assert result.triggered_rules[0].rule_id == "R5"
    assert EVIDENCE_SUCCESS_WITHOUT_MERCHANT_CONFIRMATION in [
        e.code for e in result.evidence
    ]


def test_clean_success_no_anomaly():
    result = assess(happy())
    assert result.anomaly_type == "NONE"
    assert result.risk_level == "LOW"
    assert result.recovery_candidate is False
    assert result.recovery_block_reason == "no failure to recover"
    assert result.triggered_rules[0].rule_id == "R7"
    assert EVIDENCE_DEBIT_CONFIRMED in [e.code for e in result.evidence]


def test_false_complaint_requires_full_chain_plus_flag():
    """R8 needs BOTH the fully confirmed chain AND the explicit flag:
      * success without flag -> NONE (never inferred from success alone)
      * success WITH flag    -> FALSE_COMPLAINT
      * non-success WITH flag-> NOT FALSE_COMPLAINT (genuine failure stands)
    """
    events = happy()

    no_flag = assess(events, customer_reported_failure=False)
    assert no_flag.anomaly_type == "NONE"

    with_flag = assess(events, customer_reported_failure=True)
    assert with_flag.anomaly_type == "FALSE_COMPLAINT"
    assert with_flag.risk_level == "HIGH"
    assert with_flag.recovery_candidate is False
    assert with_flag.triggered_rules[0].rule_id == "R8"
    assert with_flag.customer_reported_failure is True

    failing = prefix_through("GATEWAY_REQUEST_SENT") + [
        ev("GATEWAY_TIMEOUT", 2.5)
    ]
    genuine = assess(failing, customer_reported_failure=True)
    assert genuine.anomaly_type == "GENUINE_FAILURE"
    assert genuine.triggered_rules[0].rule_id != "R8"


# ---------------------------------------------------------------------------
# R9 / R10 — pattern and incompleteness
# ---------------------------------------------------------------------------

def test_incomplete_evidence():
    """Spec Case F: only a debit event -> INCOMPLETE (UNKNOWN risk), never
    GENUINE_FAILURE — missing evidence is uncertainty, not a guessed failure."""
    events = [ev("CUSTOMER_DEBIT_CONFIRMED", 0.0)]
    result = assess(events)
    assert result.anomaly_type == "INCOMPLETE"
    assert result.anomaly_type != "GENUINE_FAILURE"
    assert result.risk_level == "UNKNOWN"
    assert result.recovery_candidate is False
    assert result.recovery_block_reason == "insufficient payment evidence"
    assert result.triggered_rules[0].rule_id == "R10"


def test_suspicious_pattern():
    """No terminal outcome anywhere + high retry count -> SUSPICIOUS."""
    events = prefix_through("GATEWAY_REQUEST_SENT")  # progress only, no outcome
    tx = FakeTx(retry_count=5, previous_failures=4)
    result = assess(events, tx)
    assert result.anomaly_type == "SUSPICIOUS"
    assert result.risk_level == "HIGH"
    assert result.recovery_candidate is False
    assert result.triggered_rules[0].rule_id == "R9"
    codes = [e.code for e in result.evidence]
    assert EVIDENCE_HIGH_RETRY_COUNT in codes
    assert EVIDENCE_REPEATED_TRANSACTION_ATTEMPTS in codes
    high = [e for e in result.evidence if e.code == EVIDENCE_HIGH_RETRY_COUNT]
    assert any(e.severity == SEVERITY_HIGH for e in high)


# ---------------------------------------------------------------------------
# invariants
# ---------------------------------------------------------------------------

def test_evidence_traceability():
    """Every EvidenceItem code is in the vocabulary; every classification
    carries at least one rule and at least one evidence item."""
    scenarios = [
        (happy(), FakeTx(), False, None),
        ([ev("CUSTOMER_DEBIT_CONFIRMED", 0.0),
          ev("CUSTOMER_DEBIT_CONFIRMED", 1.0, provider_event_id="P-X")],
         FakeTx(), False, None),
        (prefix_through("GATEWAY_REQUEST_SENT") + [ev("GATEWAY_TIMEOUT", 2.5)],
         FakeTx(), False, None),
        ([ev("CUSTOMER_DEBIT_CONFIRMED", 0.0)], FakeTx(), False, None),
        (prefix_through("GATEWAY_REQUEST_SENT"),
         FakeTx(retry_count=5, previous_failures=4), False, None),
        (happy(reference_id="REF-SHARED"), FakeTx(), False,
         lambda ref: ["TXN-OTHER"] if ref == "REF-SHARED" else []),
        (happy(), FakeTx(), True, None),
        (happy("SETTLEMENT_CONFIRMED") + [ev("SETTLEMENT_NOT_CONFIRMED", 7.5)],
         FakeTx(), False, None),
        # high-latency informational evidence
        (happy(), FakeTx(gateway_latency_ms=9000), False, None),
    ]
    for events, tx, flag, lookup in scenarios:
        result = assess(
            events, tx,
            customer_reported_failure=flag, reference_lookup=lookup,
        )
        assert result.evidence, result.anomaly_type
        assert result.triggered_rules, result.anomaly_type
        for item in result.evidence:
            assert item.code in EVIDENCE_CODES, item.code
            assert item.description
            assert item.severity in ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def test_rules_only_no_ml_fields():
    result = assess(happy())
    assert result.ml_anomaly_score is None
    assert result.model_version == "rules-only"
    assert result.rule_version == RULE_VERSION
    assert result.risk_score == result.deterministic_risk_score


def test_determinism():
    """Same input twice -> identical outputs (assessment_id is fresh uuid4
    identity metadata; every assessment FIELD is reproducible)."""
    events = prefix_through("GATEWAY_REQUEST_SENT") + [
        ev("GATEWAY_TIMEOUT", 2.5)
    ]
    tx = FakeTx(retry_count=1)
    a = assess(events, tx)
    b = assess(events, tx)
    assert a.model_dump(exclude={"assessment_id"}) == b.model_dump(
        exclude={"assessment_id"}
    )
    # the ids must at least be valid uuid-shaped and distinct
    assert a.assessment_id != b.assessment_id
    assert len(a.assessment_id) == 36

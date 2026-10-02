"""Stage 9 §19/§20 — ML and GenAI FAILURE-MODE safety.

The invariant under test: ML and GenAI are SUPPORTING signals only. When
either layer is down, the deterministic rules + policy + executor path must
still produce correct, SAFE outcomes, and no failure may ever be interpreted
as "safe" by omission.

Sections:
  * rules-only risk engine (MLService anomaly models unavailable -> None)
  * ML-unavailable is never read as a LOW-risk verdict (DOUBLE_DEDUCTION
    stays blocked with ml=None)
  * the full recovery path executes on RULES alone when ML is down
  * GenAI provider failure never changes the recovery outcome; the
    explanation endpoint serves the deterministic fallback (is_fallback)
  * OpenAI provider timeout -> AIProviderUnavailable -> fallback (unit)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from sqlalchemy import select

from api.core.payment_lifecycle import EVENT_TYPE_INFO
from api.db.database import SessionLocal
from api.db.models import PaymentEvent, Transaction
from api.schemas.risk_assessment import (
    ANOMALY_DOUBLE_DEDUCTION,
    ANOMALY_GENUINE_FAILURE,
)
from api.services.event_reconstruction import reconstruct_from_events
from api.services.payment_provider import get_payment_provider
from api.services.recovery_decision_policy import decide
from api.services.recovery_executor import execute_recovery
from api.services.risk_engine import persist_assessment, run_assessment
from api.services.transaction_service import get_transaction

from tests.conftest import CLEAN_FAILED_TX, SYSTEM_KEY, make_event

EVENT_URL = "/api/v1/transaction/event"
PROCESS_URL = "/api/v1/transactions/{tid}/recovery/process"
EXPLANATION_URL = "/api/v1/explanations/transaction"

# the genuine-failure evidence chain: money left the customer, the merchant
# confirmation timed out, nothing followed
_MERCHANT_TIMEOUT_EVENTS = [
    "CUSTOMER_DEBIT_CONFIRMED",
    "GATEWAY_RESPONSE_RECEIVED",
    "MERCHANT_CONFIRMATION_REQUESTED",
    "MERCHANT_CONFIRMATION_TIMEOUT",
]
_DOUBLE_DEDUCTION_EVENTS = [
    "CUSTOMER_DEBIT_CONFIRMED",
    "CUSTOMER_DEBIT_CONFIRMED",
    "GATEWAY_TIMEOUT",
]

_T0 = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)


def _unique_id(prefix: str = "TXN-FM") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class _NoML:
    """Duck-typed MLService whose anomaly model is unavailable — the exact
    shape ml_service.predict_anomaly_features() presents when the optional
    artifact failed to load (rules-only mode, precedence rule 4)."""

    def predict_anomaly_features(self, features: dict) -> dict | None:
        return None


def _seed_tx(client, tid: str) -> None:
    resp = client.post(
        EVENT_URL, json=make_event(tid, **CLEAN_FAILED_TX), headers=SYSTEM_KEY
    )
    assert resp.status_code == 200


def _seed_events(tid: str, event_types: list[str]) -> None:
    db = SessionLocal()
    try:
        for i, event_type in enumerate(event_types):
            info = EVENT_TYPE_INFO[event_type]
            db.add(
                PaymentEvent(
                    transaction_id=tid,
                    provider_event_id=f"{tid}-{event_type}-{i}",
                    event_type=event_type,
                    source=info["source"],
                    status=info["outcome"],
                    event_timestamp=_T0 + timedelta(minutes=i),
                )
            )
        db.commit()
    finally:
        db.close()


def _run_engine(tid: str):
    """Run the real risk engine with the unavailable-ML wrapper."""
    db = SessionLocal()
    try:
        tx = get_transaction(db, tid)
        assert tx is not None
        return run_assessment(db, tx, _NoML(), customer_reported_failure=False)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# §19 — ML failure modes
# ---------------------------------------------------------------------------


def test_rules_engine_works_without_ml(client):
    """Anomaly models unavailable -> the assessment is still produced by the
    deterministic rules: GENUINE_FAILURE, a recovery candidate, ml_anomaly_score
    None, model_version 'rules-only'."""
    tid = _unique_id("TXN-FM-RULES")
    _seed_tx(client, tid)
    _seed_events(tid, _MERCHANT_TIMEOUT_EVENTS)

    assessment, _fingerprint, reused = _run_engine(tid)

    assert reused is False
    assert assessment.anomaly_type == ANOMALY_GENUINE_FAILURE
    assert assessment.recovery_candidate is True
    assert assessment.ml_anomaly_score is None
    assert assessment.model_version == "rules-only"
    # the deterministic score stands on its own — no ML blending happened
    assert assessment.risk_score == assessment.deterministic_risk_score


def test_ml_unavailable_is_never_interpreted_as_safe(client):
    """With ML unavailable the POLICY still enforces the deterministic gates:
    a DOUBLE_DEDUCTION case stays a non-candidate. ml=None must never lower
    the risk or unblock anything — absence of a signal is not evidence of
    safety."""
    tid = _unique_id("TXN-FM-DD")
    _seed_tx(client, tid)
    _seed_events(tid, _DOUBLE_DEDUCTION_EVENTS)

    assessment, _fingerprint, _reused = _run_engine(tid)

    assert assessment.anomaly_type == ANOMALY_DOUBLE_DEDUCTION
    assert assessment.recovery_candidate is False
    assert assessment.ml_anomaly_score is None
    assert assessment.model_version == "rules-only"


def test_recovery_path_safe_when_ml_down(client):
    """Full path with ML unavailable on genuine-failure evidence: the policy
    approves and the executor releases — recovery_candidate True came from the
    RULES (rules-only model_version), ML is only a supporting signal."""
    tid = _unique_id("TXN-FM-EXEC")
    _seed_tx(client, tid)
    _seed_events(tid, _MERCHANT_TIMEOUT_EVENTS)

    db = SessionLocal()
    try:
        tx = get_transaction(db, tid)
        assert tx is not None and tx.current_state == "RECOVERY_PENDING"

        assessment, fingerprint, _reused = run_assessment(
            db, tx, _NoML(), customer_reported_failure=False
        )
        persist_assessment(db, tx, assessment, fingerprint)
        db.commit()

        # the eligibility the policy consumed was the rules-only verdict
        assert assessment.model_version == "rules-only"
        assert assessment.recovery_candidate is True
        assert assessment.ml_anomaly_score is None

        now = datetime.now(timezone.utc)
        events = list(
            db.scalars(
                select(PaymentEvent).where(PaymentEvent.transaction_id == tid)
            )
        )
        reconstruction = reconstruct_from_events(tid, events, now)
        decision = decide(tx, assessment, reconstruction, now=now)
        assert decision.eligible and decision.action == "RELEASE_LIMIT"

        row, info = execute_recovery(
            db, tx, decision, assessment, fingerprint,
            get_payment_provider(), now=now,
        )
        db.commit()

        assert info["already_recovered"] is False
        assert row.status == "VERIFIED"
        assert row.provider_reference
        db.refresh(tx)
        assert tx.current_state == "LIMIT_RELEASED"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# §20 — GenAI failure modes
# ---------------------------------------------------------------------------


def _prepare_processable_tx(client, tid: str) -> None:
    _seed_tx(client, tid)
    _seed_events(tid, _MERCHANT_TIMEOUT_EVENTS)


def test_genai_failure_does_not_affect_recovery(client):
    """With the AI provider factory wired to a provider that ALWAYS fails,
    the autonomous recovery outcome is byte-for-byte the pinned happy
    outcome (AUTO_RECOVERED / VERIFIED), and the explanation endpoint serves
    the deterministic fallback (is_fallback True) instead of erroring."""
    from api.services.ai import FailingMockProvider, get_ai_provider

    tid = _unique_id("TXN-FM-AI")
    _prepare_processable_tx(client, tid)

    client.app.dependency_overrides[get_ai_provider] = lambda: FailingMockProvider()
    try:
        process = client.post(
            PROCESS_URL.format(tid=tid),
            json={"customer_reported_failure": False},
            headers=SYSTEM_KEY,
        )
        assert process.status_code == 200
        body = process.json()
        assert body["decision"] == "AUTO_RECOVERED"
        assert body["status"] == "VERIFIED"
        assert body["simulated"] is True

        explanation = client.post(
            EXPLANATION_URL,
            json={"transaction_id": tid, "language": "en", "audience": "support"},
            headers=SYSTEM_KEY,
        )
        assert explanation.status_code == 200
        exp_body = explanation.json()
        assert exp_body["is_fallback"] is True
        assert exp_body["explanation"].strip() != ""
    finally:
        client.app.dependency_overrides.pop(get_ai_provider, None)


def test_genai_timeout_mapping():
    """Unit: an OpenAI client that raises a timeout exception is mapped to
    AIProviderUnavailable (never leaked as a raw SDK error), and the
    deterministic fallback still produces text for the same context."""
    from api.services.ai import AIProviderUnavailable, OpenAIProvider, fallback_explanation
    from api.services.ai.schemas import Audience, ExplanationContext, Language

    class _TimeoutClient:
        class chat:  # noqa: N801 — mirrors the openai client attribute shape
            class completions:
                @staticmethod
                def create(**_kwargs):
                    raise TimeoutError("timed out")

    provider = OpenAIProvider(
        api_key="test-key-not-a-secret", model="gpt-4o-mini", timeout_seconds=1.0
    )
    provider._client = _TimeoutClient()

    context = ExplanationContext(
        transaction_id="TXN-TIMEOUT-1",
        transaction_status="LIMIT_RELEASED",
        amount="30.00 BDT",
        failure_reason="Timeout",
        failure_prediction="Timeout",
        risk_score="0.03",
        safe_to_release_probability="98%",
        safe_to_release=True,
        recovery_decision="LIMIT_RELEASED",
        recovery_reason="clean failure",
        language=Language.EN,
        audience=Audience.SUPPORT,
    )

    with pytest.raises(AIProviderUnavailable):
        provider.generate(context)

    # the caller's contract: fallback text is always servable
    text = fallback_explanation(context)
    assert isinstance(text, str) and text.strip() != ""

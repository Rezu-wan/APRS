"""GenAI explanation layer internals: MockAIProvider determinism and
anti-hallucination, deterministic fallback templates, audience-based prompt
filtering, and the OpenAI provider's not-configured error path."""

from __future__ import annotations

import json
import uuid

import pytest

from api.services.ai.base import AIProviderUnavailable
from api.services.ai.fallback import fallback_explanation
from api.services.ai.mock_provider import FailingMockProvider, MockAIProvider
from api.services.ai.openai_provider import OpenAIProvider
from api.services.ai.prompts import build_messages
from api.services.ai.schemas import (
    Audience,
    ExplanationContext,
    Language,
    ReconstructionEvidence,
    RecoveryEvidence,
    RiskAssessmentEvidence,
)


def _context(**overrides) -> ExplanationContext:
    """A LIMIT_RELEASED customer/bn context — the happy-path recovery case."""
    payload = {
        "transaction_id": f"TXN-AI-{uuid.uuid4().hex[:12]}",
        "transaction_status": "LIMIT_RELEASED",
        "amount": "30.00 BDT",
        "failure_reason": "Gateway Error",
        "failure_prediction": "Gateway Error",
        "risk_score": "0.03",
        "safe_to_release_probability": "98%",
        "safe_to_release": True,
        "recovery_decision": "LIMIT_RELEASED",
        "recovery_reason": "clean failure, high safe-to-release probability",
        "timeline": [
            "TRANSACTION_CREATED: - -> INITIATED",
            "RISK_ASSESSED: INITIATED -> RECOVERY_PENDING",
            "LIMIT_RELEASED: RECOVERY_PENDING -> LIMIT_RELEASED",
        ],
        "language": Language.BN,
        "audience": Audience.CUSTOMER,
    }
    payload.update(overrides)
    return ExplanationContext(**payload)


def _has_bengali(text: str) -> bool:
    return any("ঀ" <= ch <= "৿" for ch in text)


def test_mock_provider_bn_customer_limit_released_is_bounded_and_non_empty():
    """Mock output for a LIMIT_RELEASED context is non-empty Bangla that only
    reflects the given data — it must not invent refunds or bank rejections."""
    provider = MockAIProvider()
    context = _context()
    text = provider.generate(context)

    assert isinstance(text, str)
    assert text.strip() != ""
    assert _has_bengali(text)
    lowered = text.lower()
    for invented in ("refund", "bank rejected"):
        assert invented not in lowered, (
            f"mock explanation invented content: {invented!r}"
        )


def test_mock_provider_is_deterministic_per_context():
    """The same context always yields the same text (no hidden state)."""
    provider = MockAIProvider()
    context = _context()
    assert provider.generate(context) == provider.generate(context)


def test_fallback_bn_customer_contains_limit_and_timeout_phrase():
    """The deterministic bn/customer fallback mentions the released limit and
    carries the Timeout failure reason (Bangla phrase or the raw reason)."""
    context = _context(failure_reason="Timeout", failure_prediction="Timeout")
    text = fallback_explanation(context)

    assert isinstance(text, str) and text.strip() != ""
    assert "লিমিট" in text
    # The approved natural-Bangla copy renders Timeout as the causal phrase
    # "গেটওয়ে সাড়া না পাওয়ায়" (no gateway response in time) — not a
    # transliteration; assert the reviewed phrase is present.
    assert "গেটওয়ে সাড়া না পাওয়ায়" in text


def test_fallback_en_support_passes_risk_score_through_unaltered():
    """Numbers arrive pre-formatted; the fallback must echo them exactly."""
    context = _context(
        language=Language.EN,
        audience=Audience.SUPPORT,
        risk_score="0.21",
    )
    text = fallback_explanation(context)

    assert isinstance(text, str) and text.strip() != ""
    assert "0.21" in text


def test_build_messages_customer_payload_excludes_sensitive_fields():
    """For a CUSTOMER audience the risk/probability/timeline keys must be gone
    from the user DATA payload — the model can never leak what it was never
    given. (The SYSTEM_PROMPT may name the fields it forbids changing; the
    security boundary is the data payload, which is checked here.)"""
    context = _context(audience=Audience.CUSTOMER)
    messages = build_messages(context)
    user_payload = json.dumps(
        [m for m in messages if m["role"] != "system"], ensure_ascii=False
    )

    for forbidden in ("risk_score", "safe_to_release_probability", "timeline"):
        assert forbidden not in user_payload


def test_build_messages_support_payload_includes_sensitive_fields():
    """For a SUPPORT audience the same fields must be present in the payload."""
    context = _context(audience=Audience.SUPPORT, language=Language.EN)
    messages = build_messages(context)
    serialized = json.dumps(messages, ensure_ascii=False)

    for required in ("risk_score", "safe_to_release_probability", "timeline"):
        assert required in serialized


def test_build_messages_customer_payload_excludes_missing_events_but_keeps_evidence():
    """Reconstruction evidence is authoritative and customer-appropriate
    (root cause, stage statuses, evidence_summary) — but missing_events is
    internal bookkeeping and must never reach a CUSTOMER payload."""
    context = _context(
        audience=Audience.CUSTOMER,
        language=Language.EN,
        reconstruction=ReconstructionEvidence(
            root_cause="MERCHANT_CONFIRMATION_TIMEOUT",
            failure_stage="MERCHANT_CONFIRMATION",
            last_successful_stage="GATEWAY",
            customer_debit_status="CONFIRMED",
            gateway_status="CONFIRMED",
            merchant_confirmation_status="TIMEOUT",
            settlement_status="NOT_OBSERVED",
            missing_events=["MERCHANT_CONFIRMATION_RECEIVED", "SETTLEMENT_REQUESTED"],
            evidence_summary=[
                "Customer bank debit confirmed.",
                "Merchant confirmation timed out.",
            ],
        ),
    )
    messages = build_messages(context)
    user_payload = json.dumps(
        [m for m in messages if m["role"] != "system"], ensure_ascii=False
    )

    assert "missing_events" not in user_payload
    assert "MERCHANT_CONFIRMATION_RECEIVED" not in user_payload
    assert "evidence_summary" in user_payload
    assert "Merchant confirmation timed out." in user_payload
    assert "MERCHANT_CONFIRMATION_TIMEOUT" in user_payload


def test_build_messages_support_payload_includes_full_reconstruction():
    """For a SUPPORT audience both evidence_summary and missing_events must be
    present in the payload."""
    context = _context(
        audience=Audience.SUPPORT,
        language=Language.EN,
        reconstruction=ReconstructionEvidence(
            root_cause="GATEWAY_TIMEOUT",
            customer_debit_status="CONFIRMED",
            gateway_status="TIMEOUT",
            merchant_confirmation_status="NOT_OBSERVED",
            settlement_status="NOT_OBSERVED",
            missing_events=["GATEWAY_RESPONSE_RECEIVED"],
            evidence_summary=["Gateway timed out."],
        ),
    )
    messages = build_messages(context)
    serialized = json.dumps(messages, ensure_ascii=False)

    assert "evidence_summary" in serialized
    assert "missing_events" in serialized
    assert "Gateway timed out." in serialized


def _risk_assessment(**overrides) -> RiskAssessmentEvidence:
    payload = {
        "anomaly_type": "GENUINE_FAILURE",
        "risk_level": "LOW",
        "recovery_candidate": True,
        "recovery_block_reason": None,
        "triggered_rules": ["clean-failure"],
        "reconstruction_root_cause": "GATEWAY_TIMEOUT",
    }
    payload.update(overrides)
    return RiskAssessmentEvidence(**payload)


def test_build_messages_customer_payload_excludes_risk_assessment_entirely():
    """Stage 7: the ENTIRE risk_assessment object is stripped from CUSTOMER
    payloads — customers must never receive anomaly data, classifications, or
    even the word 'anomaly' from the model's data."""
    context = _context(
        audience=Audience.CUSTOMER,
        language=Language.EN,
        risk_assessment=_risk_assessment(anomaly_type="FALSE_COMPLAINT"),
    )
    messages = build_messages(context)
    user_payload = json.dumps(
        [m for m in messages if m["role"] != "system"], ensure_ascii=False
    )

    assert "risk_assessment" not in user_payload
    assert "FALSE_COMPLAINT" not in user_payload
    assert "anomaly" not in user_payload.lower()
    assert "possible false complaint" not in user_payload.lower()


def test_build_messages_support_payload_includes_risk_assessment():
    """For a SUPPORT audience the full classification is present in the
    payload — support-facing output may include the classification."""
    context = _context(
        audience=Audience.SUPPORT,
        language=Language.EN,
        risk_assessment=_risk_assessment(),
    )
    messages = build_messages(context)
    serialized = json.dumps(messages, ensure_ascii=False)

    assert "risk_assessment" in serialized
    assert "GENUINE_FAILURE" in serialized
    assert "clean-failure" in serialized


def test_fallback_risk_assessment_support_en_reports_classification_and_eligibility():
    """The support/en fallback prepends a faithful classification line from the
    deterministic rules engine, including recovery eligibility."""
    context = _context(
        audience=Audience.SUPPORT,
        language=Language.EN,
        risk_assessment=_risk_assessment(),
    )
    text = fallback_explanation(context)

    assert "Anomaly classification: genuine transaction failure (risk level: LOW)." in text
    assert "Recovery eligibility: eligible — pending recovery decision." in text


def test_fallback_risk_assessment_support_en_reports_block_reason():
    """When not a recovery candidate, a present block reason is surfaced."""
    context = _context(
        audience=Audience.SUPPORT,
        language=Language.EN,
        risk_assessment=_risk_assessment(
            recovery_candidate=False, recovery_block_reason="duplicate debit detected"
        ),
    )
    text = fallback_explanation(context)

    assert "Anomaly classification:" in text
    assert "Recovery eligibility: not eligible — duplicate debit detected." in text


def test_fallback_risk_assessment_support_bn_reports_classification_and_eligibility():
    """The support/bn fallback reports the classification in natural Bangla."""
    context = _context(
        audience=Audience.SUPPORT,
        language=Language.BN,
        risk_assessment=_risk_assessment(),
    )
    text = fallback_explanation(context)

    assert "অসঙ্গতি শ্রেণিবিন্যাস: সত্যিকারের লেনদেন ব্যর্থতা (ঝুঁকির স্তর: LOW)" in text
    assert "পুনরুদ্ধারের যোগ্যতা: যোগ্য — পুনরুদ্ধার সিদ্ধান্ত অপেক্ষমাণ" in text


def test_fallback_risk_assessment_customer_is_neutral_no_anomaly_terminology():
    """Customer fallbacks (bn + en) carry ONLY the neutral review line — no
    anomaly type, no classification, no risk level, no accusatory language."""
    en_context = _context(
        audience=Audience.CUSTOMER,
        language=Language.EN,
        risk_assessment=_risk_assessment(anomaly_type="FALSE_COMPLAINT"),
    )
    en_text = fallback_explanation(en_context)
    assert (
        "Your transaction is being evaluated using payment-system evidence. "
        "Recovery eligibility: under review." in en_text
    )
    lowered = en_text.lower()
    assert "anomaly" not in lowered
    assert "false complaint" not in lowered
    assert "fraud" not in lowered
    assert "risk level" not in lowered

    bn_context = _context(
        audience=Audience.CUSTOMER,
        language=Language.BN,
        risk_assessment=_risk_assessment(anomaly_type="FALSE_COMPLAINT"),
    )
    bn_text = fallback_explanation(bn_context)
    assert (
        "আপনার লেনদেনটি পেমেন্ট সিস্টেমের প্রমাণের ভিত্তিতে মূল্যায়ন করা হচ্ছে। "
        "পুনরুদ্ধারের যোগ্যতা: পর্যালোচনাধীন।" in bn_text
    )
    assert "সম্ভাব্য ভুল অভিযোগ" not in bn_text
    assert "ঝুঁকির স্তর" not in bn_text


def test_fallback_without_risk_assessment_outputs_unchanged():
    """Regression guard: with risk_assessment absent every fallback output is
    byte-identical to the pre-Stage-7 templates — no review line, no
    classification line, no crash."""
    for language in (Language.BN, Language.EN):
        for audience in (Audience.CUSTOMER, Audience.SUPPORT):
            context = _context(language=language, audience=audience)
            text = fallback_explanation(context)
            assert "Anomaly classification:" not in text
            assert "payment-system evidence" not in text
            assert "পেমেন্ট সিস্টেমের প্রমাণের ভিত্তিতে" not in text
            assert "পুনরুদ্ধারের যোগ্যতা" not in text
            assert text.strip() != ""


def _recovery(**overrides) -> RecoveryEvidence:
    payload = {
        "action": "RELEASE_LIMIT",
        "status": "VERIFIED",
        "decision_reason": "genuine failure",
        "blocked_reason": None,
        "failure_reason": None,
        "provider_reference": "REL-TEST1234",
        "verified": True,
    }
    payload.update(overrides)
    return RecoveryEvidence(**payload)


def test_fallback_recovery_verified_customer_reports_release_and_reference():
    """Stage 8: with status VERIFIED the customer fallback claims the release
    and includes the provider reference — en and reviewed bn copy."""
    en_context = _context(language=Language.EN, recovery=_recovery())
    en_text = fallback_explanation(en_context)
    assert "released" in en_text
    assert "REL-TEST1234" in en_text

    bn_context = _context(recovery=_recovery())
    bn_text = fallback_explanation(bn_context)
    assert _has_bengali(bn_text)
    assert "স্বয়ংক্রিয়ভাবে ছেড়ে দেওয়া হয়েছে" in bn_text
    assert "REL-TEST1234" in bn_text


def test_fallback_recovery_non_verified_customer_never_claims_release():
    """Spec §30: pending/blocked/non-verified recovery must yield neutral
    under-review wording — never a release claim, no internal statuses."""
    for language in (Language.BN, Language.EN):
        for status in ("PENDING", "EXECUTING", "BLOCKED", "FAILED",
                       "VERIFICATION_PENDING", "COMPLETED"):
            context = _context(
                language=language,
                recovery=_recovery(status=status, verified=False),
                recovery_decision="LIMIT_RELEASED",
            )
            text = fallback_explanation(context)

            assert "released" not in text.lower(), (
                f"{language.value}/{status} claimed release"
            )
            assert "ছেড়ে দেওয়া" not in text
            assert status not in text


def test_fallback_recovery_support_reports_status_and_sandbox_marker():
    """Support sees the full recovery outcome: humanized action, status and
    the simulated sandbox marker."""
    context = _context(
        language=Language.EN,
        audience=Audience.SUPPORT,
        recovery=_recovery(
            status="BLOCKED", verified=False, blocked_reason="duplicate debit"
        ),
    )
    text = fallback_explanation(context)

    assert "Autonomous recovery: release limit — status blocked" in text
    assert "(simulated sandbox provider)" in text
    assert "Blocked reason: duplicate debit" in text


def test_build_messages_customer_payload_excludes_recovery_entirely():
    """Stage 8: the ENTIRE recovery object is stripped from CUSTOMER payloads
    — no action codes, statuses, reasons, or provider references."""
    context = _context(
        audience=Audience.CUSTOMER,
        language=Language.EN,
        recovery_decision=None,
        recovery_reason=None,
        recovery=_recovery(),
    )
    messages = build_messages(context)
    user_payload = json.dumps(
        [m for m in messages if m["role"] != "system"], ensure_ascii=False
    )

    assert '"recovery"' not in user_payload
    assert "provider_reference" not in user_payload
    assert "REL-TEST1234" not in user_payload
    assert "VERIFIED" not in user_payload
    assert "RELEASE_LIMIT" not in user_payload
    assert "genuine failure" not in user_payload


def test_build_messages_support_payload_includes_recovery():
    """For a SUPPORT audience the full recovery outcome stays in the payload."""
    context = _context(
        audience=Audience.SUPPORT,
        language=Language.EN,
        recovery=_recovery(),
    )
    messages = build_messages(context)
    serialized = json.dumps(messages, ensure_ascii=False)

    assert "recovery" in serialized
    assert "REL-TEST1234" in serialized
    assert "RELEASE_LIMIT" in serialized


def test_openai_provider_without_api_key_raises_unavailable():
    """An empty api_key is a configuration error surfaced as
    AIProviderUnavailable — constructor + generate, no network involved."""

    def _use_provider():
        provider = OpenAIProvider(api_key="", model="gpt-4o-mini", timeout_seconds=12.0)
        provider.generate(_context(audience=Audience.SUPPORT, language=Language.EN))

    with pytest.raises(AIProviderUnavailable):
        _use_provider()


def test_failing_mock_provider_raises_unavailable():
    provider = FailingMockProvider()
    with pytest.raises(AIProviderUnavailable):
        provider.generate(_context())

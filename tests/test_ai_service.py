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
from api.services.ai.schemas import Audience, ExplanationContext, Language


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

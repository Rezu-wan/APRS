"""
api/services/ai/mock_provider.py — offline providers for dev and tests.

MockAIProvider is the safe default: fully deterministic, no network, and it
returns the SAME text as the deterministic fallback templates so dev
environments and tests exercise data-faithful output.

FailingMockProvider always raises AIProviderUnavailable — used to test the
fallback path end-to-end.
"""

from __future__ import annotations

import logging

from api.services.ai.base import AIProvider, AIProviderUnavailable
from api.services.ai.fallback import fallback_explanation
from api.services.ai.schemas import ExplanationContext

logger = logging.getLogger("payment_recovery.ai.mock")


class MockAIProvider(AIProvider):
    """Deterministic, no-network provider. name/model are fixed so the
    audit fields on the response always say 'mock'."""

    name = "mock"
    model = "mock-1"

    def generate(self, context: ExplanationContext) -> str:
        text = fallback_explanation(context)
        logger.debug(
            "mock provider generated explanation: tx=%s lang=%s audience=%s",
            context.transaction_id,
            context.language.value,
            context.audience.value,
        )
        return text


class FailingMockProvider(AIProvider):
    """Always unavailable — for fallback tests."""

    name = "mock-failing"
    model = "mock-failing-1"

    def generate(self, context: ExplanationContext) -> str:
        logger.debug("failing mock provider invoked: tx=%s", context.transaction_id)
        raise AIProviderUnavailable("mock provider is configured to fail")

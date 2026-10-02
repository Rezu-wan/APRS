"""
api/services/ai/__init__.py — GenAI explanation layer (Stage 4).

ARCHITECTURAL RULE: GenAI only EXPLAINS already-made decisions. It never
decides, never mutates state, never calculates risk. This package is a pure
generation layer.

Public surface:
- Schemas (ExplanationRequest/Context/Output/Response, Language, Audience).
- AIProvider + its error taxonomy (base).
- Prompt construction (SYSTEM_PROMPT, PROMPT_VERSION, build_messages).
- Deterministic fallback templates (fallback_explanation) — used when a
  provider is unavailable and returned verbatim by MockAIProvider.
- get_ai_provider(): settings-driven factory. FastAPI uses it as a
  dependency; tests override it via app.dependency_overrides.
"""

from __future__ import annotations

import logging

from fastapi import Depends

from api.core.config import Settings, get_settings
from api.services.ai.base import (
    AIProvider,
    AIProviderBadResponse,
    AIProviderError,
    AIProviderUnavailable,
)
from api.services.ai.fallback import fallback_explanation
from api.services.ai.mock_provider import FailingMockProvider, MockAIProvider
from api.services.ai.openai_provider import OpenAIProvider
from api.services.ai.prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_messages
from api.services.ai.schemas import (
    Audience,
    ExplanationContext,
    ExplanationOutput,
    ExplanationRequest,
    ExplanationResponse,
    Language,
)

logger = logging.getLogger("payment_recovery.ai")

__all__ = [
    "AIProvider",
    "AIProviderBadResponse",
    "AIProviderError",
    "AIProviderUnavailable",
    "Audience",
    "ExplanationContext",
    "ExplanationOutput",
    "ExplanationRequest",
    "ExplanationResponse",
    "FailingMockProvider",
    "Language",
    "MockAIProvider",
    "OpenAIProvider",
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "build_messages",
    "fallback_explanation",
    "get_ai_provider",
]


def get_ai_provider(settings: Settings = Depends(get_settings)) -> AIProvider:
    """Factory: settings.ai_provider 'mock' -> MockAIProvider(); 'openai' ->
    OpenAIProvider(settings.openai_api_key, settings.openai_model,
    settings.ai_timeout_seconds). Unknown value -> AIProviderUnavailable.

    FastAPI-dependency shaped: settings are injected via Depends so the
    Settings model is never mistaken for a request body. Tests override the
    whole dependency via app.dependency_overrides[get_ai_provider]."""
    provider_name = (settings.ai_provider or "").strip().lower()
    if provider_name == "mock":
        return MockAIProvider()
    if provider_name == "openai":
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_seconds=settings.ai_timeout_seconds,
        )
    logger.warning("unknown ai_provider setting: %s", provider_name)
    raise AIProviderUnavailable(f"unknown ai_provider: {provider_name}")

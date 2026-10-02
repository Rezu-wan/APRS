"""
api/services/ai/base.py — provider abstraction for the GenAI explanation layer.

The AIProvider contract is deliberately tiny: given an ExplanationContext,
return explanation text. Providers raise ONLY AIProviderError subclasses so
callers can catch a single hierarchy; a raised provider error always means
the caller should fall back to the deterministic templates (never to
re-deciding anything — GenAI explains, it never decides).

Error taxonomy:
  AIProviderUnavailable — the provider could not be reached or used at all
  (timeout, network, rate limit, bad key, not configured, 5xx).
  AIProviderBadResponse — the provider answered but the answer is unusable
  (malformed, empty, or exceeding the allowed length).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from api.services.ai.schemas import ExplanationContext


class AIProviderError(Exception):
    """Base class for all AI provider errors."""


class AIProviderUnavailable(AIProviderError):
    """Timeout / network / rate-limit / bad key / not configured / 5xx."""


class AIProviderBadResponse(AIProviderError):
    """Provider responded but the payload is malformed, empty, or too long."""


class AIProvider(ABC):
    """A provider that EXPLAINS already-made decisions. It never decides,
    never mutates state, and never calculates risk."""

    name: str
    model: str

    @abstractmethod
    def generate(self, context: ExplanationContext) -> str:
        """Return explanation text. Raises AIProviderError subclasses only."""

"""
api/services/ai/openai_provider.py — OpenAI chat.completions provider.

Design decisions:
- Lazy client construction: importing this module (and instantiating the
  provider) never creates a network client or requires the openai package
  until the first generate() call.
- Failure containment: every openai exception is mapped to
  AIProviderUnavailable. Logs include ONLY the exception class name — never
  the API key, never provider message bodies (they could echo request data).
- Output validation: empty/whitespace answers or answers longer than
  max_chars are AIProviderBadResponse, so the caller falls back to the
  deterministic templates instead of shipping a garbage explanation.
- The provider only ever RECEIVES an ExplanationContext (schema-controlled,
  pre-formatted numbers, no secrets) and returns text. It never decides.
"""

from __future__ import annotations

import logging

from api.services.ai.base import AIProvider, AIProviderBadResponse, AIProviderUnavailable
from api.services.ai.prompts import build_messages
from api.services.ai.schemas import ExplanationContext

logger = logging.getLogger("payment_recovery.ai.openai")


class OpenAIProvider(AIProvider):
    """Chat-completions provider. Explains decisions; decides nothing."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_chars: int = 2000,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._timeout_seconds = timeout_seconds
        self._max_chars = max_chars
        self.name = "openai"
        self._client = None

    def _get_client(self):
        # imported here so importing this module never pulls the openai SDK
        from openai import OpenAI

        if self._client is None:
            self._client = OpenAI(api_key=self._api_key, timeout=self._timeout_seconds)
        return self._client

    def generate(self, context: ExplanationContext) -> str:
        if not self._api_key or not self._api_key.strip():
            logger.warning(
                "OpenAI provider has no API key configured; class=NoApiKey"
            )
            raise AIProviderUnavailable("AI provider not configured")

        messages = build_messages(context)
        try:
            response = self._get_client().chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3,
                max_tokens=512,
            )
        except Exception as exc:  # noqa: BLE001 — ALL provider errors are contained
            # Log ONLY the exception class name: provider message bodies may
            # echo request content, and the key must never appear anywhere.
            logger.warning("OpenAI call failed: class=%s", type(exc).__name__)
            raise AIProviderUnavailable("AI provider unavailable") from exc

        text = ""
        if response.choices:
            text = (response.choices[0].message.content or "").strip()
        if not text:
            logger.warning("OpenAI returned an empty response")
            raise AIProviderBadResponse("AI provider returned an empty response")
        if len(text) > self._max_chars:
            logger.warning(
                "OpenAI response exceeded max length: len=%d max=%d",
                len(text),
                self._max_chars,
            )
            raise AIProviderBadResponse("AI provider response too long")
        logger.info(
            "OpenAI explanation generated: tx=%s model=%s chars=%d",
            context.transaction_id,
            self.model,
            len(text),
        )
        return text

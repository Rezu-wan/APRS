"""
api/services/ai/schemas.py — data contracts for the GenAI explanation layer.

These schemas form a hard, schema-controlled boundary between the backend and
any AI provider: an ExplanationContext is the ONLY data ever handed to a
provider. It carries no secrets, no credentials, no internal config, and all
numbers arrive PRE-FORMATTED as strings (done by the backend) so the model can
never recalculate or alter them.

ExplanationOutput / ExplanationResponse are what the layer returns; the
response records provider/model/prompt version and whether the text came from
the deterministic fallback, so every explanation stays auditable.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Language(str, Enum):
    BN = "bn"
    EN = "en"


class Audience(str, Enum):
    CUSTOMER = "customer"
    SUPPORT = "support"
    SYSTEM = "system"


class ExplanationRequest(BaseModel):
    language: Language
    audience: Audience


class ExplanationContext(BaseModel):
    """The ONLY data ever handed to an AI provider — schema-controlled, no
    secrets, no credentials, no internal config. Numbers are pre-formatted
    by the backend so the model can never alter them."""

    transaction_id: str
    transaction_status: str  # e.g. LIMIT_RELEASED
    amount: str  # preformatted, e.g. "1250.00 BDT"
    failure_reason: str | None = None  # observed reason, e.g. "Timeout"
    failure_prediction: str | None = None  # ML output, e.g. "Timeout"
    risk_score: str | None = None  # preformatted, e.g. "0.21"
    safe_to_release_probability: str | None = None  # preformatted, e.g. "94%"
    safe_to_release: bool | None = None
    recovery_decision: str | None = None  # LIMIT_RELEASED | MANUAL_REVIEW | RECOVERY_REJECTED
    recovery_reason: str | None = None
    timeline: list[str] | None = None  # e.g. ["TRANSACTION_CREATED: - -> INITIATED", ...]
    reconstruction: ReconstructionEvidence | None = None
    language: Language
    audience: Audience


class ReconstructionEvidence(BaseModel):
    """Projection of the Stage 6 deterministic reconstruction into the
    explanation context — evidence fields only, no confidence internals."""

    root_cause: str
    failure_stage: str | None = None
    last_successful_stage: str | None = None
    customer_debit_status: str
    gateway_status: str
    merchant_confirmation_status: str
    settlement_status: str
    missing_events: list[str] = []
    evidence_summary: list[str] = []


class ExplanationOutput(BaseModel):
    explanation: str = Field(min_length=1)


class ExplanationResponse(BaseModel):
    transaction_id: str
    language: Language
    audience: Audience
    explanation: str
    provider: str
    model: str
    prompt_version: str
    is_fallback: bool
    cached: bool = False
    generated_at: datetime

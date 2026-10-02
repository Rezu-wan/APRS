"""
api/schemas/reconstruction.py — request/response models for the Stage 6
payment-event reconstruction API.

The reconstruction engine (api/services/event_reconstruction.py) is PURE and
deterministic: it derives a payment's lifecycle story ONLY from stored
PaymentEvent facts. There is no LLM, no randomness, and no invented events
anywhere in this slice — uncertainty is represented explicitly
(INCOMPLETE / NOT_OBSERVED), never guessed away.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.transaction import UtcDatetime

# Root-cause vocabulary (deterministic evidence rules — see the engine's
# docstring for the FIRST-MATCH priority order).
ROOT_CAUSE_NONE = "NONE"
ROOT_CAUSE_INCOMPLETE = "INCOMPLETE"
ROOT_CAUSE_CUSTOMER_DEBIT_FAILED = "CUSTOMER_DEBIT_FAILED"
ROOT_CAUSE_GATEWAY_TIMEOUT = "GATEWAY_TIMEOUT"
ROOT_CAUSE_GATEWAY_ERROR = "GATEWAY_ERROR"
ROOT_CAUSE_MERCHANT_CONFIRMATION_TIMEOUT = "MERCHANT_CONFIRMATION_TIMEOUT"
ROOT_CAUSE_MERCHANT_ERROR = "MERCHANT_ERROR"
ROOT_CAUSE_SETTLEMENT_FAILED = "SETTLEMENT_FAILED"
ROOT_CAUSE_SETTLEMENT_NOT_CONFIRMED = "SETTLEMENT_NOT_CONFIRMED"

ROOT_CAUSES = (
    ROOT_CAUSE_NONE,
    ROOT_CAUSE_INCOMPLETE,
    ROOT_CAUSE_CUSTOMER_DEBIT_FAILED,
    ROOT_CAUSE_GATEWAY_TIMEOUT,
    ROOT_CAUSE_GATEWAY_ERROR,
    ROOT_CAUSE_MERCHANT_CONFIRMATION_TIMEOUT,
    ROOT_CAUSE_MERCHANT_ERROR,
    ROOT_CAUSE_SETTLEMENT_FAILED,
    ROOT_CAUSE_SETTLEMENT_NOT_CONFIRMED,
)

# Stage-status vocabulary (mirrors the lifecycle outcomes plus the derived
# NOT_OBSERVED — which is never stored, only derived at reconstruction time).
STAGE_NOT_OBSERVED = "NOT_OBSERVED"

# Current stage sentinel when a transaction has no payment events at all.
STAGE_UNAVAILABLE = "UNAVAILABLE"


class PaymentEventOut(BaseModel):
    """One stored payment-domain event (the observed fact)."""

    model_config = ConfigDict(from_attributes=True)

    event_id: str
    transaction_id: str
    provider_event_id: str
    event_type: str
    source: str
    status: str
    event_timestamp: UtcDatetime
    reference_id: str | None
    latency_ms: int | None
    # stored under the column name event_metadata; serialized to clients as
    # "metadata" (same convention as TimelineEvent)
    metadata: dict | None = Field(
        default=None,
        validation_alias="event_metadata",
        serialization_alias="metadata",
    )


class ReconstructionResult(BaseModel):
    """Deterministic reconstruction of a payment's lifecycle from events."""

    transaction_id: str
    ordered_events: list[PaymentEventOut]
    # PaymentStage of the furthest stage with ANY evidence, else UNAVAILABLE
    current_stage: str
    # furthest stage whose outcome is CONFIRMED
    last_successful_stage: str | None
    # stage carrying the root-cause event
    failure_stage: str | None
    root_cause: str  # ROOT_CAUSES value; NONE only for full success
    customer_debit_status: str  # CONFIRMED | FAILED | NOT_OBSERVED
    gateway_status: str  # CONFIRMED | TIMEOUT | ERROR | OBSERVED | NOT_OBSERVED
    merchant_confirmation_status: str  # same family
    settlement_status: str  # CONFIRMED | FAILED | NOT_CONFIRMED | OBSERVED | NOT_OBSERVED
    # 0..1, deterministic — see the engine docstring for the formula
    reconstruction_confidence: float
    # HAPPY_PATH_EVENTS never observed, in HAPPY_PATH order
    missing_events: list[str]
    # human-readable English lines, deterministic
    evidence_summary: list[str]
    reconstructed_at: UtcDatetime
    # set by the route layer after the (idempotent) twin append
    digital_twin_event_recorded: bool = False

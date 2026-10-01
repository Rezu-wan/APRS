"""
api/schemas/transaction.py — request/response models for the transaction API.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

SUPPORTED_FAILURE_REASONS = (
    "Timeout",
    "Merchant Disconnect",
    "Insufficient Balance",
    "Network Drop",
    "Gateway Error",
)


def _coerce_utc(v):
    """SQLite (dev DB) returns naive datetimes; re-attach UTC so API
    consumers always receive offset-designated ISO-8601 timestamps."""
    if isinstance(v, datetime) and v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v


UtcDatetime = Annotated[datetime, BeforeValidator(_coerce_utc)]


class NetworkQuality(str, Enum):
    EXCELLENT = "Excellent"
    GOOD = "Good"
    FAIR = "Fair"
    POOR = "Poor"


class FailureReason(str, Enum):
    TIMEOUT = "Timeout"
    MERCHANT_DISCONNECT = "Merchant Disconnect"
    INSUFFICIENT_BALANCE = "Insufficient Balance"
    NETWORK_DROP = "Network Drop"
    GATEWAY_ERROR = "Gateway Error"


class OutcomeStatus(str, Enum):
    # statuses a client may report on an event; INITIATED is implicit on create
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    STALLED = "STALLED"


class TransactionEventRequest(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=64, examples=["TXN-123456"])
    user_id: str = Field(min_length=1, max_length=64, examples=["USER-001"])
    merchant_id: str = Field(min_length=1, max_length=64, examples=["MERCHANT-001"])
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2, examples=[1250.00])
    currency: str = Field(min_length=3, max_length=3, default="BDT", examples=["BDT"])
    timestamp: datetime | None = Field(
        default=None,
        description="payment timestamp (ISO-8601); defaults to server UTC now",
    )
    gateway_latency_ms: int = Field(default=0, ge=0, le=60000, examples=[2800])
    retry_count: int = Field(default=0, ge=0, le=10, examples=[3])
    network_quality: NetworkQuality = Field(default=NetworkQuality.GOOD)
    previous_failures: int = Field(default=0, ge=0, le=1000, examples=[2])
    account_age_days: int = Field(default=0, ge=0, le=36500, examples=[450])
    status: OutcomeStatus = Field(examples=["FAILED"])
    failure_reason: FailureReason | None = Field(
        default=None, description="required when status is FAILED"
    )

    @field_validator("timestamp")
    @classmethod
    def _assume_utc(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    @model_validator(mode="after")
    def _failure_reason_required_on_failure(self):
        if self.status == OutcomeStatus.FAILED and self.failure_reason is None:
            raise ValueError(
                "failure_reason is required when status is FAILED "
                f"(one of: {', '.join(SUPPORTED_FAILURE_REASONS)})"
            )
        if self.status != OutcomeStatus.FAILED:
            self.failure_reason = None
        return self


class MLAssessmentOut(BaseModel):
    failure_prediction: str = Field(examples=["Timeout"])
    failure_probability: float
    failure_probabilities: dict[str, float]
    risk_score: float = Field(
        description="recovery risk in [0,1] (model output; Stage 2 vocabulary: recovery_risk)"
    )
    safe_to_release_probability: float
    safe_to_release: bool


class StateHop(BaseModel):
    event_type: str
    previous_state: str | None
    new_state: str


class TransactionEventResponse(BaseModel):
    transaction_id: str
    current_state: str
    created: bool
    state_changed: bool
    ml_assessment: MLAssessmentOut | None = None
    new_events: list[StateHop] = []


class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_id: str
    user_id: str
    merchant_id: str
    amount: Decimal
    currency: str
    timestamp: UtcDatetime
    gateway_latency_ms: int
    retry_count: int
    network_quality: str
    previous_failures: int
    account_age_days: int
    failure_reason: str | None
    current_state: str
    failure_prediction: str | None
    failure_probability: float | None
    risk_score: float | None
    safe_to_release_probability: float | None
    safe_to_release: bool | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TimelineEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    event_id: str
    event_type: str
    timestamp: UtcDatetime
    previous_state: str | None
    new_state: str
    failure_prediction: str | None
    risk_score: float | None
    safe_to_release_probability: float | None
    safe_to_release: bool | None
    reason: str | None
    metadata: dict[str, Any] | None = Field(
        default=None, validation_alias="event_metadata", serialization_alias="metadata"
    )


class TimelineResponse(BaseModel):
    transaction_id: str
    current_state: str
    event_count: int
    events: list[TimelineEvent]

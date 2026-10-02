"""
api/schemas/payment_events.py — request/response models for the Stage 6
payment-domain event ingestion API.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field, field_validator

from api.core.payment_lifecycle import EVENT_TYPE_INFO, EVENT_TYPES, OUTCOMES, SOURCES

MAX_METADATA_JSON_CHARS = 4096
MAX_FUTURE_SKEW = timedelta(hours=24)  # clock-skew guard
PROVIDER_EVENT_ID_PATTERN = r"^[A-Za-z0-9._:-]{1,128}$"


def _coerce_utc(v):
    """Naive datetimes are assumed to be UTC (same contract as the
    transaction ingestion schema)."""
    if isinstance(v, datetime) and v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v


UtcDatetime = Annotated[datetime, BeforeValidator(_coerce_utc)]


class PaymentEventIn(BaseModel):
    """One provider-observed payment-domain event.

    `source` and `status` are validated against the shared vocabulary but the
    AUTHORITATIVE triple comes from EVENT_TYPE_INFO at ingestion time — the
    client cannot mislabel evidence.
    """

    provider_event_id: str = Field(
        pattern=PROVIDER_EVENT_ID_PATTERN, examples=["EVT-PROV-0001"],
    )
    event_type: str = Field(examples=["GATEWAY_TIMEOUT"])
    source: str = Field(examples=["GATEWAY"])
    status: str = Field(examples=["TIMEOUT"])
    event_timestamp: datetime = Field(description="domain time (ISO-8601); ordering key")
    reference_id: str | None = Field(default=None, max_length=128)
    latency_ms: int | None = Field(default=None, ge=0, le=3_600_000)
    metadata: dict[str, Any] | None = None

    @field_validator("metadata")
    @classmethod
    def _bounded_metadata(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        """Keys must be strings and the serialized JSON must stay within
        MAX_METADATA_JSON_CHARS — validated, never silently coerced."""
        if v is None:
            return v
        for key in v:
            if not isinstance(key, str):
                raise ValueError("metadata keys must be strings")
        if len(json.dumps(v, separators=(",", ":"), default=str)) > MAX_METADATA_JSON_CHARS:
            raise ValueError(
                f"metadata JSON exceeds {MAX_METADATA_JSON_CHARS} characters"
            )
        return v

    @field_validator("event_type")
    @classmethod
    def _known_event_type(cls, v: str) -> str:
        if v not in EVENT_TYPE_INFO:
            raise ValueError(f"unknown event_type (one of: {', '.join(EVENT_TYPES)})")
        return v

    @field_validator("source")
    @classmethod
    def _known_source(cls, v: str) -> str:
        if v not in SOURCES:
            raise ValueError(f"unknown source (one of: {', '.join(SOURCES)})")
        return v

    @field_validator("status")
    @classmethod
    def _known_status(cls, v: str) -> str:
        if v not in OUTCOMES:
            raise ValueError(f"unknown status (one of: {', '.join(OUTCOMES)})")
        return v

    @field_validator("event_timestamp")
    @classmethod
    def _assume_utc(cls, v: datetime) -> datetime:
        v = _coerce_utc(v)
        # clock-skew guard: reject domain timestamps more than MAX_FUTURE_SKEW
        # in the future — past events are always accepted (late arrival is the
        # normal case for provider redelivery); future-skew is evidence tampering
        now = datetime.now(timezone.utc)
        if v > now + MAX_FUTURE_SKEW:
            raise ValueError(
                f"event_timestamp more than {MAX_FUTURE_SKEW} in the future"
            )
        return v


class PaymentEventOut(BaseModel):
    model_config = {"from_attributes": True, "populate_by_name": True}

    event_id: str
    provider_event_id: str
    event_type: str
    source: str
    status: str
    event_timestamp: UtcDatetime
    reference_id: str | None
    latency_ms: int | None
    metadata: dict[str, Any] | None = Field(
        default=None, validation_alias="event_metadata", serialization_alias="metadata"
    )


class PaymentEventBatchRequest(BaseModel):
    events: list[PaymentEventIn] = Field(min_length=1, max_length=200)


class PaymentEventBatchResponse(BaseModel):
    transaction_id: str
    created: int
    duplicates: int
    events: list[PaymentEventOut]

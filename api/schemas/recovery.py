"""
api/schemas/recovery.py — request/response models for the recovery API.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from api.schemas.transaction import UtcDatetime


class RecoveryRequest(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=64, examples=["TXN-123456"])


class RecoveryDecisionResponse(BaseModel):
    transaction_id: str
    decision: str = Field(
        description="LIMIT_RELEASED | MANUAL_REVIEW | RECOVERY_REJECTED",
        examples=["LIMIT_RELEASED"],
    )
    safe_to_release: bool
    safe_to_release_probability: float
    risk_score: float
    reason: str
    decided_by: str = Field(examples=["SYSTEM"])
    decided_at: UtcDatetime
    current_state: str
    already_applied: bool = Field(
        description="true when this call replayed an existing decision (idempotency)"
    )

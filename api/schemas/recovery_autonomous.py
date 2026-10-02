"""
api/schemas/recovery_autonomous.py — Stage 8 DECISION-LOGIC schemas.

DESIGN PRINCIPLE: AI identifies/assesses; EXECUTION passes through a
deterministic pipeline:

    decision policy -> safety gate -> idempotency -> verification

These models carry the outputs of that pipeline. Everything here is pure
data — no DB, no provider calls. Blocked-reason codes are STABLE strings:
they are logged and persisted, so their values must never change once
released.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
ACTION_RELEASE_LIMIT = "RELEASE_LIMIT"
ACTION_NO_ACTION = "NO_ACTION"
ACTION_MANUAL_REVIEW = "MANUAL_REVIEW"

ACTIONS = (
    ACTION_RELEASE_LIMIT,
    ACTION_NO_ACTION,
    ACTION_MANUAL_REVIEW,
)

# ---------------------------------------------------------------------------
# Recovery execution statuses
# ---------------------------------------------------------------------------
STATUS_PENDING = "PENDING"
STATUS_EXECUTING = "EXECUTING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_BLOCKED = "BLOCKED"
STATUS_VERIFICATION_PENDING = "VERIFICATION_PENDING"
STATUS_VERIFIED = "VERIFIED"

RECOVERY_STATUSES = (
    STATUS_PENDING,
    STATUS_EXECUTING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_BLOCKED,
    STATUS_VERIFICATION_PENDING,
    STATUS_VERIFIED,
)

# ---------------------------------------------------------------------------
# Blocked-reason codes (stable, logged + persisted — never rename these)
# ---------------------------------------------------------------------------
BLOCK_ALREADY_SUCCESS = "ALREADY_SUCCESS"
BLOCK_ALREADY_RECOVERED = "ALREADY_RECOVERED"
BLOCK_NEW_SUCCESSFUL_SETTLEMENT = "NEW_SUCCESSFUL_SETTLEMENT"
BLOCK_DOUBLE_DEDUCTION = "DOUBLE_DEDUCTION"
BLOCK_RISK_NO_LONGER_PERMITS = "RISK_NO_LONGER_PERMITS"
BLOCK_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
BLOCK_NOT_ELIGIBLE = "NOT_ELIGIBLE"
BLOCK_PROVIDER_REJECTED = "PROVIDER_REJECTED"

BLOCKED_REASONS = (
    BLOCK_ALREADY_SUCCESS,
    BLOCK_ALREADY_RECOVERED,
    BLOCK_NEW_SUCCESSFUL_SETTLEMENT,
    BLOCK_DOUBLE_DEDUCTION,
    BLOCK_RISK_NO_LONGER_PERMITS,
    BLOCK_INSUFFICIENT_EVIDENCE,
    BLOCK_NOT_ELIGIBLE,
    BLOCK_PROVIDER_REJECTED,
)


class RecoveryDecision(BaseModel):
    """Output of the deterministic decision policy (Stage 8)."""

    transaction_id: str
    eligible: bool
    action: str  # ACTIONS value
    decision_reason: str
    blocked_reason: str | None = None  # BLOCK_* code, when not eligible
    risk_level: str
    anomaly_type: str
    root_cause: str | None = None
    # evidence codes the policy relied on (from the EVIDENCE_CODES vocabulary
    # where applicable)
    required_evidence: list[str] = []
    risk_assessment_id: str | None = None
    policy_version: str
    created_at: datetime


class SafetyGateResult(BaseModel):
    """Output of the independent pre-execution safety gate.

    The gate NEVER trusts earlier conclusions — it re-derives everything
    from fresh events/reconstruction passed in by the caller.
    """

    allowed: bool
    blocked_reason: str | None = None  # BLOCK_* code
    # [{name, passed, detail}] — one entry per check, in execution order
    checks: list[dict]
    checked_at: datetime


class VerificationResult(BaseModel):
    """Output of the post-execution verification step."""

    passed: bool
    # [{name, passed, detail}] — one entry per check, in execution order
    checks: list[dict]
    verified_at: datetime

"""
api/services/recovery_decision_policy.py — Stage 8 DECISION slice:
the versioned deterministic recovery decision policy (POLICY_VERSION
"autonomous-v1").

PURE logic: given the Transaction-like object, the Stage-7 RiskAssessment
and the Stage-6 ReconstructionResult, produce ONE RecoveryDecision.
NO DB writes, NO provider calls. When uncertain: DO NOT RECOVER.

Decision table — FIRST MATCH WINS (rule ids R-A..R-F, then DEFAULT):

  R-A  recovery_candidate AND anomaly GENUINE_FAILURE AND risk in
       (LOW, MEDIUM) AND debit CONFIRMED AND settlement in
       (NOT_OBSERVED, NOT_CONFIRMED, FAILED)
         -> ELIGIBLE, action RELEASE_LIMIT.
         Money provably left the customer, the failure is genuine, risk is
         bounded, and settlement was never confirmed — safe to release.

  R-B  recovery_candidate AND anomaly GENUINE_FAILURE AND risk in
       (HIGH, CRITICAL)
         -> NOT eligible, action MANUAL_REVIEW,
            blocked BLOCK_RISK_NO_LONGER_PERMITS.
         The policy allows only LOW/MEDIUM risk to move autonomously.

  R-C  anomaly SUCCESSFUL_BUT_UNCONFIRMED
         -> NOT eligible, action MANUAL_REVIEW,
            blocked BLOCK_NOT_ELIGIBLE.
         Funds moved — manual reconciliation, never recovery.

  R-D  anomaly DOUBLE_DEDUCTION | DUPLICATE_TRANSACTION | FALSE_COMPLAINT
       | SUSPICIOUS
         -> NOT eligible, action NO_ACTION,
            blocked DOUBLE_DEDUCTION -> BLOCK_DOUBLE_DEDUCTION,
            else -> BLOCK_NOT_ELIGIBLE (spec section 8 reasons per case).

  R-E  anomaly INCOMPLETE | UNKNOWN
         -> NOT eligible, action NO_ACTION,
            blocked BLOCK_INSUFFICIENT_EVIDENCE.
         Missing evidence produces uncertainty; uncertainty never recovers.

  R-F  anomaly NONE (clean success)
         -> NOT eligible, action NO_ACTION, blocked BLOCK_ALREADY_SUCCESS.

  DEFAULT (anything else — e.g. an out-of-vocabulary anomaly type)
         -> NOT eligible, action NO_ACTION, blocked BLOCK_NOT_ELIGIBLE.

``required_evidence`` lists the evidence codes that DROVE the decision —
for R-A these come from the assessment's own evidence trail (the codes the
policy actually relied on), keeping every decision traceable to observed
facts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from api.schemas.reconstruction import (
    ReconstructionResult,
    ROOT_CAUSE_NONE,
)
from api.schemas.recovery_autonomous import (
    ACTION_MANUAL_REVIEW,
    ACTION_NO_ACTION,
    ACTION_RELEASE_LIMIT,
    BLOCK_ALREADY_SUCCESS,
    BLOCK_DOUBLE_DEDUCTION,
    BLOCK_INSUFFICIENT_EVIDENCE,
    BLOCK_NOT_ELIGIBLE,
    BLOCK_RISK_NO_LONGER_PERMITS,
    RecoveryDecision,
)
from api.schemas.risk_assessment import (
    ANOMALY_DOUBLE_DEDUCTION,
    ANOMALY_DUPLICATE_TRANSACTION,
    ANOMALY_FALSE_COMPLAINT,
    ANOMALY_GENUINE_FAILURE,
    ANOMALY_INCOMPLETE,
    ANOMALY_NONE,
    ANOMALY_SUCCESSFUL_BUT_UNCONFIRMED,
    ANOMALY_SUSPICIOUS,
    ANOMALY_UNKNOWN,
    RISK_CRITICAL,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
)

POLICY_VERSION = "autonomous-v1"

# Risk levels allowed to move autonomously.
_AUTONOMOUS_RISK_LEVELS = (RISK_LOW, RISK_MEDIUM)

# Settlement statuses compatible with a recovery attempt.
_RECOVERABLE_SETTLEMENT = ("NOT_OBSERVED", "NOT_CONFIRMED", "FAILED")


def decide(
    tx: Any,
    assessment: Any,
    reconstruction: ReconstructionResult,
    *,
    now: datetime,
) -> RecoveryDecision:
    """Apply the FIRST-MATCH decision table. ``tx`` provides amount/context
    for reason wording; the decision itself rests on the assessment and the
    reconstruction only."""

    def _build(
        *,
        eligible: bool,
        action: str,
        reason: str,
        blocked_reason: str | None = None,
    ) -> RecoveryDecision:
        return RecoveryDecision(
            transaction_id=str(getattr(assessment, "transaction_id")),
            eligible=eligible,
            action=action,
            decision_reason=reason,
            blocked_reason=blocked_reason,
            risk_level=str(getattr(assessment, "risk_level")),
            anomaly_type=str(getattr(assessment, "anomaly_type")),
            root_cause=getattr(reconstruction, "root_cause", None),
            required_evidence=[
                e.code for e in getattr(assessment, "evidence", [])
            ],
            risk_assessment_id=getattr(assessment, "assessment_id", None),
            policy_version=POLICY_VERSION,
            created_at=now,
        )

    anomaly = getattr(assessment, "anomaly_type")
    risk = getattr(assessment, "risk_level")
    candidate = bool(getattr(assessment, "recovery_candidate"))

    # R-A — the one eligible path.
    if (
        candidate
        and anomaly == ANOMALY_GENUINE_FAILURE
        and risk in _AUTONOMOUS_RISK_LEVELS
        and reconstruction.customer_debit_status == "CONFIRMED"
        and reconstruction.settlement_status in _RECOVERABLE_SETTLEMENT
    ):
        return _build(
            eligible=True,
            action=ACTION_RELEASE_LIMIT,
            reason=(
                "customer debit confirmed, settlement "
                f"{reconstruction.settlement_status.lower().replace('_', ' ')} "
                f"with genuine failure ({reconstruction.root_cause}) at "
                f"{risk} risk — release limit autonomously"
            ),
        )

    # R-B — genuine failure but risk too high for autonomous movement.
    if (
        candidate
        and anomaly == ANOMALY_GENUINE_FAILURE
        and risk in (RISK_HIGH, RISK_CRITICAL)
    ):
        return _build(
            eligible=False,
            action=ACTION_MANUAL_REVIEW,
            reason=(
                f"genuine failure at {risk} risk — policy allows only "
                "LOW/MEDIUM risk to move autonomously"
            ),
            blocked_reason=BLOCK_RISK_NO_LONGER_PERMITS,
        )

    # R-C — funds moved; reconciliation, not recovery.
    if anomaly == ANOMALY_SUCCESSFUL_BUT_UNCONFIRMED:
        return _build(
            eligible=False,
            action=ACTION_MANUAL_REVIEW,
            reason=(
                "settlement confirmed without merchant confirmation — funds "
                "moved, manual reconciliation required"
            ),
            blocked_reason=BLOCK_NOT_ELIGIBLE,
        )

    # R-D — financial-integrity / fraud-pattern outcomes: no action.
    if anomaly in (
        ANOMALY_DOUBLE_DEDUCTION,
        ANOMALY_DUPLICATE_TRANSACTION,
        ANOMALY_FALSE_COMPLAINT,
        ANOMALY_SUSPICIOUS,
    ):
        if anomaly == ANOMALY_DOUBLE_DEDUCTION:
            return _build(
                eligible=False,
                action=ACTION_NO_ACTION,
                reason=(
                    "multiple customer debit confirmations — possible double "
                    "deduction requires manual financial review"
                ),
                blocked_reason=BLOCK_DOUBLE_DEDUCTION,
            )
        reasons = {
            ANOMALY_DUPLICATE_TRANSACTION: (
                "provider reference shared with another transaction — "
                "duplicate submission, no recovery action"
            ),
            ANOMALY_FALSE_COMPLAINT: (
                "payment completed successfully — customer-reported failure "
                "contradicted by the evidence chain"
            ),
            ANOMALY_SUSPICIOUS: (
                "unusual retry/attempt pattern without a determinable "
                "outcome — no autonomous action"
            ),
        }
        return _build(
            eligible=False,
            action=ACTION_NO_ACTION,
            reason=reasons[anomaly],
            blocked_reason=BLOCK_NOT_ELIGIBLE,
        )

    # R-E — uncertainty never recovers.
    if anomaly in (ANOMALY_INCOMPLETE, ANOMALY_UNKNOWN):
        return _build(
            eligible=False,
            action=ACTION_NO_ACTION,
            reason=(
                "missing evidence produces uncertainty — uncertainty never "
                "recovers funds autonomously"
            ),
            blocked_reason=BLOCK_INSUFFICIENT_EVIDENCE,
        )

    # R-F — clean success: nothing to recover.
    if anomaly == ANOMALY_NONE and reconstruction.root_cause == ROOT_CAUSE_NONE:
        return _build(
            eligible=False,
            action=ACTION_NO_ACTION,
            reason="payment completed successfully — no failure to recover",
            blocked_reason=BLOCK_ALREADY_SUCCESS,
        )

    # DEFAULT — never guess.
    return _build(
        eligible=False,
        action=ACTION_NO_ACTION,
        reason=(
            f"no policy rule matched (anomaly {anomaly}, risk {risk}) — "
            "defaulting to no action"
        ),
        blocked_reason=BLOCK_NOT_ELIGIBLE,
    )

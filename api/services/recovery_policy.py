"""
api/services/recovery_policy.py — deterministic recovery policy.

The recovery decision is a PURE, auditable function of
(ML assessment, transaction attributes, policy parameters). It is deliberately
separate from the ML model so thresholds and business rules can change
without retraining, and separate from any GenAI layer — GenAI will only ever
EXPLAIN decisions, never make them.

Order of evaluation (first match wins):
  1. hard business rules        -> RECOVERY_REJECTED
  2. ML safe + high confidence  -> LIMIT_RELEASED
  3. everything else            -> MANUAL_REVIEW
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from api.core.config import Settings


@dataclass(frozen=True)
class PolicyDecision:
    decision: str  # LIMIT_RELEASED | MANUAL_REVIEW | RECOVERY_REJECTED
    safe_to_release: bool
    reason: str


def evaluate_policy(tx, assessment: dict, settings: Settings) -> PolicyDecision:
    amount = Decimal(tx.amount)
    if amount > Decimal(str(settings.recovery_max_amount)):
        return PolicyDecision(
            decision="RECOVERY_REJECTED",
            safe_to_release=False,
            reason=(
                f"amount {amount} exceeds auto-release cap "
                f"{settings.recovery_max_amount}: manual handling required"
            ),
        )

    if tx.previous_failures > settings.recovery_max_previous_failures:
        return PolicyDecision(
            decision="RECOVERY_REJECTED",
            safe_to_release=False,
            reason=(
                f"previous_failures {tx.previous_failures} exceeds policy limit "
                f"{settings.recovery_max_previous_failures}"
            ),
        )

    if (
        assessment["safe_to_release"]
        and assessment["safe_to_release_probability"]
        >= settings.recovery_min_safe_probability
    ):
        return PolicyDecision(
            decision="LIMIT_RELEASED",
            safe_to_release=True,
            reason="Recovery conditions satisfied",
        )

    return PolicyDecision(
        decision="MANUAL_REVIEW",
        safe_to_release=False,
        reason="Recovery conditions not satisfied",
    )

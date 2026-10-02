"""
api/services/recovery_safety.py — Stage 8 pre-execution SAFETY GATE.

The gate is INDEPENDENT of the decision policy: it NEVER trusts earlier
conclusions (the assessment, the decision, the reconstruction the decision
was based on). It re-derives everything from FRESH data passed in by the
caller — fresh events, a fresh reconstruction, the current transaction
state and the current recovery row. The window between decision and
execution is exactly where a settlement can land; re-checking with fresh
facts closes that race (spec section 10: a settlement confirmed after the
assessment must block execution, not double-pay).

PURE: no DB reads (the caller supplies fresh data), no writes, no provider
calls. Checks run in a fixed order; the FIRST failure wins and its
BLOCK_* code is returned.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from api.schemas.recovery_autonomous import (
    BLOCK_ALREADY_RECOVERED,
    BLOCK_ALREADY_SUCCESS,
    BLOCK_DOUBLE_DEDUCTION,
    BLOCK_INSUFFICIENT_EVIDENCE,
    BLOCK_NEW_SUCCESSFUL_SETTLEMENT,
    BLOCK_RISK_NO_LONGER_PERMITS,
    SafetyGateResult,
)
from api.schemas.reconstruction import ROOT_CAUSE_NONE

# Event types / states / statuses the gate reasons about.
_DEBIT_CONFIRMED_EVENT = "CUSTOMER_DEBIT_CONFIRMED"
_SETTLEMENT_CONFIRMED_EVENT = "SETTLEMENT_CONFIRMED"

# Recovery-row statuses that mean a recovery is already in flight or done —
# executing another one would double-recover.
_IN_FLIGHT_OR_DONE_STATUSES = (
    "COMPLETED",
    "VERIFIED",
    "VERIFICATION_PENDING",
    "EXECUTING",
    "PENDING",
)


def check_safety(
    tx: Any,
    fresh_events: Sequence[Any],
    fresh_reconstruction: Any,
    latest_assessment: Any | None,
    existing_recovery_row: Any | None,
    *,
    now: datetime,
) -> SafetyGateResult:
    """Run the ordered checks; return allowed=False with the FIRST failing
    BLOCK_* code, or allowed=True when every check passes.

    ``tx``: object with a ``current_state`` attribute.
    ``fresh_events``: objects with ``event_type`` attributes (the events
        re-read NOW, not at assessment time).
    ``fresh_reconstruction``: a Stage-6 ReconstructionResult rebuilt from
        ``fresh_events``.
    ``latest_assessment``: the newest RiskAssessment (or None).
    ``existing_recovery_row``: a RecoveryActionRecord-like object (or None)
        with a ``status`` attribute.
    """
    checks: list[dict] = []

    def _record(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    def _fail(name: str, blocked_reason: str, detail: str) -> SafetyGateResult:
        _record(name, False, detail)
        return SafetyGateResult(
            allowed=False, blocked_reason=blocked_reason,
            checks=checks, checked_at=now,
        )

    # (1) transaction state — already fully successful or already recovered?
    state = str(getattr(tx, "current_state", ""))
    if state == "SUCCESS":
        return _fail(
            "transaction_state", BLOCK_ALREADY_SUCCESS,
            f"transaction state is {state}",
        )
    if state == "LIMIT_RELEASED":
        return _fail(
            "transaction_state", BLOCK_ALREADY_RECOVERED,
            f"transaction state is {state}",
        )
    _record("transaction_state", True, f"transaction state is {state}")

    # (2) idempotency — an in-flight or completed recovery row exists?
    if existing_recovery_row is not None:
        row_status = str(getattr(existing_recovery_row, "status", ""))
        if row_status in _IN_FLIGHT_OR_DONE_STATUSES:
            return _fail(
                "existing_recovery", BLOCK_ALREADY_RECOVERED,
                f"recovery row already in status {row_status}",
            )
        _record(
            "existing_recovery", True,
            f"existing recovery row status {row_status} does not block",
        )
    else:
        _record("existing_recovery", True, "no existing recovery row")

    # (3) THE race-condition check — did a settlement confirm since the
    # assessment? (spec section 10)
    settlement_status = getattr(fresh_reconstruction, "settlement_status", None)
    if settlement_status == "CONFIRMED":
        return _fail(
            "fresh_settlement", BLOCK_NEW_SUCCESSFUL_SETTLEMENT,
            "fresh reconstruction shows settlement CONFIRMED — the payment "
            "succeeded on its own",
        )
    _record(
        "fresh_settlement", True,
        f"settlement status {settlement_status} permits recovery",
    )

    # (4) double-deduction guard — count DISTINCT debit-confirmation events
    # in the FRESH event stream.
    debit_confirmed = [
        e for e in fresh_events
        if str(getattr(e, "event_type", "")) == _DEBIT_CONFIRMED_EVENT
    ]
    debit_count = len(debit_confirmed)
    if debit_count >= 2:
        return _fail(
            "debit_confirmations", BLOCK_DOUBLE_DEDUCTION,
            f"{debit_count} customer debit confirmations observed — "
            "possible double deduction",
        )
    _record(
        "debit_confirmations", True,
        f"{debit_count} customer debit confirmation(s)",
    )

    # (5) is there still a genuine, current failure with a live assessment?
    root_cause = getattr(fresh_reconstruction, "root_cause", None)
    if root_cause is None:
        return _fail(
            "current_failure", BLOCK_INSUFFICIENT_EVIDENCE,
            "fresh reconstruction has no root cause — nothing to recover "
            "against",
        )
    if latest_assessment is None:
        return _fail(
            "current_failure", BLOCK_INSUFFICIENT_EVIDENCE,
            "no risk assessment available — cannot confirm recovery "
            "candidacy",
        )
    if not bool(getattr(latest_assessment, "recovery_candidate", False)):
        return _fail(
            "current_failure", BLOCK_RISK_NO_LONGER_PERMITS,
            "latest assessment no longer marks this transaction as a "
            "recovery candidate",
        )
    _record(
        "current_failure", True,
        f"root cause {root_cause} with an active recovery-candidate "
        "assessment",
    )

    # (6) the failure must STILL be a failure — a clean success (root cause
    # NONE) has nothing to recover.
    if root_cause == ROOT_CAUSE_NONE:
        return _fail(
            "still_failing", BLOCK_ALREADY_SUCCESS,
            "fresh reconstruction shows a fully successful payment",
        )
    _record("still_failing", True, f"failure still present: {root_cause}")

    return SafetyGateResult(
        allowed=True, blocked_reason=None, checks=checks, checked_at=now,
    )

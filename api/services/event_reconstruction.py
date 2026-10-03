"""
api/services/event_reconstruction.py — PURE deterministic reconstruction
engine (Stage 6).

Given the stored PaymentEvent facts for one transaction, derive:

  * the per-stage status (BANK_DEBIT / GATEWAY / MERCHANT_CONFIRMATION /
    SETTLEMENT) from observed evidence,
  * the current stage and the last successfully confirmed stage,
  * a root cause (FIRST MATCH over a fixed priority order),
  * a deterministic confidence score in [0, 1],
  * the missing happy-path events and a human-readable evidence summary.

Integrity rules:

  * NO LLM, NO randomness, NO invented events — output is a pure function of
    the input event list and ``now``.
  * Absence of evidence is EXPLICIT: a stage with no events is NOT_OBSERVED
    and the evidence summary says so.
  * Uncertainty is never resolved by guessing: if evidence exists but no
    terminal outcome justifies a verdict, the root cause is INCOMPLETE
    ("final outcome unknown from available evidence"), never a fabricated
    failure.

This function performs NO database writes and NO Digital Twin append — the
route layer owns that side effect, and the GenAI explanation service calls
this function directly and MUST stay read-only.

Caching: Results are cached in memory keyed by evidence fingerprint
(transaction_id + sorted event IDs). Cache is invalidated when new payment
events are ingested for a transaction.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Sequence

from api.core.payment_lifecycle import (
    EVENT_TYPE_INFO,
    HAPPY_PATH_EVENTS,
    OUTCOME_CONFIRMED,
    OUTCOME_ERROR,
    OUTCOME_FAILED,
    OUTCOME_NOT_CONFIRMED,
    OUTCOME_PROGRESS,
    OUTCOME_TIMEOUT,
    STAGE_ORDER,
)
from api.schemas.reconstruction import (
    ROOT_CAUSE_CUSTOMER_DEBIT_FAILED,
    ROOT_CAUSE_GATEWAY_ERROR,
    ROOT_CAUSE_GATEWAY_TIMEOUT,
    ROOT_CAUSE_INCOMPLETE,
    ROOT_CAUSE_MERCHANT_CONFIRMATION_TIMEOUT,
    ROOT_CAUSE_MERCHANT_ERROR,
    ROOT_CAUSE_NONE,
    ROOT_CAUSE_SETTLEMENT_FAILED,
    ROOT_CAUSE_SETTLEMENT_NOT_CONFIRMED,
    STAGE_NOT_OBSERVED,
    STAGE_UNAVAILABLE,
    PaymentEventOut,
    ReconstructionResult,
)

# terminal outcomes: evidence of a DECISION about a stage (vs mere progress)
_TERMINAL_OUTCOMES = (
    OUTCOME_CONFIRMED,
    OUTCOME_FAILED,
    OUTCOME_TIMEOUT,
    OUTCOME_ERROR,
    OUTCOME_NOT_CONFIRMED,
)

# humanized names for NOT_OBSERVED / fallback evidence lines
_STAGE_LABELS = {
    "BANK_DEBIT": "Bank debit",
    "GATEWAY": "Gateway",
    "MERCHANT_CONFIRMATION": "Merchant confirmation",
    "SETTLEMENT": "Settlement",
}

# humanized root causes for the conclusion line
_ROOT_CAUSE_LABELS = {
    ROOT_CAUSE_CUSTOMER_DEBIT_FAILED: "customer debit failed",
    ROOT_CAUSE_GATEWAY_TIMEOUT: "gateway timeout",
    ROOT_CAUSE_GATEWAY_ERROR: "gateway error",
    ROOT_CAUSE_MERCHANT_CONFIRMATION_TIMEOUT: "merchant confirmation timeout",
    ROOT_CAUSE_MERCHANT_ERROR: "merchant error",
    ROOT_CAUSE_SETTLEMENT_FAILED: "settlement failed",
    ROOT_CAUSE_SETTLEMENT_NOT_CONFIRMED: "settlement not confirmed",
}

# In-memory cache: {cache_key: ReconstructionResult}
# Cache key = sha256(transaction_id + sorted event IDs)
_reconstruction_cache: dict[str, ReconstructionResult] = {}

# per-stage CONFIRMED evidence lines (exact wording per Stage-6 spec)
_CONFIRMED_LINES = {
    "BANK_DEBIT": "Customer debit was confirmed.",
    "GATEWAY": "Gateway response was received.",
    "MERCHANT_CONFIRMATION": "Merchant confirmation was received.",
    "SETTLEMENT": "Settlement was confirmed.",
}

# per-stage progress-only evidence lines
_PROGRESS_LINES = {
    "GATEWAY": "Gateway request was sent, but no response was observed.",
    "MERCHANT_CONFIRMATION": (
        "Merchant confirmation was requested but not received."
    ),
}


def _event_attr(event: Any, name: str) -> Any:
    return getattr(event, name)


def _sort_events(events: Sequence[Any]) -> list[Any]:
    """Order by event_timestamp; ties broken by storage id when the rows
    carry one, otherwise by insertion order (Python's sort is stable, so
    equal keys keep input order). Duplicate provider events (provider
    redelivery) are tolerated: they are kept in ordered_events but cannot
    change a stage's status, because a redelivery carries the same outcome."""
    indexed = list(enumerate(events))
    indexed.sort(
        key=lambda pair: (
            _event_attr(pair[1], "event_timestamp"),
            _event_attr(pair[1], "id") if hasattr(pair[1], "id") else pair[0],
        )
    )
    return [event for _, event in indexed]


def _stage_status(stage_events: list[Any]) -> str:
    """Status of one stage: the outcome of its most recent TERMINAL event;
    OBSERVED if the stage only has progress events; NOT_OBSERVED if empty.
    Duplicates (same provider_event_id redelivered) carry the same outcome
    and therefore cannot affect the result."""
    terminal = [
        e for e in stage_events if _event_attr(e, "status") in _TERMINAL_OUTCOMES
    ]
    if terminal:
        return str(_event_attr(terminal[-1], "status"))
    if stage_events:
        return OUTCOME_PROGRESS
    return STAGE_NOT_OBSERVED


def _stage_evidence_line(stage: str, status: str) -> str:
    label = _STAGE_LABELS[stage]
    if status == OUTCOME_CONFIRMED:
        return _CONFIRMED_LINES[stage]
    if status == OUTCOME_FAILED:
        return {
            "BANK_DEBIT": "Customer debit failed.",
            "SETTLEMENT": "Settlement failed.",
        }.get(stage, f"{label} failed.")
    if status == OUTCOME_TIMEOUT:
        return {
            "GATEWAY": "Gateway timeout occurred.",
            "MERCHANT_CONFIRMATION": "Merchant confirmation timeout occurred.",
        }.get(stage, f"{label} timeout occurred.")
    if status == OUTCOME_ERROR:
        return {
            "GATEWAY": "Gateway error occurred.",
            "MERCHANT_CONFIRMATION": "Merchant error occurred.",
        }.get(stage, f"{label} error occurred.")
    if status == OUTCOME_NOT_CONFIRMED:
        return {
            "SETTLEMENT": "Settlement not confirmed.",
        }.get(stage, f"{label} was not confirmed.")
    if status == OUTCOME_PROGRESS:
        return _PROGRESS_LINES.get(
            stage, f"{label} was initiated, but no outcome was observed."
        )
    return f"{label} evidence was not observed."


def _derive_root_cause(statuses: dict[str, str]) -> tuple[str, str | None]:
    """FIRST MATCH in fixed priority order (deterministic evidence rules):

      1. debit FAILED    -> CUSTOMER_DEBIT_FAILED (nothing downstream can be
                            trusted if the money never left the customer's
                            bank)
      2-3. gateway TIMEOUT / ERROR
      4-5. merchant TIMEOUT / ERROR
      6-7. settlement FAILED / NOT_CONFIRMED
      8. all four stages CONFIRMED -> NONE (full success)
      9. otherwise -> INCOMPLETE: evidence exists but no terminal outcome
         justifies a failure verdict — uncertainty is represented EXPLICITLY,
         never guessed (Step-16 integrity rule).
    """
    if statuses["BANK_DEBIT"] == OUTCOME_FAILED:
        return ROOT_CAUSE_CUSTOMER_DEBIT_FAILED, "BANK_DEBIT"
    if statuses["GATEWAY"] == OUTCOME_TIMEOUT:
        return ROOT_CAUSE_GATEWAY_TIMEOUT, "GATEWAY"
    if statuses["GATEWAY"] == OUTCOME_ERROR:
        return ROOT_CAUSE_GATEWAY_ERROR, "GATEWAY"
    if statuses["MERCHANT_CONFIRMATION"] == OUTCOME_TIMEOUT:
        return ROOT_CAUSE_MERCHANT_CONFIRMATION_TIMEOUT, "MERCHANT_CONFIRMATION"
    if statuses["MERCHANT_CONFIRMATION"] == OUTCOME_ERROR:
        return ROOT_CAUSE_MERCHANT_ERROR, "MERCHANT_CONFIRMATION"
    if statuses["SETTLEMENT"] == OUTCOME_FAILED:
        return ROOT_CAUSE_SETTLEMENT_FAILED, "SETTLEMENT"
    if statuses["SETTLEMENT"] == OUTCOME_NOT_CONFIRMED:
        return ROOT_CAUSE_SETTLEMENT_NOT_CONFIRMED, "SETTLEMENT"
    if all(statuses[stage] == OUTCOME_CONFIRMED for stage in STAGE_ORDER):
        return ROOT_CAUSE_NONE, None
    return ROOT_CAUSE_INCOMPLETE, None


def _compute_cache_key(transaction_id: str, events: Sequence[Any]) -> str:
    """Compute cache key from transaction ID and sorted event IDs. Events with
    no id attribute (test doubles) use their event_type + event_timestamp."""
    key_parts = [transaction_id]
    for event in events:
        if hasattr(event, "id") and event.id is not None:
            key_parts.append(str(event.id))
        else:
            # Test doubles: use event_type + timestamp as fingerprint
            key_parts.append(
                f"{_event_attr(event, 'event_type')}:{_event_attr(event, 'event_timestamp').isoformat()}"
            )
    key_parts.sort()
    return hashlib.sha256("|".join(key_parts).encode()).hexdigest()


def clear_reconstruction_cache(transaction_id: str | None = None) -> None:
    """Clear reconstruction cache. If transaction_id given, only clears entries
    for that transaction (prefix match). If None, clears entire cache."""
    if transaction_id is None:
        _reconstruction_cache.clear()
    else:
        # Remove all keys that start with the transaction_id hash prefix
        keys_to_remove = [
            key for key in _reconstruction_cache
            if key.startswith(hashlib.sha256(transaction_id.encode()).hexdigest()[:16])
        ]
        for key in keys_to_remove:
            _reconstruction_cache.pop(key, None)


def _confidence(statuses: dict[str, str], observed_types: set[str]) -> float:
    """Deterministic score in [0, 1], capped at 1.0:

        confidence = round(
            (observed happy-path events
             + stages with a TERMINAL event whose happy-path events are NOT
               fully covered by observed happy events)
            / len(HAPPY_PATH_EVENTS),
            2,
        )

    A stage's terminal event substitutes for that stage's missing happy event
    (e.g. GATEWAY_TIMEOUT proves the gateway decided even though
    GATEWAY_RESPONSE_RECEIVED never arrived).

    Worked examples (7 happy-path events):
      full success      -> 7/7                 = 1.0
      gateway timeout   -> (2 happy + 1)/7     = 0.43
      merchant timeout  -> (4 happy + 1)/7     = 0.71
      debit failure     -> (0 happy + 1)/7     = 0.14
      no events         -> 0/7                 = 0.0
    """
    total = len(HAPPY_PATH_EVENTS)
    score = sum(1 for et in HAPPY_PATH_EVENTS if et in observed_types)
    for stage in STAGE_ORDER:
        if statuses[stage] not in _TERMINAL_OUTCOMES:
            continue
        stage_happy = [
            et
            for et in HAPPY_PATH_EVENTS
            if EVENT_TYPE_INFO[et]["stage"] == stage
        ]
        if not all(et in observed_types for et in stage_happy):
            score += 1
    return min(round(score / total, 2), 1.0)


def reconstruct_from_events(
    transaction_id: str, events: Sequence[Any], now: datetime
) -> ReconstructionResult:
    """Pure function — NO db writes, NO twin append (the route layer owns
    that, and the GenAI explanation service calls this directly and MUST stay
    read-only). ``events`` are objects with PaymentEvent attributes (ORM rows
    or lightweight test doubles).

    Algorithm:
      1. sort by event_timestamp (stable; id tiebreak) -> ordered_events
      2. per stage in STAGE_ORDER: status = outcome of the most recent
         TERMINAL event, OBSERVED if only progress events, NOT_OBSERVED if
         no events (duplicates tolerated — same outcome, no effect)
      3. current_stage = furthest stage with any evidence (UNAVAILABLE if none)
      4. root cause = FIRST MATCH in the fixed priority order documented on
         _derive_root_cause (NONE only when all four stages are CONFIRMED;
         INCOMPLETE when evidence exists but no terminal outcome)
      5. last_successful_stage = furthest CONFIRMED stage
      6. missing_events = HAPPY_PATH_EVENTS never observed, in order
      7. confidence = documented deterministic formula
      8. evidence_summary = one line per stage in STAGE_ORDER + a conclusion

    Results are cached in memory by evidence fingerprint. Cache hit returns
    immediately without recomputation.
    """
    # Check cache first
    cache_key = _compute_cache_key(transaction_id, events)
    if cache_key in _reconstruction_cache:
        return _reconstruction_cache[cache_key]

    # Cache miss - compute reconstruction
    ordered = _sort_events(events)

    by_stage: dict[str, list[Any]] = {stage: [] for stage in STAGE_ORDER}
    observed_types: set[str] = set()
    for event in ordered:
        info = EVENT_TYPE_INFO.get(_event_attr(event, "event_type"))
        if info is not None and info["stage"] in by_stage:
            by_stage[info["stage"]].append(event)
        observed_types.add(_event_attr(event, "event_type"))

    statuses = {
        stage: _stage_status(stage_events)
        for stage, stage_events in by_stage.items()
    }

    current_stage = STAGE_UNAVAILABLE
    for stage in STAGE_ORDER:
        if by_stage[stage]:
            current_stage = stage

    root_cause, failure_stage = _derive_root_cause(statuses)

    last_successful_stage = None
    for stage in STAGE_ORDER:
        if statuses[stage] == OUTCOME_CONFIRMED:
            last_successful_stage = stage

    missing_events = [et for et in HAPPY_PATH_EVENTS if et not in observed_types]

    evidence_summary = [
        _stage_evidence_line(stage, statuses[stage]) for stage in STAGE_ORDER
    ]
    if root_cause == ROOT_CAUSE_NONE:
        evidence_summary.append("Payment completed successfully.")
    elif root_cause == ROOT_CAUSE_INCOMPLETE:
        evidence_summary.append(
            "Payment flow is incomplete; final outcome unknown from available evidence."
        )
    else:
        evidence_summary.append(f"Root cause: {_ROOT_CAUSE_LABELS[root_cause]}.")

    result = ReconstructionResult(
        transaction_id=transaction_id,
        ordered_events=[PaymentEventOut.model_validate(e) for e in ordered],
        current_stage=current_stage,
        last_successful_stage=last_successful_stage,
        failure_stage=failure_stage,
        root_cause=root_cause,
        customer_debit_status=statuses["BANK_DEBIT"],
        gateway_status=statuses["GATEWAY"],
        merchant_confirmation_status=statuses["MERCHANT_CONFIRMATION"],
        settlement_status=statuses["SETTLEMENT"],
        reconstruction_confidence=_confidence(statuses, observed_types),
        missing_events=missing_events,
        evidence_summary=evidence_summary,
        reconstructed_at=now,
        digital_twin_event_recorded=False,
    )

    # Cache the result
    _reconstruction_cache[cache_key] = result
    return result

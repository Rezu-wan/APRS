"""
api/core/state_machine.py — the transaction lifecycle state machine.

The single authority for which state transitions are legal. Services must go
through validate_transition / transition_path; arbitrary state changes are
rejected with InvalidStateTransitionError (HTTP 400).

Lifecycle (recovery branch depends on the ML/policy decision):

    INITIATED -> PROCESSING -> SUCCESS
                            -> FAILED -----> RISK_ASSESSED -> RECOVERY_PENDING
                                                               -> LIMIT_RELEASED
                                                               -> MANUAL_REVIEW
                                                               -> RECOVERY_REJECTED
                            -> STALLED (rests; may retry below, or recovery
                                 |          request assesses on demand)
                                 v
                            PROCESSING (retry -> SUCCESS / FAILED / STALLED)

    FAILED is auto-assessed on arrival (terminal outcome). A STALLED
    transaction RESTS in STALLED — it may resolve via a PROCESSING retry
    event, or an explicit recovery request runs the assessment on demand.
    MANUAL_REVIEW -> LIMIT_RELEASED | RECOVERY_REJECTED   (support actions,
                                                           Stage 4+ endpoints)
"""

from __future__ import annotations

from api.core.exceptions import InvalidStateTransitionError


class TransactionState:
    INITIATED = "INITIATED"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    STALLED = "STALLED"
    RISK_ASSESSED = "RISK_ASSESSED"
    RECOVERY_PENDING = "RECOVERY_PENDING"
    LIMIT_RELEASED = "LIMIT_RELEASED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    RECOVERY_REJECTED = "RECOVERY_REJECTED"


ALL_STATES = frozenset(vars(TransactionState).values())

VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    TransactionState.INITIATED: frozenset({TransactionState.PROCESSING}),
    TransactionState.PROCESSING: frozenset(
        {TransactionState.SUCCESS, TransactionState.FAILED, TransactionState.STALLED}
    ),
    TransactionState.FAILED: frozenset({TransactionState.RISK_ASSESSED}),
    TransactionState.STALLED: frozenset(
        {TransactionState.RISK_ASSESSED, TransactionState.PROCESSING}
    ),
    TransactionState.RISK_ASSESSED: frozenset({TransactionState.RECOVERY_PENDING}),
    TransactionState.RECOVERY_PENDING: frozenset(
        {
            TransactionState.LIMIT_RELEASED,
            TransactionState.MANUAL_REVIEW,
            TransactionState.RECOVERY_REJECTED,
        }
    ),
    TransactionState.MANUAL_REVIEW: frozenset(
        {TransactionState.LIMIT_RELEASED, TransactionState.RECOVERY_REJECTED}
    ),
    # terminal
    TransactionState.SUCCESS: frozenset(),
    TransactionState.LIMIT_RELEASED: frozenset(),
    TransactionState.RECOVERY_REJECTED: frozenset(),
}

# Event type emitted for landing in each state (Digital Twin audit vocabulary)
STATE_EVENT_TYPES = {
    TransactionState.INITIATED: "TRANSACTION_CREATED",
    TransactionState.PROCESSING: "PAYMENT_PROCESSING",
    TransactionState.SUCCESS: "PAYMENT_SUCCEEDED",
    TransactionState.FAILED: "PAYMENT_FAILED",
    TransactionState.STALLED: "PAYMENT_STALLED",
    TransactionState.RISK_ASSESSED: "ML_RISK_ASSESSED",
    TransactionState.RECOVERY_PENDING: "RECOVERY_CHECKED",
    TransactionState.LIMIT_RELEASED: "LIMIT_RELEASED",
    TransactionState.MANUAL_REVIEW: "MANUAL_REVIEW_TRIGGERED",
    TransactionState.RECOVERY_REJECTED: "RECOVERY_REJECTED",
}

MAX_TRANSITION_PATH = 4  # chains are short by construction; guards against cycles


def validate_transition(current: str, target: str) -> None:
    if current not in VALID_TRANSITIONS:
        raise InvalidStateTransitionError(f"unknown current state: {current!r}")
    if target not in ALL_STATES:
        raise InvalidStateTransitionError(f"unknown target state: {target!r}")
    if target not in VALID_TRANSITIONS[current]:
        raise InvalidStateTransitionError(
            f"illegal transition {current} -> {target}"
        )


def transition_path(current: str, target: str) -> list[str]:
    """Shortest legal chain of states from current to target (inclusive).

    Used so a single API event can walk e.g. INITIATED -> PROCESSING -> FAILED,
    recording one Digital Twin event per hop. Raises if unreachable.
    """
    if current == target:
        return [current]
    # BFS over the (tiny) transition graph
    from collections import deque

    queue = deque([[current]])
    while queue:
        path = queue.popleft()
        node = path[-1]
        for nxt in sorted(VALID_TRANSITIONS.get(node, frozenset())):
            if nxt in path:  # graph is acyclic, but stay defensive
                continue
            new_path = path + [nxt]
            if nxt == target:
                if len(new_path) > MAX_TRANSITION_PATH:
                    break
                return new_path
            queue.append(new_path)
    raise InvalidStateTransitionError(
        f"no legal transition path from {current} to {target}"
    )

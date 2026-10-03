"""
api/services/temporal.py — Stage 11 Phase 11B: Temporal Digital Twin.

Historical state reconstruction: "what did the system KNOW at time T?"

THE rule (spec §6-7): a query at time T uses ONLY evidence with
``event_timestamp <= T``. A future event must NEVER influence a historical
reconstruction. The count of excluded later events is REPORTED as
uncertainty, never used.

Design:
  * The SAME deterministic reconstruction engine as the live path
    (event_reconstruction.reconstruct_from_events) runs on the
    time-filtered event set — stage statuses, root cause, confidence and
    missing_events therefore reflect ONLY what was known at T.
  * state_then is derived from the Digital Twin timeline filtered to
    ``timestamp <= T`` — the LAST such event's new_state — NEVER from
    Transaction.current_state, which is the mutable current state. With no
    twin events at/before T the honest answer is the birth state INITIATED.
  * Purity: state_at performs NO writes (no twin appends, no assessments).
    Like the GenAI explanation path, it MUST stay read-only.

Naive datetimes returned by SQLite are treated as UTC (same convention as
behavioral._aware / relationship._as_utc).
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.core.state_machine import TransactionState
from api.db.models import DigitalTwinEvent, PaymentEvent, Transaction
from api.services.digital_twin import get_timeline
from api.services.event_reconstruction import reconstruct_from_events

BIRTH_STATE = TransactionState.INITIATED

_EXCLUSION_NOTE = (
    "{n} later event(s) exist but are EXCLUDED from this historical view — "
    "future evidence never rewrites the past."
)
_NO_EVIDENCE_NOTE = "no evidence existed at this time"


def _aware(dt: datetime) -> datetime:
    """SQLite may hand back naive datetimes; treat stored wall time as UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class TemporalReconstruction(BaseModel):
    """Reconstruction result restricted to pre-T evidence."""

    root_cause: str
    reconstruction_confidence: float
    stages: dict[str, str]  # bank_debit / gateway / merchant_confirmation / settlement
    missing_events: list[str]


class TemporalStateReport(BaseModel):
    """Historical state of one transaction as of time T."""

    transaction_id: str
    as_of: str  # echoed query instant, tz-aware ISO-8601
    state_then: str
    last_twin_event_type: str | None
    observed_event_count: int
    excluded_event_count: int
    reconstruction: TemporalReconstruction
    uncertainty_note: str | None


def state_at(db: Session, transaction: Transaction, at: datetime) -> TemporalStateReport:
    """Pure historical read — NO writes (no twin appends, no assessments).

    ``at`` may be naive (assumed UTC) or aware; it is normalized before any
    comparison so SQLite-naive event timestamps compare correctly.
    """
    at_aware = _aware(at)

    events = (
        db.query(PaymentEvent)
        .filter(PaymentEvent.transaction_id == transaction.transaction_id)
        .all()
    )
    observed = [e for e in events if _aware(e.event_timestamp) <= at_aware]
    excluded = len(events) - len(observed)

    result = reconstruct_from_events(
        transaction.transaction_id, observed, at_aware
    )

    timeline = get_timeline(db, transaction.transaction_id)
    past_twin_events = [
        e for e in timeline if _aware(e.timestamp) <= at_aware
    ]
    if past_twin_events:
        last_twin = past_twin_events[-1]  # get_timeline orders by (timestamp, id)
        state_then = last_twin.new_state
        last_twin_event_type = last_twin.event_type
    else:
        state_then = BIRTH_STATE
        last_twin_event_type = None

    notes: list[str] = []
    if excluded > 0:
        notes.append(_EXCLUSION_NOTE.format(n=excluded))
    if len(observed) == 0 and excluded == 0:
        notes.append(_NO_EVIDENCE_NOTE)

    return TemporalStateReport(
        transaction_id=transaction.transaction_id,
        as_of=at_aware.isoformat(),
        state_then=state_then,
        last_twin_event_type=last_twin_event_type,
        observed_event_count=len(observed),
        excluded_event_count=excluded,
        reconstruction=TemporalReconstruction(
            root_cause=result.root_cause,
            reconstruction_confidence=result.reconstruction_confidence,
            stages={
                "bank_debit": result.customer_debit_status,
                "gateway": result.gateway_status,
                "merchant_confirmation": result.merchant_confirmation_status,
                "settlement": result.settlement_status,
            },
            missing_events=list(result.missing_events),
        ),
        uncertainty_note="; ".join(notes) if notes else None,
    )

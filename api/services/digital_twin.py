"""
api/services/digital_twin.py — Digital Twin event log.

The twin is the append-only audit history of a transaction. Events are only
ever INSERTed here; there is no update or delete path anywhere in the code
base, and no API surface mutates past events. Every state change recorded by
the services lands here in the SAME database transaction as the state change
itself (see transaction_service / recovery_service), so the twin can never
diverge from the live state.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.core.state_machine import STATE_EVENT_TYPES
from api.db.models import DigitalTwinEvent, Transaction


def append_event(
    db: Session,
    tx: Transaction,
    *,
    new_state: str,
    previous_state: str | None = None,
    event_type: str | None = None,
    ml: dict | None = None,
    reason: str | None = None,
    event_metadata: dict | None = None,
) -> DigitalTwinEvent:
    """Insert one audit event (no commit — the caller owns the transaction)."""
    event = DigitalTwinEvent(
        transaction_id=tx.transaction_id,
        event_type=event_type or STATE_EVENT_TYPES[new_state],
        previous_state=previous_state,
        new_state=new_state,
        failure_prediction=ml.get("failure_prediction") if ml else None,
        risk_score=ml.get("risk_score") if ml else None,
        safe_to_release_probability=(
            ml.get("safe_to_release_probability") if ml else None
        ),
        safe_to_release=ml.get("safe_to_release") if ml else None,
        reason=reason,
        event_metadata=event_metadata,
    )
    db.add(event)
    return event


def get_timeline(db: Session, transaction_id: str) -> list[DigitalTwinEvent]:
    stmt = (
        select(DigitalTwinEvent)
        .where(DigitalTwinEvent.transaction_id == transaction_id)
        .order_by(DigitalTwinEvent.timestamp.asc(), DigitalTwinEvent.id.asc())
    )
    return list(db.scalars(stmt))

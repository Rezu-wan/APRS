"""
api/db/models.py — SQLAlchemy ORM models.

transactions         current transaction state + latest ML assessment
digital_twin_events  append-only audit timeline (Digital Twin); rows are only
                     ever INSERTed — no code path updates or deletes them
recovery_decisions   one immutable decision row per transaction (UNIQUE
                     transaction_id) — this constraint is what makes
                     /recovery/release-limit idempotent, including under
                     concurrent duplicate requests
ai_explanations      derived GenAI explanation cache (NON-AUTHORITATIVE) —
                     never part of the decision path; the only table the
                     explanation layer writes
payment_events       Stage 6 payment-DOMAIN evidence stream (debit/gateway/
                     merchant/settlement) — append-only, replay-safe

Timestamps are timezone-aware UTC. UUIDs are stored as 36-char strings for
portability across PostgreSQL and SQLite.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.core.state_machine import TransactionState
from api.db.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_event_id() -> str:
    return str(uuid.uuid4())


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    merchant_id: Mapped[str] = mapped_column(String(64), index=True)

    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="BDT")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )

    # latest raw transaction attributes (updated when new events arrive)
    gateway_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    network_quality: Mapped[str] = mapped_column(String(20), default="Good")
    previous_failures: Mapped[int] = mapped_column(Integer, default=0)
    account_age_days: Mapped[int] = mapped_column(Integer, default=0)
    failure_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)

    current_state: Mapped[str] = mapped_column(
        String(32), default=TransactionState.INITIATED, index=True
    )

    # latest ML assessment (null until the transaction failed/stalled and the
    # ML engine ran) — names match the API contract; risk_score is the model's
    # recovery-risk output (ml.predict returns it as "recovery_risk")
    failure_prediction: Mapped[str | None] = mapped_column(String(50), nullable=True)
    failure_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    safe_to_release_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    safe_to_release: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    events: Mapped[list["DigitalTwinEvent"]] = relationship(
        back_populates="transaction",
        cascade="all, delete-orphan",
        order_by="DigitalTwinEvent.timestamp",
    )
    recovery_decision: Mapped["RecoveryDecision | None"] = relationship(
        back_populates="transaction", uselist=False
    )


class DigitalTwinEvent(Base):
    __tablename__ = "digital_twin_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(
        String(36), unique=True, default=new_event_id, index=True
    )
    transaction_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("transactions.transaction_id"), index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )

    event_type: Mapped[str] = mapped_column(String(40), index=True)
    previous_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    new_state: Mapped[str] = mapped_column(String(32))

    # ML snapshot at event time (null for non-ML events)
    failure_prediction: Mapped[str | None] = mapped_column(String(50), nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    safe_to_release_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    safe_to_release: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    transaction: Mapped[Transaction] = relationship(back_populates="events")


class RecoveryDecision(Base):
    __tablename__ = "recovery_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("transactions.transaction_id"), unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    decision: Mapped[str] = mapped_column(String(32))  # LIMIT_RELEASED | MANUAL_REVIEW | RECOVERY_REJECTED
    safe_to_release: Mapped[bool] = mapped_column(Boolean)
    safe_to_release_probability: Mapped[float] = mapped_column(Float)
    risk_score: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)

    decided_by: Mapped[str] = mapped_column(String(32))  # acting role
    policy_snapshot: Mapped[dict] = mapped_column(JSON)

    transaction: Mapped[Transaction] = relationship(back_populates="recovery_decision")


class AIExplanation(Base):
    """Generated GenAI explanation cache. DERIVED, NON-AUTHORITATIVE data:
    never part of the decision path, never a substitute for the Digital Twin
    log. Multiple rows per transaction are kept (history); the latest row
    matching (transaction, language, audience, context_fingerprint) is reused."""

    __tablename__ = "ai_explanations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    explanation_id: Mapped[str] = mapped_column(
        String(36), unique=True, default=new_event_id, index=True
    )
    transaction_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("transactions.transaction_id"), index=True
    )
    language: Mapped[str] = mapped_column(String(2))
    audience: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(8))
    explanation: Mapped[str] = mapped_column(Text)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    context_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    transaction: Mapped[Transaction] = relationship()


class PaymentEvent(Base):
    """Payment-DOMAIN event (Stage 6): fine-grained debit/gateway/merchant/
    settlement evidence, distinct from the transaction-state Digital Twin.
    Append-only. Idempotency anchor is provider_event_id (UNIQUE): real payment
    systems redeliver events, so replays must be safe."""

    __tablename__ = "payment_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(
        String(36), unique=True, default=new_event_id, index=True
    )
    transaction_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("transactions.transaction_id"), index=True
    )
    provider_event_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(48))  # EVENT_TYPES value
    source: Mapped[str] = mapped_column(String(16))  # PaymentSource
    status: Mapped[str] = mapped_column(String(16))  # outcome constant
    # domain time — the AUTHORITATIVE ordering key (arrival order is not
    # guaranteed for redelivered provider events)
    event_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    reference_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    transaction: Mapped[Transaction] = relationship()

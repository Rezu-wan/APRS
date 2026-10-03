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
risk_assessments     Stage 7 hybrid risk assessment EVIDENCE (rules + ML),
                     never an action — history per transaction, fingerprint-
                     idempotent
recovery_actions     Stage 8 autonomous recovery action — SANDBOX execution
                     record; idempotency_key is the UNIQUE anchor
security_audit /     Stage 9 security audit trail + write-through persistence
sandbox_ledger_entries for the simulated sandbox ledger
customers /          synthetic-dataset reference data (read-only): customer
merchants            profiles and the merchant catalog the UI resolves ids
                     against

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
    UniqueConstraint,
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

    # descriptive payment attributes (from the payment itself, not risk
    # internals) — nullable: rows created before the dataset loader ran
    transaction_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)

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
    # Stage 11A event-correlation columns (all nullable — pre-existing rows
    # are backfilled with correlation_id = transaction_id by migration)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    causation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    transaction: Mapped[Transaction] = relationship()


class RiskAssessmentRecord(Base):
    """Stage 7 hybrid risk assessment — decision EVIDENCE for Stage 8, never
    an action. Multiple rows per transaction (history); the latest row
    matching the current evidence fingerprint is reused instead of
    re-computing and re-appending to the Digital Twin."""

    __tablename__ = "risk_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    transaction_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("transactions.transaction_id"), index=True
    )
    # sha256 over the assessment's input evidence — the idempotency anchor
    evidence_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    anomaly_type: Mapped[str] = mapped_column(String(32), index=True)
    risk_level: Mapped[str] = mapped_column(String(16), index=True)
    risk_score: Mapped[float] = mapped_column(Float)
    ml_anomaly_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    deterministic_risk_score: Mapped[float] = mapped_column(Float)
    # DECISION EVIDENCE for Stage 8 — Stage 7 never executes recovery
    recovery_candidate: Mapped[bool] = mapped_column(Boolean)
    recovery_block_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reconstruction_root_cause: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    reconstruction_confidence: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    customer_reported_failure: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence: Mapped[list] = mapped_column(JSON)
    triggered_rules: Mapped[list] = mapped_column(JSON)
    model_version: Mapped[str] = mapped_column(String(32))
    rule_version: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    transaction: Mapped[Transaction] = relationship()


class RecoveryActionRecord(Base):
    """Stage 8 autonomous recovery action — SANDBOX execution record. Idempotency
    anchor is idempotency_key (UNIQUE at DB level): sha256 of
    transaction_id + action + policy_version + risk-evidence fingerprint, so
    identical evidence replays safely while genuinely new evidence may warrant
    a new attempt. BLOCKED decisions are also recorded (provider never called)."""

    __tablename__ = "recovery_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recovery_id: Mapped[str] = mapped_column(
        String(36), unique=True, default=new_event_id, index=True
    )
    transaction_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("transactions.transaction_id"), index=True
    )
    # RELEASE_LIMIT | NO_ACTION | MANUAL_REVIEW
    action: Mapped[str] = mapped_column(String(24))
    # PENDING|EXECUTING|COMPLETED|FAILED|BLOCKED|VERIFICATION_PENDING|VERIFIED
    status: Mapped[str] = mapped_column(String(24), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    requested_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    released_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    currency: Mapped[str] = mapped_column(String(3), default="BDT")

    policy_version: Mapped[str] = mapped_column(String(24))
    executor_version: Mapped[str | None] = mapped_column(String(24), nullable=True)
    verifier_version: Mapped[str | None] = mapped_column(String(24), nullable=True)
    risk_assessment_id: Mapped[str | None] = mapped_column(String(36), index=True)

    decision_reason: Mapped[str] = mapped_column(Text)
    # 96: dataset blocked-reason sentences run to 68 chars
    blocked_reason: Mapped[str | None] = mapped_column(String(96), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # sandbox provider ("mock") + its result/verification snapshots
    provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verification_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    transaction: Mapped[Transaction] = relationship()


class SecurityAuditRecord(Base):
    """Stage 9 security audit trail — WHO did WHAT to WHICH resource, for the
    actions that matter to the safety model. Distinct from the Digital Twin:
    the twin is the payment narrative; this is the security/ops record.
    Never logs secrets — actor_id is the key NAME (api_key_admin), not the key."""

    __tablename__ = "security_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    audit_id: Mapped[str] = mapped_column(
        String(36), unique=True, default=new_event_id, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    actor_type: Mapped[str] = mapped_column(String(16))  # ROLE value or "ANONYMOUS"
    actor_id: Mapped[str] = mapped_column(String(48))  # key_name or customer_id
    action: Mapped[str] = mapped_column(String(40), index=True)  # AUDIT_* constant
    resource_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    result: Mapped[str] = mapped_column(String(12))  # ALLOWED | DENIED
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    audit_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSON, nullable=True
    )


class SandboxLedgerEntry(Base):
    """Write-through persistence for the SIMULATED sandbox ledger (Stage 8's
    in-memory ledger remains the live state; this table survives process
    restarts so recovery references stay verifiable). Still 100% simulated."""

    __tablename__ = "sandbox_ledger_entries"

    transaction_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    held_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0")
    )
    released_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0")
    )
    currency: Mapped[str] = mapped_column(String(3), default="BDT")
    provider_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CustomerReport(Base):
    """Customer problem report — DECISION EVIDENCE ONLY, never an action.

    A customer files a problem against one of THEIR transactions (double
    charge, failed payment, …) plus where in the payment flow it happened.
    The report never changes transaction state, never influences the
    recovery policy by itself, and is never a decision: it is stored
    evidence that staff can see (and that a later risk assessment may take
    into account through the existing customer_reported_failure input, which
    remains SYSTEM/ADMIN-only). One report per (transaction, customer):
    re-filing replays the stored report (already_reported=true)."""

    __tablename__ = "customer_reports"
    __table_args__ = (
        UniqueConstraint("transaction_id", "customer_id", name="uq_customer_report"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[str] = mapped_column(
        String(36), unique=True, default=new_event_id, index=True
    )
    transaction_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("transactions.transaction_id"), index=True
    )
    customer_id: Mapped[str] = mapped_column(String(64), index=True)

    # closed vocabularies — validated at the schema layer
    problem_type: Mapped[str] = mapped_column(String(32))
    stage: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text, default="")

    # OPEN | UNDER_REVIEW | RESOLVED | REJECTED — moved by STAFF workflows
    # only; filing always creates OPEN.
    status: Mapped[str] = mapped_column(String(16), default="OPEN")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    transaction: Mapped[Transaction] = relationship()


class Customer(Base):
    """Customer identity/profile (synthetic dataset). Read-only reference data:
    the API only ever serves a CUSTOMER their OWN row — auth still comes from
    the X-API-Key binding, never from this table."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(40), default="")
    country: Mapped[str] = mapped_column(String(2), default="BD")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="active")
    segment: Mapped[str] = mapped_column(String(16), default="retail")
    risk_profile: Mapped[str] = mapped_column(String(16), default="LOW")
    archetype: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Merchant(Base):
    """Merchant catalog (synthetic dataset) — resolution data so the UI can
    show a merchant's name/category instead of the raw MER- id."""

    __tablename__ = "merchants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40), default="")
    country: Mapped[str] = mapped_column(String(2), default="BD")
    risk_tier: Mapped[str] = mapped_column(String(16), default="LOW")

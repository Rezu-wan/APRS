"""
api/schemas/support.py — request/response models for the support workspace.

The support API is a customer-care view over the SAME data the rest of the
app serves — it never duplicates state and never becomes a second authority.
Every response is derived from real tables (transactions, recovery_actions,
risk_assessments, customers/accounts/... registries, support_cases).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.transaction import UtcDatetime


class CaseStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING_FOR_CUSTOMER = "WAITING_FOR_CUSTOMER"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class CasePriority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class SupportCaseCreate(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=64)
    subject: str = Field(min_length=3, max_length=140)
    description: str | None = Field(default=None, max_length=4000)
    priority: CasePriority = CasePriority.MEDIUM
    assignee: str | None = Field(default=None, max_length=64)


class SupportCaseUpdate(BaseModel):
    status: CaseStatus | None = None
    priority: CasePriority | None = None
    assignee: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=2000)


class SupportCaseNote(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    at: UtcDatetime
    by: str
    role: str
    text: str


class SupportCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_id: str
    transaction_id: str
    customer_id: str
    subject: str
    description: str | None = None
    status: str
    priority: str
    created_by: str
    assignee: str | None = None
    notes: list[SupportCaseNote] = []
    created_at: UtcDatetime
    updated_at: UtcDatetime
    resolved_at: UtcDatetime | None = None


class SupportCaseListResponse(BaseModel):
    cases: list[SupportCaseResponse]
    total: int


class CustomerSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_id: str
    full_name: str
    email: str
    phone: str | None = None
    status: str
    segment: str | None = None
    risk_profile: str | None = None
    country: str | None = None


class CustomerSearchResult(CustomerSummary):
    transaction_count: int
    failed_transaction_count: int
    last_activity_at: UtcDatetime | None = None
    open_case_count: int
    dataset_known: bool = Field(
        description="False when the customer exists only as a transaction "
        "user_id (no registry row) — identity fields then fall back to "
        "honest unknowns."
    )


class CustomerSearchResponse(BaseModel):
    query: str
    results: list[CustomerSearchResult]
    total: int


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: str
    account_type: str
    currency: str
    balance: float
    status: str
    is_primary: bool
    last_activity_at: UtcDatetime | None = None


class BehaviorSignalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    signal_name: str
    signal_value: float | None = None
    unit: str | None = None
    signal_level: str
    window: str | None = None
    computed_at: UtcDatetime | None = None


class SupportTransactionSummary(BaseModel):
    """The support-facing slice of a transaction — human-readable context,
    not the full engineering contract."""

    model_config = ConfigDict(from_attributes=True)

    transaction_id: str
    # ORM attribute is user_id; the support contract exposes customer_id
    customer_id: str = Field(validation_alias="user_id")
    merchant_id: str
    amount: float
    currency: str
    timestamp: UtcDatetime
    current_state: str
    failure_reason: str | None = None
    risk_score: float | None = None
    # dataset enrichment (null for ingest-API transactions)
    channel: str | None = None
    country: str | None = None
    direction: str | None = None
    transaction_type: str | None = None


class TransactionListResponse(BaseModel):
    transactions: list[SupportTransactionSummary]
    total: int
    limit: int
    offset: int


class CustomerTransactionsResponse(BaseModel):
    customer_id: str
    transactions: list[SupportTransactionSummary]
    total: int


class CustomerAggregate(BaseModel):
    transaction_count: int
    failed_count: int
    recovered_count: int
    last_activity_at: UtcDatetime | None = None


class OpenCaseSummary(BaseModel):
    case_id: str
    subject: str
    status: str
    priority: str
    transaction_id: str
    created_at: UtcDatetime


class CustomerProfileResponse(BaseModel):
    customer: CustomerSummary
    accounts: list[AccountOut]
    aggregate: CustomerAggregate
    recent_transactions: list[SupportTransactionSummary]
    behavior_signals: list[BehaviorSignalOut]
    open_cases: list[OpenCaseSummary]
    dataset_known: bool = Field(
        description="False when the customer exists only as a transaction "
        "user_id (no registry row) — every registry field then falls back "
        "to honest unknowns."
    )


class QueueCounts(BaseModel):
    open: int
    in_progress: int
    waiting_for_customer: int
    escalated: int
    resolved: int
    closed: int


class NeedsAttentionItem(BaseModel):
    """One row of the 'needs attention' list: a recovery that ended blocked
    or in manual review, or an escalated case — the work a support agent
    picks up."""

    transaction_id: str
    customer_id: str
    amount: float
    currency: str
    timestamp: UtcDatetime
    failure_reason: str | None = None
    recovery_status: str | None = None
    blocked_reason: str | None = None
    risk_level: str | None = None
    anomaly_type: str | None = None
    open_case_id: str | None = None


class RecentActivityItem(BaseModel):
    transaction_id: str
    customer_id: str
    customer_name: str | None = None
    amount: float
    currency: str
    timestamp: UtcDatetime
    current_state: str
    failure_reason: str | None = None


class SupportOverviewResponse(BaseModel):
    cases_by_status: QueueCounts
    needs_attention: list[NeedsAttentionItem]
    recent_activity: list[RecentActivityItem]
    generated_at: UtcDatetime


class StreamTicketResponse(BaseModel):
    ticket: str
    expires_in_seconds: int


class StreamEventOut(BaseModel):
    """One SSE `data:` payload (JSON-encoded). Mirrors EventEnvelope for the
    frontend; `data` stays small — no raw payloads."""

    event_id: str
    event_type: str
    transaction_id: str
    occurred_at: UtcDatetime
    data: dict[str, Any] = {}

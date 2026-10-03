"""api/schemas/customer_report.py — request/response shapes for customer
problem reports. EVIDENCE ONLY: nothing here can change transaction state or
a recovery decision."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProblemType = Literal[
    "DOUBLE_CHARGED",       # customer was debited more than once
    "PAYMENT_FAILED",       # payment did not go through
    "MONEY_NOT_RECEIVED",   # counterparty/merchant never received the money
    "UNAUTHORIZED",         # customer does not recognise the transaction
    "OTHER",
]
ProblemStage = Literal[
    "CARD_DEBIT",            # problem at the bank debit step
    "GATEWAY",               # problem at the payment gateway
    "MERCHANT_CONFIRMATION",  # problem at the merchant confirmation step
    "SETTLEMENT",            # problem at settlement
    "NOT_SURE",
]
ReportStatus = Literal["OPEN", "UNDER_REVIEW", "RESOLVED", "REJECTED"]


class CustomerReportRequest(BaseModel):
    problem_type: ProblemType
    stage: ProblemStage
    description: str = Field(default="", max_length=2000)


class CustomerReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    report_id: str
    transaction_id: str
    customer_id: str
    problem_type: str
    stage: str
    description: str
    status: str
    created_at: datetime
    updated_at: datetime


class CustomerReportFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    report: CustomerReportResponse
    # true when this customer already filed a report on this transaction —
    # the stored report is replayed unchanged (idempotent filing)
    already_reported: bool
    digital_twin_event_recorded: bool

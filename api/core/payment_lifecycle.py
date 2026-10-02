"""
api/core/payment_lifecycle.py — Stage 6 payment-domain event vocabulary.

The transaction state machine (api/core/state_machine.py) records
transaction-LEVEL lifecycle. Stage 6 adds a finer-grained, payment-DOMAIN
event stream (customer bank debit -> gateway -> merchant confirmation ->
settlement) that providers report on independently. This module is the
SHARED vocabulary: the ingestion service, the reconstruction engine, and the
payment simulator all import these EXACT names so no string literals drift.

Stored payment events are OBSERVED FACTS. There is deliberately no
NOT_OBSERVED outcome constant for stored events: absence of evidence is
derived at reconstruction time (the engine stamps NOT_OBSERVED for stages
with no events), never ingested as fact.
"""

from __future__ import annotations


class PaymentSource:
    """Who emitted the event."""

    BANK = "BANK"
    GATEWAY = "GATEWAY"
    MERCHANT = "MERCHANT"
    SETTLEMENT = "SETTLEMENT"
    SYSTEM = "SYSTEM"


SOURCES = (PaymentSource.BANK, PaymentSource.GATEWAY, PaymentSource.MERCHANT,
           PaymentSource.SETTLEMENT, PaymentSource.SYSTEM)


class PaymentStage:
    """Sequential payment stages, in domain order."""

    BANK_DEBIT = "BANK_DEBIT"
    GATEWAY = "GATEWAY"
    MERCHANT_CONFIRMATION = "MERCHANT_CONFIRMATION"
    SETTLEMENT = "SETTLEMENT"


STAGE_ORDER = (PaymentStage.BANK_DEBIT, PaymentStage.GATEWAY,
               PaymentStage.MERCHANT_CONFIRMATION, PaymentStage.SETTLEMENT)

# Outcomes carried by events. Stored events are observed facts — the set
# deliberately excludes NOT_OBSERVED, which is derived at reconstruction
# time for absent evidence.
OUTCOME_CONFIRMED = "CONFIRMED"
OUTCOME_FAILED = "FAILED"
OUTCOME_TIMEOUT = "TIMEOUT"
OUTCOME_ERROR = "ERROR"
OUTCOME_NOT_CONFIRMED = "NOT_CONFIRMED"
OUTCOME_PROGRESS = "OBSERVED"

OUTCOMES = (OUTCOME_CONFIRMED, OUTCOME_FAILED, OUTCOME_TIMEOUT, OUTCOME_ERROR,
            OUTCOME_NOT_CONFIRMED, OUTCOME_PROGRESS)

# EVENT_TYPE_INFO: the 14 canonical payment-domain event types, each with its
# fixed (source, stage, outcome) triple. The triple is authoritative — it is
# what the reconstruction engine reasons over — so ingestion derives
# source/status from this table rather than trusting the client's claim.
EVENT_TYPE_INFO: dict[str, dict[str, str]] = {
    # Stage 1: customer bank debit
    "CUSTOMER_DEBIT_CONFIRMED": {"source": PaymentSource.BANK, "stage": PaymentStage.BANK_DEBIT, "outcome": OUTCOME_CONFIRMED},
    "CUSTOMER_DEBIT_FAILED": {"source": PaymentSource.BANK, "stage": PaymentStage.BANK_DEBIT, "outcome": OUTCOME_FAILED},
    # Stage 2: gateway
    "GATEWAY_REQUEST_SENT": {"source": PaymentSource.GATEWAY, "stage": PaymentStage.GATEWAY, "outcome": OUTCOME_PROGRESS},
    "GATEWAY_RESPONSE_RECEIVED": {"source": PaymentSource.GATEWAY, "stage": PaymentStage.GATEWAY, "outcome": OUTCOME_CONFIRMED},
    "GATEWAY_TIMEOUT": {"source": PaymentSource.GATEWAY, "stage": PaymentStage.GATEWAY, "outcome": OUTCOME_TIMEOUT},
    "GATEWAY_ERROR": {"source": PaymentSource.GATEWAY, "stage": PaymentStage.GATEWAY, "outcome": OUTCOME_ERROR},
    # Stage 3: merchant confirmation
    "MERCHANT_CONFIRMATION_REQUESTED": {"source": PaymentSource.MERCHANT, "stage": PaymentStage.MERCHANT_CONFIRMATION, "outcome": OUTCOME_PROGRESS},
    "MERCHANT_CONFIRMATION_RECEIVED": {"source": PaymentSource.MERCHANT, "stage": PaymentStage.MERCHANT_CONFIRMATION, "outcome": OUTCOME_CONFIRMED},
    "MERCHANT_CONFIRMATION_TIMEOUT": {"source": PaymentSource.MERCHANT, "stage": PaymentStage.MERCHANT_CONFIRMATION, "outcome": OUTCOME_TIMEOUT},
    "MERCHANT_ERROR": {"source": PaymentSource.MERCHANT, "stage": PaymentStage.MERCHANT_CONFIRMATION, "outcome": OUTCOME_ERROR},
    # Stage 4: settlement
    "SETTLEMENT_REQUESTED": {"source": PaymentSource.SETTLEMENT, "stage": PaymentStage.SETTLEMENT, "outcome": OUTCOME_PROGRESS},
    "SETTLEMENT_CONFIRMED": {"source": PaymentSource.SETTLEMENT, "stage": PaymentStage.SETTLEMENT, "outcome": OUTCOME_CONFIRMED},
    "SETTLEMENT_FAILED": {"source": PaymentSource.SETTLEMENT, "stage": PaymentStage.SETTLEMENT, "outcome": OUTCOME_FAILED},
    "SETTLEMENT_NOT_CONFIRMED": {"source": PaymentSource.SETTLEMENT, "stage": PaymentStage.SETTLEMENT, "outcome": OUTCOME_NOT_CONFIRMED},
}

EVENT_TYPES = tuple(EVENT_TYPE_INFO)

# The 7 events of a fully successful flow (happy path). Useful both for the
# simulator and as the reconstruction engine's reference trace.
HAPPY_PATH_EVENTS = (
    "CUSTOMER_DEBIT_CONFIRMED",
    "GATEWAY_REQUEST_SENT",
    "GATEWAY_RESPONSE_RECEIVED",
    "MERCHANT_CONFIRMATION_REQUESTED",
    "MERCHANT_CONFIRMATION_RECEIVED",
    "SETTLEMENT_REQUESTED",
    "SETTLEMENT_CONFIRMED",
)

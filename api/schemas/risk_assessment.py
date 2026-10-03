"""
api/schemas/risk_assessment.py — request/response models for the Stage 7
RULES slice (Risk & Anomaly Classification).

DESIGN PRINCIPLE — evidence-first: every classification is traceable to
observed facts (stored payment events, reconstruction output, transaction
features). The rules engine (api/services/anomaly_rules.py) produces a
COMPLETE assessment with NO ML: ``model_version`` is "rules-only" and
``ml_anomaly_score`` is None. A parallel agent later adds ML as a SUPPORTING
signal only — it can never invent an anomaly the rules did not observe.

``recovery_candidate`` is DECISION EVIDENCE for Stage 8. Stage 7 never
executes anything: it only reports whether recovery is worth considering and,
when it is not, why it is blocked.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from api.schemas.transaction import UtcDatetime

# ---------------------------------------------------------------------------
# Anomaly-type vocabulary.
# ---------------------------------------------------------------------------
ANOMALY_NONE = "NONE"
ANOMALY_GENUINE_FAILURE = "GENUINE_FAILURE"
ANOMALY_DOUBLE_DEDUCTION = "DOUBLE_DEDUCTION"
ANOMALY_DUPLICATE_TRANSACTION = "DUPLICATE_TRANSACTION"
ANOMALY_SUCCESSFUL_BUT_UNCONFIRMED = "SUCCESSFUL_BUT_UNCONFIRMED"
ANOMALY_FALSE_COMPLAINT = "FALSE_COMPLAINT"
ANOMALY_SUSPICIOUS = "SUSPICIOUS"
ANOMALY_INCOMPLETE = "INCOMPLETE"
ANOMALY_UNKNOWN = "UNKNOWN"

# NONE is part of the vocabulary beyond the spec's anomaly list because a
# clean successful payment is NOT anomalous: forcing every payment into an
# anomaly bucket would inflate risk scores and flood Stage 8 with
# non-candidates. An explicit NONE makes "no anomaly found" a first-class,
# auditable classification instead of an absent one.
ANOMALY_TYPES = (
    ANOMALY_NONE,
    ANOMALY_GENUINE_FAILURE,
    ANOMALY_DOUBLE_DEDUCTION,
    ANOMALY_DUPLICATE_TRANSACTION,
    ANOMALY_SUCCESSFUL_BUT_UNCONFIRMED,
    ANOMALY_FALSE_COMPLAINT,
    ANOMALY_SUSPICIOUS,
    ANOMALY_INCOMPLETE,
    ANOMALY_UNKNOWN,
)

# ---------------------------------------------------------------------------
# Risk-level vocabulary.
# ---------------------------------------------------------------------------
RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"
RISK_CRITICAL = "CRITICAL"
# Reserved for assessments that cannot establish any outcome (rule R10 /
# fallback): uncertainty is a level of its own, never mapped onto a guessed
# severity.
RISK_UNKNOWN = "UNKNOWN"

RISK_LEVELS = (
    RISK_LOW,
    RISK_MEDIUM,
    RISK_HIGH,
    RISK_CRITICAL,
    RISK_UNKNOWN,
)

# ---------------------------------------------------------------------------
# Evidence-code vocabulary (spec section 7). Every EvidenceItem.code MUST be
# one of these constants — the traceability test enforces it.
# ---------------------------------------------------------------------------
EVIDENCE_DEBIT_CONFIRMED = "DEBIT_CONFIRMED"
EVIDENCE_DEBIT_FAILED = "DEBIT_FAILED"
EVIDENCE_GATEWAY_TIMEOUT = "GATEWAY_TIMEOUT"
EVIDENCE_GATEWAY_ERROR = "GATEWAY_ERROR"
EVIDENCE_MERCHANT_CONFIRMATION_TIMEOUT = "MERCHANT_CONFIRMATION_TIMEOUT"
EVIDENCE_MERCHANT_ERROR = "MERCHANT_ERROR"
EVIDENCE_SETTLEMENT_CONFIRMED = "SETTLEMENT_CONFIRMED"
EVIDENCE_SETTLEMENT_FAILED = "SETTLEMENT_FAILED"
EVIDENCE_SETTLEMENT_NOT_CONFIRMED = "SETTLEMENT_NOT_CONFIRMED"
EVIDENCE_DUPLICATE_PROVIDER_EVENT = "DUPLICATE_PROVIDER_EVENT"
EVIDENCE_MULTIPLE_DEBIT_CONFIRMATIONS = "MULTIPLE_DEBIT_CONFIRMATIONS"
EVIDENCE_DUPLICATE_TRANSACTION_PATTERN = "DUPLICATE_TRANSACTION_PATTERN"
EVIDENCE_SUCCESS_WITHOUT_MERCHANT_CONFIRMATION = "SUCCESS_WITHOUT_MERCHANT_CONFIRMATION"
EVIDENCE_HIGH_RETRY_COUNT = "HIGH_RETRY_COUNT"
EVIDENCE_HIGH_LATENCY = "HIGH_LATENCY"
EVIDENCE_REPEATED_TRANSACTION_ATTEMPTS = "REPEATED_TRANSACTION_ATTEMPTS"
EVIDENCE_INCOMPLETE_EVENT_CHAIN = "INCOMPLETE_EVENT_CHAIN"
# Reserved for the hybrid (rules + ML) agent — never emitted by rules-only.
EVIDENCE_ML_HIGH_ANOMALY = "ML_HIGH_ANOMALY"

EVIDENCE_CODES = (
    EVIDENCE_DEBIT_CONFIRMED,
    EVIDENCE_DEBIT_FAILED,
    EVIDENCE_GATEWAY_TIMEOUT,
    EVIDENCE_GATEWAY_ERROR,
    EVIDENCE_MERCHANT_CONFIRMATION_TIMEOUT,
    EVIDENCE_MERCHANT_ERROR,
    EVIDENCE_SETTLEMENT_CONFIRMED,
    EVIDENCE_SETTLEMENT_FAILED,
    EVIDENCE_SETTLEMENT_NOT_CONFIRMED,
    EVIDENCE_DUPLICATE_PROVIDER_EVENT,
    EVIDENCE_MULTIPLE_DEBIT_CONFIRMATIONS,
    EVIDENCE_DUPLICATE_TRANSACTION_PATTERN,
    EVIDENCE_SUCCESS_WITHOUT_MERCHANT_CONFIRMATION,
    EVIDENCE_HIGH_RETRY_COUNT,
    EVIDENCE_HIGH_LATENCY,
    EVIDENCE_REPEATED_TRANSACTION_ATTEMPTS,
    EVIDENCE_INCOMPLETE_EVENT_CHAIN,
    EVIDENCE_ML_HIGH_ANOMALY,
)

# Where an EvidenceItem was observed.
SOURCE_PAYMENT_EVENT = "PAYMENT_EVENT"
SOURCE_TRANSACTION_FEATURE = "TRANSACTION_FEATURE"
SOURCE_HISTORY = "HISTORY"
SOURCE_RECONSTRUCTION = "RECONSTRUCTION"
SOURCE_RULE = "RULE"

EVIDENCE_SOURCES = (
    SOURCE_PAYMENT_EVENT,
    SOURCE_TRANSACTION_FEATURE,
    SOURCE_HISTORY,
    SOURCE_RECONSTRUCTION,
    SOURCE_RULE,
)

# Severity weights used by the documented deterministic risk-score formula in
# api/services/anomaly_rules.py.
SEVERITY_LOW = "LOW"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_HIGH = "HIGH"
SEVERITY_CRITICAL = "CRITICAL"


class EvidenceItem(BaseModel):
    """One observed fact that the assessment rests on.

    ``code`` is from the EVIDENCE_CODES vocabulary; ``description`` is
    human-readable English traceable to the observation; ``source`` says
    where the fact came from. ``source`` is optional: dataset-loaded
    assessments carry evidence without provenance (the CSV has no source
    column), while live-pipeline evidence always sets it.
    """

    code: str
    description: str
    source: str | None = None  # EVIDENCE_SOURCES value; None for dataset rows
    severity: str  # LOW | MEDIUM | HIGH | CRITICAL


class TriggeredRule(BaseModel):
    """The deterministic rule (R1..R10) that produced (or contributed to) the
    classification."""

    rule_id: str  # "R1".."R10"
    name: str


class RiskAssessment(BaseModel):
    """Complete Stage-7 assessment of one transaction.

    In rules-only mode ``risk_score`` EQUALS ``deterministic_risk_score``;
    the hybrid agent later combines ML into ``risk_score`` while keeping the
    deterministic component intact for auditability.
    """

    transaction_id: str
    assessment_id: str  # uuid4 string
    anomaly_type: str  # ANOMALY_TYPES value
    risk_level: str  # RISK_LEVELS value
    # combined 0..1 score, 2dp (rules-only: equals deterministic_risk_score)
    risk_score: float
    ml_anomaly_score: float | None = None  # None in rules-only mode
    deterministic_risk_score: float
    # DECISION EVIDENCE for Stage 8 — Stage 7 never executes recovery
    recovery_candidate: bool
    recovery_block_reason: str | None = None
    evidence: list[EvidenceItem]
    triggered_rules: list[TriggeredRule]
    reconstruction_root_cause: str | None = None
    reconstruction_confidence: float | None = None
    customer_reported_failure: bool = False
    model_version: str = "rules-only"
    rule_version: str = "1"
    created_at: UtcDatetime

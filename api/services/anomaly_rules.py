"""
api/services/anomaly_rules.py — Stage 7 RULES slice: the versioned
deterministic rule engine (rule_version "1").

PURE logic: given a Transaction-like object, its stored PaymentEvent objects
and the Stage-6 ReconstructionResult, produce ONE complete RiskAssessment.
NO DB writes, NO API routes, NO ML — in this slice the assessment is complete
WITHOUT any model: ``model_version`` is "rules-only" and
``ml_anomaly_score`` is None (a parallel hybrid agent adds ML later as a
SUPPORTING signal only).

Evidence-first:
  * Every observed fact used by a rule is recorded as an EvidenceItem with a
    code from the EVIDENCE_CODES vocabulary, a human-readable description and
    the fact's source.
  * Absence of evidence produces uncertainty (INCOMPLETE / UNKNOWN), never an
    invented fact (spec Case F: a lone debit event is INCOMPLETE, not
    GENUINE_FAILURE).
  * ``recovery_candidate`` is DECISION EVIDENCE for Stage 8 — this module
    never executes recovery.

Deterministic risk-score formula (documented):

    deterministic_risk_score = round(
        min(1.0, 0.85 * n_high + 0.5 * n_medium + 0.2 * n_low), 2)

where n_high / n_medium / n_low count EvidenceItems by severity. CRITICAL
items count with the HIGH weight (rules-only mode emits none; the hybrid
agent may add them via ML evidence).

Risk level <-> score consistency: the risk LEVEL is set by the firing rule
(financial-integrity outcomes such as DOUBLE_DEDUCTION are HIGH regardless
of how few evidence rows back them, while a clean success is LOW even though
its confirmation evidence still contributes to the score). The SCORE is the
evidence-weighted number above. The pairing is intentional: the level is the
rule's verdict, the score is the weight of the evidence behind it. Callers
should treat the level as authoritative for routing and the score as the
audit trail. In rules-only mode risk_score == deterministic_risk_score.

Rule precedence — FIRST MATCH WINS. Financial-integrity rules (R1, R2)
outrank outcome rules because money that moved twice can never be treated as
an ordinary failure; success-chain rules (R7, R8) outrank pattern rules
because a fully evidenced completion settles the outcome question; the
incompleteness rule (R10) is last before the fallback so any rule with real
evidence claims the classification first.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Callable, Sequence

from api.core.payment_lifecycle import (
    OUTCOME_CONFIRMED,
    OUTCOME_ERROR,
    OUTCOME_FAILED,
    OUTCOME_NOT_CONFIRMED,
    OUTCOME_TIMEOUT,
)
from api.schemas.reconstruction import (
    ROOT_CAUSE_INCOMPLETE,
    ReconstructionResult,
    STAGE_NOT_OBSERVED,
)
from api.schemas.risk_assessment import (
    ANOMALY_DOUBLE_DEDUCTION,
    ANOMALY_DUPLICATE_TRANSACTION,
    ANOMALY_FALSE_COMPLAINT,
    ANOMALY_GENUINE_FAILURE,
    ANOMALY_INCOMPLETE,
    ANOMALY_NONE,
    ANOMALY_SUCCESSFUL_BUT_UNCONFIRMED,
    ANOMALY_SUSPICIOUS,
    ANOMALY_UNKNOWN,
    EVIDENCE_DEBIT_CONFIRMED,
    EVIDENCE_DEBIT_FAILED,
    EVIDENCE_DUPLICATE_PROVIDER_EVENT,
    EVIDENCE_DUPLICATE_TRANSACTION_PATTERN,
    EVIDENCE_GATEWAY_ERROR,
    EVIDENCE_GATEWAY_TIMEOUT,
    EVIDENCE_HIGH_LATENCY,
    EVIDENCE_HIGH_RETRY_COUNT,
    EVIDENCE_INCOMPLETE_EVENT_CHAIN,
    EVIDENCE_MERCHANT_CONFIRMATION_TIMEOUT,
    EVIDENCE_MERCHANT_ERROR,
    EVIDENCE_MULTIPLE_DEBIT_CONFIRMATIONS,
    EVIDENCE_REPEATED_TRANSACTION_ATTEMPTS,
    EVIDENCE_SETTLEMENT_CONFIRMED,
    EVIDENCE_SETTLEMENT_FAILED,
    EVIDENCE_SETTLEMENT_NOT_CONFIRMED,
    EVIDENCE_SUCCESS_WITHOUT_MERCHANT_CONFIRMATION,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    RISK_UNKNOWN,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    EvidenceItem,
    RiskAssessment,
    SOURCE_PAYMENT_EVENT,
    SOURCE_RECONSTRUCTION,
    SOURCE_RULE,
    SOURCE_TRANSACTION_FEATURE,
    TriggeredRule,
)

RULE_VERSION = "1"

# ---------------------------------------------------------------------------
# Named thresholds (module constants — never inline magic numbers).
# ---------------------------------------------------------------------------
HIGH_RETRY_THRESHOLD = 3
HIGH_LATENCY_MS = 5000
REPEATED_ATTEMPT_THRESHOLD = 3  # previous_failures

# Severity weights for the deterministic risk-score formula.
_WEIGHT_HIGH = 0.85
_WEIGHT_MEDIUM = 0.5
_WEIGHT_LOW = 0.2


def _ev(
    code: str, description: str, source: str, severity: str
) -> EvidenceItem:
    return EvidenceItem(
        code=code, description=description, source=source, severity=severity
    )


def _rule(rule_id: str, name: str) -> TriggeredRule:
    return TriggeredRule(rule_id=rule_id, name=name)


def _risk_score(evidence: Sequence[EvidenceItem]) -> float:
    """Documented deterministic formula — see module docstring."""
    n_high = sum(
        1 for e in evidence if e.severity in (SEVERITY_HIGH, SEVERITY_CRITICAL)
    )
    n_medium = sum(1 for e in evidence if e.severity == SEVERITY_MEDIUM)
    n_low = sum(1 for e in evidence if e.severity == SEVERITY_LOW)
    return round(min(1.0, _WEIGHT_HIGH * n_high + _WEIGHT_MEDIUM * n_medium
                     + _WEIGHT_LOW * n_low), 2)


def assess_rules(
    tx: Any,
    events: Sequence[Any],
    reconstruction: ReconstructionResult,
    *,
    customer_reported_failure: bool,
    now: datetime,
    reference_lookup: Callable[[str], list[str]] | None = None,
) -> RiskAssessment:
    """Produce the complete rules-only RiskAssessment for one transaction.

    ``tx``: object with Transaction attributes (amount, retry_count,
        previous_failures, gateway_latency_ms, transaction_id).
    ``events``: objects with PaymentEvent attributes (the raw observed
        facts — used for debit-confirmation counting and reference ids).
    ``reconstruction``: the Stage-6 ReconstructionResult (per-stage
        statuses, root_cause, confidence). Stage outcomes are taken from
        HERE, never re-derived, so rules and reconstruction can never
        disagree.
    ``customer_reported_failure``: explicit complaint flag from the route
        layer — R8 (FALSE_COMPLAINT) requires BOTH the full success chain
        AND this flag; it is never inferred from success alone (spec
        section 9: avoid accusatory automation).
    ``reference_lookup``: OPTIONAL injected callable(reference_id) ->
        list[str] of OTHER transaction_ids sharing that provider reference.
        The route layer wires a DB query; tests inject a fake. When None,
        R2 cannot fire (amount similarity alone NEVER triggers it — only a
        shared provider reference is evidence of a true duplicate).

    Exactly ONE classification is returned (first match over R1..R10, then
    the fallback). All evidence collected along the way is attached, so the
    audit trail is complete even when a rule did not fire.
    """
    tx_id = str(getattr(tx, "transaction_id"))

    # ---- evidence-first: collect observed facts ---------------------------
    evidence: list[EvidenceItem] = []

    d = reconstruction.customer_debit_status
    g = reconstruction.gateway_status
    m = reconstruction.merchant_confirmation_status
    s = reconstruction.settlement_status

    if d == OUTCOME_CONFIRMED:
        evidence.append(_ev(
            EVIDENCE_DEBIT_CONFIRMED,
            "Customer bank debit was confirmed.",
            SOURCE_RECONSTRUCTION,
            SEVERITY_LOW,
        ))
    elif d == OUTCOME_FAILED:
        evidence.append(_ev(
            EVIDENCE_DEBIT_FAILED,
            "Customer bank debit failed; no money left the customer's account.",
            SOURCE_RECONSTRUCTION,
            SEVERITY_HIGH,
        ))

    if g == OUTCOME_TIMEOUT:
        evidence.append(_ev(
            EVIDENCE_GATEWAY_TIMEOUT,
            "Payment gateway timed out.",
            SOURCE_RECONSTRUCTION,
            SEVERITY_MEDIUM,
        ))
    elif g == OUTCOME_ERROR:
        evidence.append(_ev(
            EVIDENCE_GATEWAY_ERROR,
            "Payment gateway returned an error.",
            SOURCE_RECONSTRUCTION,
            SEVERITY_MEDIUM,
        ))

    if m == OUTCOME_TIMEOUT:
        evidence.append(_ev(
            EVIDENCE_MERCHANT_CONFIRMATION_TIMEOUT,
            "Merchant confirmation timed out.",
            SOURCE_RECONSTRUCTION,
            SEVERITY_MEDIUM,
        ))
    elif m == OUTCOME_ERROR:
        evidence.append(_ev(
            EVIDENCE_MERCHANT_ERROR,
            "Merchant returned an error during confirmation.",
            SOURCE_RECONSTRUCTION,
            SEVERITY_MEDIUM,
        ))

    if s == OUTCOME_CONFIRMED:
        evidence.append(_ev(
            EVIDENCE_SETTLEMENT_CONFIRMED,
            "Settlement was confirmed; funds completed the transfer.",
            SOURCE_RECONSTRUCTION,
            SEVERITY_LOW,
        ))
    elif s == OUTCOME_FAILED:
        evidence.append(_ev(
            EVIDENCE_SETTLEMENT_FAILED,
            "Settlement failed.",
            SOURCE_RECONSTRUCTION,
            SEVERITY_MEDIUM,
        ))
    elif s == OUTCOME_NOT_CONFIRMED:
        evidence.append(_ev(
            EVIDENCE_SETTLEMENT_NOT_CONFIRMED,
            "Settlement was reported but not confirmed.",
            SOURCE_RECONSTRUCTION,
            SEVERITY_MEDIUM,
        ))

    retry_count = int(getattr(tx, "retry_count", 0) or 0)
    previous_failures = int(getattr(tx, "previous_failures", 0) or 0)
    latency_ms = int(getattr(tx, "gateway_latency_ms", 0) or 0)

    high_retry = retry_count >= HIGH_RETRY_THRESHOLD
    repeated = previous_failures >= REPEATED_ATTEMPT_THRESHOLD

    if high_retry:
        evidence.append(_ev(
            EVIDENCE_HIGH_RETRY_COUNT,
            f"Retry count {retry_count} reached the high-retry threshold "
            f"({HIGH_RETRY_THRESHOLD}).",
            SOURCE_TRANSACTION_FEATURE,
            SEVERITY_LOW,  # informational unless a pattern rule fires
        ))
    if repeated:
        evidence.append(_ev(
            EVIDENCE_REPEATED_TRANSACTION_ATTEMPTS,
            f"{previous_failures} prior failed attempts for this customer "
            f"pattern (threshold {REPEATED_ATTEMPT_THRESHOLD}).",
            SOURCE_TRANSACTION_FEATURE,
            SEVERITY_LOW,
        ))
    if latency_ms > HIGH_LATENCY_MS:
        evidence.append(_ev(
            EVIDENCE_HIGH_LATENCY,
            f"Gateway latency {latency_ms} ms exceeded {HIGH_LATENCY_MS} ms.",
            SOURCE_TRANSACTION_FEATURE,
            SEVERITY_LOW,  # informational only
        ))

    # provider redelivery WITHIN this transaction (same provider_event_id
    # seen twice) is an observed fact — record it even though it can never
    # fire R1/R2 (a redelivery carries the same outcome, and R2 only looks at
    # reference_ids shared with OTHER transactions).
    provider_id_counts: dict[str, int] = {}
    for e in events:
        pid = getattr(e, "provider_event_id", None)
        if pid is not None:
            provider_id_counts[str(pid)] = (
                provider_id_counts.get(str(pid), 0) + 1
            )
    n_redelivered = sum(1 for c in provider_id_counts.values() if c > 1)
    if n_redelivered:
        evidence.append(_ev(
            EVIDENCE_DUPLICATE_PROVIDER_EVENT,
            f"{n_redelivered} provider event(s) were redelivered within this "
            "transaction (idempotent replay; no duplicate charge).",
            SOURCE_PAYMENT_EVENT,
            SEVERITY_LOW,
        ))

    # duplicate debit confirmations: distinct provider_event_id redeliveries
    # of the SAME confirmation count ONCE; two different provider events
    # confirming the customer debit count TWICE (real double deduction).
    debit_confirmed_events = [
        e
        for e in events
        if getattr(e, "event_type", None) == "CUSTOMER_DEBIT_CONFIRMED"
        and getattr(e, "status", None) == OUTCOME_CONFIRMED
    ]
    distinct_debit_provider_ids = {
        str(getattr(e, "provider_event_id")) for e in debit_confirmed_events
    }
    n_debit_confirmed = len(distinct_debit_provider_ids)

    # R2 inputs: shared provider reference_ids on OTHER transactions.
    other_tx_on_refs: dict[str, list[str]] = {}
    if reference_lookup is not None:
        for e in events:
            ref = getattr(e, "reference_id", None)
            if not ref:
                continue
            others = [
                str(other)
                for other in reference_lookup(str(ref))
                if str(other) != tx_id
            ]
            if others:
                other_tx_on_refs[str(ref)] = others

    stages_with_evidence = sum(
        1 for status in (d, g, m, s) if status != STAGE_NOT_OBSERVED
    )

    # ---- rules: FIRST MATCH WINS ------------------------------------------
    if n_debit_confirmed >= 2:
        # R1 — financial integrity first: money left the account twice.
        evidence.append(_ev(
            EVIDENCE_MULTIPLE_DEBIT_CONFIRMATIONS,
            f"{n_debit_confirmed} distinct provider events confirmed the "
            "customer debit.",
            SOURCE_PAYMENT_EVENT,
            SEVERITY_HIGH,
        ))
        return _build(
            tx_id, now, customer_reported_failure, reconstruction, evidence,
            _rule("R1", "double deduction"),
            anomaly_type=ANOMALY_DOUBLE_DEDUCTION,
            risk_level=RISK_HIGH,
            recovery_candidate=False,
            recovery_block_reason=(
                "multiple customer debit confirmations require manual "
                "financial review"
            ),
        )

    if other_tx_on_refs:
        # R2 — a provider reference shared with ANOTHER transaction. Amount
        # similarity alone NEVER triggers this: only the provider's own
        # reference is evidence of a true duplicate submission.
        refs_text = ", ".join(sorted(other_tx_on_refs))
        evidence.append(_ev(
            EVIDENCE_DUPLICATE_TRANSACTION_PATTERN,
            f"Provider reference(s) {refs_text} also appear on another "
            "transaction.",
            SOURCE_RULE,
            SEVERITY_HIGH,
        ))
        return _build(
            tx_id, now, customer_reported_failure, reconstruction, evidence,
            _rule("R2", "duplicate transaction"),
            anomaly_type=ANOMALY_DUPLICATE_TRANSACTION,
            risk_level=RISK_HIGH,
            recovery_candidate=False,
            recovery_block_reason=(
                "same provider reference found on another transaction"
            ),
        )

    if (
        d == OUTCOME_CONFIRMED
        and g in (OUTCOME_TIMEOUT, OUTCOME_ERROR)
        and m == STAGE_NOT_OBSERVED
        and s == STAGE_NOT_OBSERVED
    ):
        # R3 — money left the customer, the gateway never answered, nothing
        # downstream was ever seen: a genuine failure worth recovering.
        return _build(
            tx_id, now, customer_reported_failure, reconstruction, evidence,
            _rule("R3", "genuine gateway failure"),
            anomaly_type=ANOMALY_GENUINE_FAILURE,
            risk_level=RISK_LOW,
            recovery_candidate=True,
        )

    if (
        d == OUTCOME_CONFIRMED
        and g == OUTCOME_CONFIRMED
        and m in (OUTCOME_TIMEOUT, OUTCOME_ERROR)
        and s in (STAGE_NOT_OBSERVED, OUTCOME_NOT_CONFIRMED)
    ):
        # R4 — gateway answered, merchant confirmation failed: genuine
        # failure worth recovering.
        return _build(
            tx_id, now, customer_reported_failure, reconstruction, evidence,
            _rule("R4", "genuine merchant failure"),
            anomaly_type=ANOMALY_GENUINE_FAILURE,
            risk_level=RISK_LOW,
            recovery_candidate=True,
        )

    if (
        d == OUTCOME_CONFIRMED
        and s == OUTCOME_CONFIRMED
        and m == STAGE_NOT_OBSERVED
    ):
        # R5 — funds moved but the merchant never confirmed: reconciliation,
        # not recovery.
        evidence.append(_ev(
            EVIDENCE_SUCCESS_WITHOUT_MERCHANT_CONFIRMATION,
            "Settlement confirmed without any merchant confirmation event.",
            SOURCE_RECONSTRUCTION,
            SEVERITY_MEDIUM,
        ))
        return _build(
            tx_id, now, customer_reported_failure, reconstruction, evidence,
            _rule("R5", "successful but unconfirmed"),
            anomaly_type=ANOMALY_SUCCESSFUL_BUT_UNCONFIRMED,
            risk_level=RISK_MEDIUM,
            recovery_candidate=False,
            recovery_block_reason=(
                "settlement confirmed; funds moved — manual reconciliation "
                "required"
            ),
        )

    if (
        d == OUTCOME_CONFIRMED
        and m == OUTCOME_CONFIRMED
        and s in (OUTCOME_FAILED, OUTCOME_NOT_CONFIRMED)
    ):
        # R6 — merchant confirmed, settlement failed downstream: genuine
        # failure worth recovering.
        return _build(
            tx_id, now, customer_reported_failure, reconstruction, evidence,
            _rule("R6", "genuine settlement failure"),
            anomaly_type=ANOMALY_GENUINE_FAILURE,
            risk_level=RISK_LOW,
            recovery_candidate=True,
        )

    all_confirmed = all(
        status == OUTCOME_CONFIRMED for status in (d, g, m, s)
    )

    if all_confirmed and customer_reported_failure:
        # R8 — FALSE_COMPLAINT requires BOTH the full success chain AND the
        # explicit customer-reported-failure flag. It is NEVER inferred from
        # success alone (spec section 9: avoid accusatory automation).
        return _build(
            tx_id, now, customer_reported_failure, reconstruction, evidence,
            _rule("R8", "false complaint"),
            anomaly_type=ANOMALY_FALSE_COMPLAINT,
            risk_level=RISK_HIGH,
            recovery_candidate=False,
            recovery_block_reason=(
                "payment completed successfully — customer-reported failure "
                "contradicted by full evidence chain"
            ),
        )

    if all_confirmed:
        # R7 — clean success: nothing anomalous, nothing to recover.
        return _build(
            tx_id, now, customer_reported_failure, reconstruction, evidence,
            _rule("R7", "clean success"),
            anomaly_type=ANOMALY_NONE,
            risk_level=RISK_LOW,
            recovery_candidate=False,
            recovery_block_reason="no failure to recover",
        )

    if reconstruction.root_cause == ROOT_CAUSE_INCOMPLETE and (
        high_retry or repeated
    ):
        # R9 — no determinable payment outcome (root cause INCOMPLETE) AND an
        # unusual retry/attempt pattern: suspicious, but not evidence of a
        # specific failure. Note a confirmed debit alone does NOT establish
        # the payment's outcome, so it must not suppress this rule.
        if high_retry:
            evidence.append(_ev(
                EVIDENCE_HIGH_RETRY_COUNT,
                f"Retry count {retry_count} reached the high-retry threshold "
                f"({HIGH_RETRY_THRESHOLD}) without a determinable outcome.",
                SOURCE_TRANSACTION_FEATURE,
                SEVERITY_HIGH,
            ))
        if repeated:
            evidence.append(_ev(
                EVIDENCE_REPEATED_TRANSACTION_ATTEMPTS,
                f"{previous_failures} prior failed attempts (threshold "
                f"{REPEATED_ATTEMPT_THRESHOLD}) without a determinable "
                "outcome.",
                SOURCE_TRANSACTION_FEATURE,
                SEVERITY_HIGH,
            ))
        return _build(
            tx_id, now, customer_reported_failure, reconstruction, evidence,
            _rule("R9", "suspicious pattern"),
            anomaly_type=ANOMALY_SUSPICIOUS,
            risk_level=RISK_HIGH,
            recovery_candidate=False,
            recovery_block_reason=(
                "unusual retry/attempt pattern without a determinable "
                "payment outcome"
            ),
        )

    if stages_with_evidence < 2 or (
        reconstruction.root_cause == ROOT_CAUSE_INCOMPLETE
    ):
        # R10 — insufficient evidence: uncertainty is the honest verdict,
        # never a guessed failure (spec Case F). INCOMPLETE root cause means
        # the reconstruction could not establish a terminal payment outcome;
        # R9 has already had its chance to claim the retry-pattern subset.
        evidence.append(_ev(
            EVIDENCE_INCOMPLETE_EVENT_CHAIN,
            f"Only {stages_with_evidence} of 4 payment stages have any "
            "evidence; the final outcome is unknown.",
            SOURCE_RECONSTRUCTION,
            SEVERITY_MEDIUM,
        ))
        return _build(
            tx_id, now, customer_reported_failure, reconstruction, evidence,
            _rule("R10", "incomplete evidence"),
            anomaly_type=ANOMALY_INCOMPLETE,
            risk_level=RISK_UNKNOWN,
            recovery_candidate=False,
            recovery_block_reason="insufficient payment evidence",
        )

    # DEFAULT fallback — should rarely fire; never guess.
    return _build(
        tx_id, now, customer_reported_failure, reconstruction, evidence,
        _rule("R0", "fallback unknown"),
        anomaly_type=ANOMALY_UNKNOWN,
        risk_level=RISK_UNKNOWN,
        recovery_candidate=False,
        recovery_block_reason="payment outcome could not be classified",
    )


def _build(
    tx_id: str,
    now: datetime,
    customer_reported_failure: bool,
    reconstruction: ReconstructionResult,
    evidence: list[EvidenceItem],
    rule: TriggeredRule,
    *,
    anomaly_type: str,
    risk_level: str,
    recovery_candidate: bool,
    recovery_block_reason: str | None = None,
) -> RiskAssessment:
    """Assemble the final assessment. Rules-only: risk_score equals the
    deterministic score and ml_anomaly_score stays None."""
    deterministic = _risk_score(evidence)
    return RiskAssessment(
        transaction_id=tx_id,
        assessment_id=str(uuid.uuid4()),
        anomaly_type=anomaly_type,
        risk_level=risk_level,
        risk_score=deterministic,
        ml_anomaly_score=None,
        deterministic_risk_score=deterministic,
        recovery_candidate=recovery_candidate,
        recovery_block_reason=recovery_block_reason,
        evidence=evidence,
        triggered_rules=[rule],
        reconstruction_root_cause=reconstruction.root_cause,
        reconstruction_confidence=reconstruction.reconstruction_confidence,
        customer_reported_failure=customer_reported_failure,
        model_version="rules-only",
        rule_version=RULE_VERSION,
        created_at=now,
    )

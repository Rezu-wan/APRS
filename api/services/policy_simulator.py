"""
api/services/policy_simulator.py — Stage 11 Phase 11E: Recovery Policy
Simulator (research tool, SANDBOX ONLY).

WHAT IT DOES: re-evaluates a SET of versioned recovery-decision policies
against a corpus of transactions on the SAME evidence, and reports flat,
measurable per-policy differences — would-release / would-block /
would-manual-review counts, how often the REAL Stage-8 safety gate would
veto each policy's releases, and honest ground-truth scoring (false
recovery / missed recovery) when the corpus has known outcomes (the demo
corpus).

THE CORE CONSTRAINT — SIMULATION PURITY:

  A simulation run NEVER touches the live policy, the executor, the
  payment provider, the sandbox ledger, transaction states, or the
  Digital Twin. The ONLY write in the entire phase is ONE best-effort
  POLICY_SIMULATION security-audit row, written by the ROUTE (not this
  module) after the run. Specifically:

    * the ACTIVE policy (autonomous-v1) is WRAPPED, never modified —
      api/services/recovery_decision_policy.decide is called unchanged;
    * autonomous_recovery.process_transaction is NEVER called — this
      module mirrors its evaluation ORDER (assessment -> reconstruction
      -> decide -> safety gate) without any of its writes;
    * risk_engine.run_assessment is used because it is pure: it reads
      events, computes, and reuses stored rows on fingerprint match but
      NEVER inserts (persist_assessment / record_anomaly_classified are
      the caller's job and are deliberately not called here);
    * event_reconstruction.reconstruct_from_events is pure;
    * recovery_safety.check_safety is pure (caller supplies all data).

THE NO-RANKING RULE (spec section 13): the run reports flat per-policy
metrics and a comparison of numeric metrics only. There is NO ranking, no
"best policy" field, no recommendation — policy selection is a human
decision; the simulator only makes the differences measurable.

POLICY REGISTRY (versioned; the ACTIVE policy is never replaced):
  autonomous-v1               the ACTIVE production policy (wraps the real
                              module — never reimplemented here).
  manual-only-baseline        EXPERIMENTAL research baseline: never acts
                              autonomously; every genuine failure /
                              recovery candidate goes to MANUAL_REVIEW,
                              everything else NO_ACTION.
  autonomous-v2-experimental  EXPERIMENTAL research DELTA from v1 (the
                              only behavioral difference): v1 releases on
                              LOW *or* MEDIUM risk; v2 keeps LOW
                              unconditional but releases MEDIUM risk only
                              when the reconstruction confidence is >= 0.6
                              (else MANUAL_REVIEW). Rationale: MEDIUM-risk
                              releases are exactly where evidence quality
                              should gate autonomy. Chosen because it is a
                              real, small, measurable delta — the demo
                              merchant-timeout chain carries confidence
                              0.71 (would still release) while a
                              gateway-timeout chain carries 0.43 (would
                              not), so corpora can exercise both sides.

All non-v1 entries are implemented PURELY in this module and are labeled
experimental in their descriptions.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from api.schemas.recovery_autonomous import (
    ACTION_MANUAL_REVIEW,
    ACTION_NO_ACTION,
    ACTION_RELEASE_LIMIT,
    BLOCK_NOT_ELIGIBLE,
    BLOCK_RISK_NO_LONGER_PERMITS,
)
from api.schemas.risk_assessment import (
    ANOMALY_GENUINE_FAILURE,
    RISK_LOW,
    RISK_MEDIUM,
)
from api.services.event_reconstruction import reconstruct_from_events
from api.services.payment_event_service import get_payment_events
from api.services.recovery_decision_policy import (
    POLICY_VERSION as ACTIVE_POLICY_VERSION,
    decide as active_decide,
)
from api.services.recovery_safety import check_safety
from api.services.risk_engine import run_assessment

logger = logging.getLogger("payment_recovery.policy_simulator")

SIMULATOR_NAME = "policy-simulator"

# v2-experimental delta: minimum reconstruction confidence for a MEDIUM-risk
# release (v1 has no confidence requirement on top of the evidence rules).
V2_MEDIUM_RELEASE_MIN_CONFIDENCE = 0.6

_DEBIT_CONFIRMED_EVENT = "CUSTOMER_DEBIT_CONFIRMED"

# "would_block" category: non-eligible NO_ACTION outcomes whose blocked
# reason is a TERMINAL fact about the payment (the payment itself forbids
# any action) — as opposed to uncertainty/non-eligibility, which counts as
# would_no_action. Manual review is always its own category.
_HARD_BLOCK_REASONS = frozenset({
    "ALREADY_SUCCESS",
    "ALREADY_RECOVERED",
    "NEW_SUCCESSFUL_SETTLEMENT",
    "DOUBLE_DEDUCTION",
})


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class SimulatedDecision(BaseModel):
    """One policy's decision for one transaction (simulation only)."""

    action: str
    blocked_reason: str | None = None
    decision_reason: str
    eligible: bool


class SimulatedTransactionDecision(BaseModel):
    """Per-transaction detail for one policy (auditability of the run)."""

    transaction_id: str
    action: str
    blocked_reason: str | None
    eligible: bool
    gate_allowed: bool
    gate_blocked_reason: str | None = None
    gate_vetoed: bool = False


class PolicySimulationResult(BaseModel):
    policy_version: str
    description: str
    experimental: bool
    transactions_evaluated: int
    would_release: int
    would_block: int
    would_manual_review: int
    would_no_action: int
    gate_vetoes: int
    false_recovery: int | None = None
    missed_recovery: int | None = None
    provider_calls_avoided: int
    decision_latency_ms_avg: float
    decisions: list[SimulatedTransactionDecision]


class ComparisonEntry(BaseModel):
    metric: str
    per_policy: dict[str, float | int]


class SimulationDataset(BaseModel):
    source: str  # "demo" | "transaction_ids"
    transaction_ids: list[str]
    dataset_fingerprint: str


class SimulationRun(BaseModel):
    run_id: str
    simulated: bool = True
    affects_live_policy: bool = False
    ground_truth_available: bool
    dataset: SimulationDataset
    policies: list[PolicySimulationResult]
    comparison: list[ComparisonEntry]
    generated_at: datetime
    code_versions: dict


# ---------------------------------------------------------------------------
# Policy registry
# ---------------------------------------------------------------------------


class PolicySpec(BaseModel):
    version: str
    description: str
    experimental: bool
    evaluate: Callable[..., SimulatedDecision]

    model_config = ConfigDict(arbitrary_types_allowed=True)


def _evaluate_autonomous_v1(
    db: Session,
    tx: Any,
    assessment: Any,
    reconstruction: Any,
    *,
    now: datetime,
) -> SimulatedDecision:
    """Wrap the REAL active policy unchanged — never reimplemented here."""
    decision = active_decide(tx, assessment, reconstruction, now=now)
    return SimulatedDecision(
        action=decision.action,
        blocked_reason=decision.blocked_reason,
        decision_reason=decision.decision_reason,
        eligible=decision.eligible,
    )


def _evaluate_manual_only_baseline(
    db: Session,
    tx: Any,
    assessment: Any,
    reconstruction: Any,
    *,
    now: datetime,
) -> SimulatedDecision:
    """Research baseline: NEVER releases. Every genuine failure / recovery
    candidate goes to MANUAL_REVIEW; everything else NO_ACTION."""
    candidate = bool(getattr(assessment, "recovery_candidate", False))
    anomaly = str(getattr(assessment, "anomaly_type", ""))
    if candidate and anomaly == ANOMALY_GENUINE_FAILURE:
        return SimulatedDecision(
            action=ACTION_MANUAL_REVIEW,
            blocked_reason=BLOCK_NOT_ELIGIBLE,
            decision_reason=(
                "manual-only baseline: genuine failure confirmed but this "
                "policy never acts autonomously"
            ),
            eligible=False,
        )
    return SimulatedDecision(
        action=ACTION_NO_ACTION,
        blocked_reason=BLOCK_NOT_ELIGIBLE,
        decision_reason=(
            "manual-only baseline: no autonomous action for this outcome"
        ),
        eligible=False,
    )


def _evaluate_autonomous_v2_experimental(
    db: Session,
    tx: Any,
    assessment: Any,
    reconstruction: Any,
    *,
    now: datetime,
) -> SimulatedDecision:
    """Experimental DELTA from v1 (see module docstring): v1 releases on LOW
    or MEDIUM risk; v2 requires reconstruction confidence >= 0.6 for MEDIUM
    risk, else MANUAL_REVIEW. Every other rule behaves exactly like v1."""

    def _build(
        *,
        eligible: bool,
        action: str,
        reason: str,
        blocked_reason: str | None = None,
    ) -> SimulatedDecision:
        return SimulatedDecision(
            action=action,
            blocked_reason=blocked_reason,
            decision_reason=reason,
            eligible=eligible,
        )

    anomaly = getattr(assessment, "anomaly_type")
    risk = getattr(assessment, "risk_level")
    candidate = bool(getattr(assessment, "recovery_candidate"))

    # The v1 R-A path with the confidence-gated MEDIUM delta.
    if (
        candidate
        and anomaly == ANOMALY_GENUINE_FAILURE
        and reconstruction.customer_debit_status == "CONFIRMED"
        and reconstruction.settlement_status in
        ("NOT_OBSERVED", "NOT_CONFIRMED", "FAILED")
    ):
        if risk == RISK_LOW or (
            risk == RISK_MEDIUM
            and float(reconstruction.reconstruction_confidence)
            >= V2_MEDIUM_RELEASE_MIN_CONFIDENCE
        ):
            return _build(
                eligible=True,
                action=ACTION_RELEASE_LIMIT,
                reason=(
                    "v2-experimental release (v1 evidence path, risk "
                    f"{risk}, confidence "
                    f"{reconstruction.reconstruction_confidence})"
                ),
            )
        if risk == RISK_MEDIUM:
            return _build(
                eligible=False,
                action=ACTION_MANUAL_REVIEW,
                reason=(
                    "v2-experimental: MEDIUM risk with reconstruction "
                    f"confidence {reconstruction.reconstruction_confidence} "
                    f"below the {V2_MEDIUM_RELEASE_MIN_CONFIDENCE} release "
                    "threshold — manual review"
                ),
                blocked_reason=BLOCK_RISK_NO_LONGER_PERMITS,
            )
        # HIGH/CRITICAL — same as v1 R-B.
        return _build(
            eligible=False,
            action=ACTION_MANUAL_REVIEW,
            reason=(
                f"genuine failure at {risk} risk — policy allows only "
                "LOW/MEDIUM risk to move autonomously"
            ),
            blocked_reason=BLOCK_RISK_NO_LONGER_PERMITS,
        )

    # Everything else: delegate to the real v1 table (pure) so the ONLY
    # behavioral difference is the documented delta above.
    decision = active_decide(tx, assessment, reconstruction, now=now)
    return SimulatedDecision(
        action=decision.action,
        blocked_reason=decision.blocked_reason,
        decision_reason=f"v2-experimental (v1 rule): {decision.decision_reason}",
        eligible=decision.eligible,
    )


POLICY_REGISTRY: dict[str, PolicySpec] = {
    ACTIVE_POLICY_VERSION: PolicySpec(
        version=ACTIVE_POLICY_VERSION,
        description=(
            "The ACTIVE production recovery decision policy "
            "(Stage 8), evaluated unchanged."
        ),
        experimental=False,
        evaluate=_evaluate_autonomous_v1,
    ),
    "manual-only-baseline": PolicySpec(
        version="manual-only-baseline",
        description=(
            "EXPERIMENTAL research baseline: never acts autonomously; "
            "genuine failures go to MANUAL_REVIEW, everything else "
            "NO_ACTION."
        ),
        experimental=True,
        evaluate=_evaluate_manual_only_baseline,
    ),
    "autonomous-v2-experimental": PolicySpec(
        version="autonomous-v2-experimental",
        description=(
            "EXPERIMENTAL delta from the active policy: releases LOW-risk "
            "genuine failures as v1 does, but releases MEDIUM risk only "
            f"when reconstruction confidence >= "
            f"{V2_MEDIUM_RELEASE_MIN_CONFIDENCE} (else MANUAL_REVIEW). "
            "All other rules identical to the active policy."
        ),
        experimental=True,
        evaluate=_evaluate_autonomous_v2_experimental,
    ),
}


class UnknownPolicyError(Exception):
    """Unknown policy version requested (route maps to 422)."""

    def __init__(self, unknown: list[str]):
        self.unknown = unknown
        valid = ", ".join(sorted(POLICY_REGISTRY))
        super().__init__(
            f"unknown policy version(s): {', '.join(unknown)}. "
            f"Valid policies: {valid}"
        )


# ---------------------------------------------------------------------------
# Ground truth (honest, corpus-limited)
# ---------------------------------------------------------------------------


def _would_be_false_recovery(
    tx: Any, events: list, reconstruction: Any
) -> bool:
    """A release would be FALSE if the transaction's CURRENT true state is
    SUCCESS, or the evidence shows a double debit (>= 2 customer debit
    confirmations) or a CONFIRMED settlement. Only meaningful on corpora
    with known outcomes (demo)."""
    if str(getattr(tx, "current_state", "")) == "SUCCESS":
        return True
    debits = sum(
        1 for e in events
        if str(getattr(e, "event_type", "")) == _DEBIT_CONFIRMED_EVENT
    )
    if debits >= 2:
        return True
    if getattr(reconstruction, "settlement_status", None) == "CONFIRMED":
        return True
    return False


# ---------------------------------------------------------------------------
# The simulation itself
# ---------------------------------------------------------------------------


def _dataset_fingerprint(events_by_tx: dict[str, list]) -> str:
    """sha256 over sorted (transaction_id, provider_event_id, event_type,
    status, event_timestamp.isoformat()) tuples across the whole corpus."""
    tuples: list[tuple[str, str, str, str, str]] = []
    for tid in sorted(events_by_tx):
        for e in events_by_tx[tid]:
            ts = getattr(e, "event_timestamp", None)
            tuples.append((
                tid,
                str(getattr(e, "provider_event_id", "")),
                str(getattr(e, "event_type", "")),
                str(getattr(e, "status", "")),
                ts.isoformat() if ts is not None else "",
            ))
    tuples.sort()
    payload = "\n".join("|".join(t) for t in tuples)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def simulate_policies(
    db: Session,
    ml_service,
    policy_versions: list[str],
    transaction_ids: list[str],
    *,
    now: datetime,
    ground_truth_available: bool = False,
    source: str = "transaction_ids",
) -> SimulationRun:
    """Simulate the requested policies over the corpus. READ-ONLY: every
    function called here is pure; the caller (route) owns the single
    best-effort audit write.

    ``ground_truth_available``: True only for corpora with KNOWN outcomes
    (the demo corpus). When False, false_recovery / missed_recovery are
    reported as null — no invented numbers for arbitrary id lists."""

    unknown = [p for p in policy_versions if p not in POLICY_REGISTRY]
    if unknown:
        raise UnknownPolicyError(unknown)

    # Load evidence ONCE per transaction, shared across policies (same
    # evidence for every policy — the comparison isolates the POLICY).
    corpus: list[dict] = []
    for tid in transaction_ids:
        from api.services.transaction_service import get_transaction

        tx = get_transaction(db, tid)
        if tx is None:
            continue  # missing ids are skipped, not invented
        events = get_payment_events(db, tid)
        reconstruction = (
            reconstruct_from_events(tid, events, now) if events else None
        )
        assessment, _fingerprint, _reused = run_assessment(
            db, tx, ml_service, customer_reported_failure=False
        )
        corpus.append(
            {
                "tx": tx,
                "events": events,
                "reconstruction": reconstruction,
                "assessment": assessment,
            }
        )

    dataset_fingerprint = _dataset_fingerprint(
        {c["tx"].transaction_id: c["events"] for c in corpus}
    )

    # Per-policy evaluation + the REAL safety gate on the SAME evidence.
    results: list[PolicySimulationResult] = []
    # Internal reference: does the ACTIVE policy + gate release each tx?
    # (used for the missed_recovery ground-truth metric; kept internal —
    # it appears in the output only if autonomous-v1 was requested).
    active_releases: dict[str, bool] = {}

    # Baseline FIRST (always, independent of which policies were requested):
    # does the ACTIVE policy + the REAL gate release each transaction? This
    # is the missed_recovery reference. Pure calls only.
    for item in corpus:
        tx = item["tx"]
        reference = active_decide(
            tx, item["assessment"], item["reconstruction"], now=now
        )
        gate = check_safety(
            tx,
            item["events"],
            item["reconstruction"],
            item["assessment"],
            None,
            now=now,
        )
        active_releases[tx.transaction_id] = bool(
            reference.eligible
            and reference.action == ACTION_RELEASE_LIMIT
            and gate.allowed
        )

    for spec in (POLICY_REGISTRY[v] for v in policy_versions):
        latencies: list[float] = []
        detail: list[SimulatedTransactionDecision] = []
        releases = 0
        blocks = 0
        manuals = 0
        no_actions = 0
        vetoes = 0
        false_recoveries = 0

        for item in corpus:
            tx = item["tx"]
            reconstruction = item["reconstruction"]
            started = time.perf_counter()
            sim = spec.evaluate(
                db,
                tx,
                item["assessment"],
                reconstruction,
                now=now,
            )
            latencies.append((time.perf_counter() - started) * 1000.0)

            would_release = sim.eligible and sim.action == ACTION_RELEASE_LIMIT
            if would_release:
                releases += 1
            elif sim.action == ACTION_MANUAL_REVIEW:
                manuals += 1
            elif (
                sim.action == ACTION_NO_ACTION
                and sim.blocked_reason in _HARD_BLOCK_REASONS
            ):
                blocks += 1
            else:
                no_actions += 1

            # The REAL gate, same evidence, existing_row=None (sandbox:
            # no in-flight recovery rows are considered).
            gate = check_safety(
                tx,
                item["events"],
                reconstruction,
                item["assessment"],
                None,
                now=now,
            )
            vetoed = bool(would_release and not gate.allowed)
            if vetoed:
                vetoes += 1
            if would_release and not vetoed and _would_be_false_recovery(
                tx, item["events"], reconstruction
            ):
                false_recoveries += 1

            detail.append(
                SimulatedTransactionDecision(
                    transaction_id=tx.transaction_id,
                    action=sim.action,
                    blocked_reason=sim.blocked_reason,
                    eligible=sim.eligible,
                    gate_allowed=gate.allowed,
                    gate_blocked_reason=gate.blocked_reason,
                    gate_vetoed=vetoed,
                )
            )

        evaluated = len(corpus)
        results.append(
            PolicySimulationResult(
                policy_version=spec.version,
                description=spec.description,
                experimental=spec.experimental,
                transactions_evaluated=evaluated,
                would_release=releases,
                would_block=blocks,
                would_manual_review=manuals,
                would_no_action=no_actions,
                gate_vetoes=vetoes,
                false_recovery=false_recoveries,
                provider_calls_avoided=blocks + manuals + no_actions,
                decision_latency_ms_avg=(
                    round(sum(latencies) / len(latencies), 3)
                    if latencies
                    else 0.0
                ),
                decisions=detail,
            )
        )

    # Ground-truth metrics: only computed (and only meaningful) for corpora
    # with KNOWN outcomes. missed_recovery = would-NOT-release a tx the
    # ACTIVE policy + gate actually WOULD release (baseline computed in the
    # same run over the same evidence); v1 itself never misses by
    # construction.
    for result in results:
        if not ground_truth_available:
            result.false_recovery = None
            result.missed_recovery = None
            continue
        result.missed_recovery = sum(
            1
            for d in result.decisions
            if active_releases.get(d.transaction_id)
            and not (
                d.eligible and d.action == ACTION_RELEASE_LIMIT
            )
        )
        if result.policy_version == ACTIVE_POLICY_VERSION:
            result.missed_recovery = 0

    return _assemble_run(
        policy_versions=policy_versions,
        corpus=corpus,
        dataset_fingerprint=dataset_fingerprint,
        results=results,
        ground_truth_available=ground_truth_available,
        source=source,
        now=now,
    )


def _assemble_run(
    *,
    policy_versions: list[str],
    corpus: list[dict],
    dataset_fingerprint: str,
    results: list[PolicySimulationResult],
    ground_truth_available: bool,
    source: str,
    now: datetime,
) -> SimulationRun:
    """Build the final SimulationRun — flat metrics, NO ranking."""
    # Comparison: numeric metrics ONLY (latency excluded — timing is noise,
    # not evidence). No ranking, no "best" field, ever.
    metric_names = (
        "transactions_evaluated",
        "would_release",
        "would_block",
        "would_manual_review",
        "would_no_action",
        "gate_vetoes",
        "false_recovery",
        "missed_recovery",
        "provider_calls_avoided",
    )
    comparison: list[ComparisonEntry] = []
    for metric in metric_names:
        per_policy: dict[str, float | int] = {}
        for result in results:
            value = getattr(result, metric)
            if value is None:
                per_policy = {}
                break
            per_policy[result.policy_version] = value
        if per_policy:
            comparison.append(
                ComparisonEntry(metric=metric, per_policy=per_policy)
            )

    rule_versions: set[str] = set()
    model_versions: set[str] = set()
    for item in corpus:
        assessment = item["assessment"]
        rv = getattr(assessment, "rule_version", None)
        mv = getattr(assessment, "model_version", None)
        if rv:
            rule_versions.add(str(rv))
        if mv:
            model_versions.add(str(mv))

    return SimulationRun(
        run_id=uuid.uuid4().hex,
        simulated=True,
        affects_live_policy=False,
        ground_truth_available=ground_truth_available,
        dataset=SimulationDataset(
            source=source,
            transaction_ids=[c["tx"].transaction_id for c in corpus],
            dataset_fingerprint=dataset_fingerprint,
        ),
        policies=results,
        comparison=comparison,
        generated_at=now,
        code_versions={
            "policies": list(policy_versions),
            "rule_version": ", ".join(sorted(rule_versions)) or None,
            "model_version": ", ".join(sorted(model_versions)) or None,
            "executor": "not-invoked",
            "verifier": "not-invoked",
        },
    )

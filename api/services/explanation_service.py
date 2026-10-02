"""
api/services/explanation_service.py — GenAI explanation layer (READ-ONLY path).

ARCHITECTURAL RULE: explanations explain decisions; they never make or change
them. This service is pure reads against authoritative data (transactions,
recovery_decisions, digital_twin_events) and writes ONLY to ai_explanations —
a derived, non-authoritative cache. It never calls the ML engine, never
mutates transaction state, and never appends Digital Twin events.

Flow (get_or_create_explanation):
  1. load the transaction (404 if unknown)
  2. load its latest recovery decision (may be None — a transaction can be
     explained before any decision exists)
  3. load the Digital Twin timeline
  4. fingerprint every field the explanation depends on; if the newest cached
     row matches, return it (cached=True) without touching any provider
  5. otherwise build the schema-controlled ExplanationContext, call the
     provider under a time budget, and fall back to the deterministic
     template on AIProviderError (logging the exception CLASS only — never a
     provider message, which could echo secrets)
  6. cache the result in ai_explanations and return it (cached=False)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.core.config import Settings
from api.core.exceptions import NotFoundError
from api.db.models import AIExplanation, RecoveryDecision, Transaction, utc_now
from api.services.ai.base import AIProvider, AIProviderError
from api.services.ai.fallback import fallback_explanation
from api.services.ai.prompts import PROMPT_VERSION
from api.services.ai.schemas import (
    Audience,
    ExplanationContext,
    ExplanationResponse,
    Language,
    ReconstructionEvidence,
)
from api.services.digital_twin import get_timeline
from api.services.event_reconstruction import reconstruct_from_events
from api.services.payment_event_service import get_payment_events

logger = logging.getLogger("payment_recovery.explanations")


def _pct(p: float) -> str:
    """0.94 -> "94%", 0.7533 -> "75%" (deterministic rounding)."""
    return f"{round(p * 100)}%"


def _risk(p: float) -> str:
    """0.213 -> "0.21"."""
    return f"{p:.2f}"


def _build_reconstruction(db: Session, tx: Transaction) -> ReconstructionEvidence | None:
    """Project the Stage 6 deterministic reconstruction into the explanation
    context. Pure read path: the engine derives everything from stored
    PaymentEvent facts and never writes. A transaction with no payment-domain
    events has no reconstruction evidence (explains exactly as before)."""
    events = get_payment_events(db, tx.transaction_id)
    if len(events) == 0:
        return None
    result = reconstruct_from_events(tx.transaction_id, events, utc_now())
    return ReconstructionEvidence(
        root_cause=result.root_cause,
        failure_stage=result.failure_stage,
        last_successful_stage=result.last_successful_stage,
        customer_debit_status=result.customer_debit_status,
        gateway_status=result.gateway_status,
        merchant_confirmation_status=result.merchant_confirmation_status,
        settlement_status=result.settlement_status,
        missing_events=result.missing_events,
        evidence_summary=result.evidence_summary,
    )


def build_fingerprint(
    tx: Transaction,
    decision: RecoveryDecision | None,
    language: str,
    audience: str,
    reconstruction: ReconstructionEvidence | None = None,
) -> str:
    """sha256 hex of a stable JSON of every field the explanation depends on.

    Any change to the underlying decision, ML assessment, transaction state,
    reconstruction evidence, prompt version, or the requested language/audience
    invalidates the cache and forces a fresh generation."""
    fingerprint_source = {
        "current_state": tx.current_state,
        "failure_reason": tx.failure_reason,
        "failure_prediction": tx.failure_prediction,
        "risk_score": tx.risk_score,
        "safe_to_release_probability": tx.safe_to_release_probability,
        "safe_to_release": tx.safe_to_release,
        "amount": str(tx.amount),
        "currency": tx.currency,
        "decision": decision.decision if decision else None,
        "decision_reason": decision.reason if decision else None,
        "reconstruction": {
            "root_cause": reconstruction.root_cause if reconstruction else None,
            "customer_debit_status": (
                reconstruction.customer_debit_status if reconstruction else None
            ),
            "gateway_status": (
                reconstruction.gateway_status if reconstruction else None
            ),
            "merchant_confirmation_status": (
                reconstruction.merchant_confirmation_status if reconstruction else None
            ),
            "settlement_status": (
                reconstruction.settlement_status if reconstruction else None
            ),
        },
        "language": language,
        "audience": audience,
        "prompt_version": PROMPT_VERSION,
    }
    stable = json.dumps(fingerprint_source, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def build_context(
    tx: Transaction,
    decision: RecoveryDecision | None,
    timeline_events,
    language: str,
    audience: str,
    reconstruction: ReconstructionEvidence | None = None,
) -> ExplanationContext:
    """Map ORM rows onto the controlled provider-facing schema. All numbers
    are pre-formatted here so the model can never recalculate them."""
    return ExplanationContext(
        transaction_id=tx.transaction_id,
        transaction_status=tx.current_state,
        amount=f"{tx.amount} {tx.currency}",
        failure_reason=tx.failure_reason,
        failure_prediction=tx.failure_prediction,
        risk_score=_risk(tx.risk_score) if tx.risk_score is not None else None,
        safe_to_release_probability=(
            _pct(tx.safe_to_release_probability)
            if tx.safe_to_release_probability is not None
            else None
        ),
        safe_to_release=tx.safe_to_release,
        recovery_decision=decision.decision if decision else None,
        recovery_reason=decision.reason if decision else None,
        timeline=[
            f"{e.event_type}: {e.previous_state or '-'} -> {e.new_state}"
            for e in timeline_events
        ],
        reconstruction=reconstruction,
        language=Language(language),
        audience=Audience(audience),
    )


def get_or_create_explanation(
    db: Session,
    provider: AIProvider,
    settings: Settings,
    *,
    transaction_id: str,
    language: str,
    audience: str,
) -> ExplanationResponse:
    """Return a cached explanation if the context is unchanged, else generate
    (with deterministic fallback), cache it, and return it fresh."""
    tx = db.scalars(
        select(Transaction).where(Transaction.transaction_id == transaction_id)
    ).one_or_none()
    if tx is None:
        raise NotFoundError(f"transaction {transaction_id} not found")

    decision = db.scalars(
        select(RecoveryDecision)
        .where(RecoveryDecision.transaction_id == transaction_id)
        .order_by(RecoveryDecision.created_at.desc(), RecoveryDecision.id.desc())
    ).first()

    timeline_events = get_timeline(db, transaction_id)
    reconstruction = _build_reconstruction(db, tx)
    fingerprint = build_fingerprint(tx, decision, language, audience, reconstruction)

    cached_row = db.scalars(
        select(AIExplanation)
        .where(
            AIExplanation.transaction_id == transaction_id,
            AIExplanation.language == language,
            AIExplanation.audience == audience,
            AIExplanation.context_fingerprint == fingerprint,
        )
        .order_by(AIExplanation.created_at.desc(), AIExplanation.id.desc())
    ).first()
    if cached_row is not None:
        logger.info(
            "explanation cache hit: id=%s language=%s audience=%s",
            transaction_id, language, audience,
        )
        return ExplanationResponse(
            transaction_id=transaction_id,
            language=Language(language),
            audience=Audience(audience),
            explanation=cached_row.explanation,
            provider=cached_row.provider,
            model=cached_row.model,
            prompt_version=cached_row.prompt_version,
            is_fallback=cached_row.is_fallback,
            cached=True,
            generated_at=cached_row.created_at,
        )

    context = build_context(
        tx, decision, timeline_events, language, audience, reconstruction
    )

    started = time.perf_counter()
    is_fallback = False
    try:
        text = provider.generate(context)
    except AIProviderError as exc:
        # log the exception CLASS only — provider messages may echo secrets
        logger.warning(
            "AI provider failed (%s); using deterministic fallback", type(exc).__name__
        )
        text = fallback_explanation(context)
        is_fallback = True
    latency_ms = (time.perf_counter() - started) * 1000

    row = AIExplanation(
        transaction_id=transaction_id,
        language=language,
        audience=audience,
        provider=provider.name,
        model=provider.model,
        prompt_version=PROMPT_VERSION,
        explanation=text,
        is_fallback=is_fallback,
        context_fingerprint=fingerprint,
    )
    db.add(row)
    db.commit()

    logger.info(
        "explanation generated: id=%s provider=%s model=%s prompt_version=%s "
        "audience=%s language=%s outcome=%s latency_ms=%.1f",
        transaction_id, provider.name, provider.model, PROMPT_VERSION,
        audience, language, "fallback" if is_fallback else "success", latency_ms,
    )

    return ExplanationResponse(
        transaction_id=transaction_id,
        language=Language(language),
        audience=Audience(audience),
        explanation=text,
        provider=provider.name,
        model=provider.model,
        prompt_version=PROMPT_VERSION,
        is_fallback=is_fallback,
        cached=False,
        generated_at=row.created_at,
    )

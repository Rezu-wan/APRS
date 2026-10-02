"""
api/services/ai/prompts.py — prompt construction for the GenAI explanation layer.

Design decisions:
- AUDIENCE FILTERING happens here, at the data boundary, not in the prompt
  alone: for CUSTOMER audiences the sensitive fields (risk_score,
  safe_to_release_probability, timeline) are removed from the payload
  entirely, so the model can never leak what it was never given.
- The SYSTEM_PROMPT is heavily anti-hallucination: the provided decision is
  AUTHORITATIVE, numbers must be used EXACTLY as pre-formatted, and nothing
  may be invented. The model explains; it never decides.
- PROMPT_VERSION is recorded on every response so explanations stay
  auditable as prompts evolve.
"""

from __future__ import annotations

import json
import logging

from api.services.ai.schemas import Audience, ExplanationContext

logger = logging.getLogger("payment_recovery.ai.prompts")

PROMPT_VERSION = "v4"

SYSTEM_PROMPT = """\
You are an explanation assistant for a payment recovery system. You do not make financial decisions.

Hard constraints:
- The decision provided in the data is AUTHORITATIVE. You must NOT change, \
re-derive, or reinterpret recovery_decision, safe_to_release, risk_score, or \
safe_to_release_probability. You only explain what is given.
- Never invent failure causes, transaction details, refund guarantees, \
processing times, account information, financial outcomes, or policy \
decisions that are not present in the provided data.
- Use EXACTLY the provided numbers. They are pre-formatted; do not round, \
convert, or recompute them.
- If reconstruction evidence is provided, it is DETERMINISTIC system output \
and authoritative: report it faithfully, do not re-derive, reinterpret, or \
contradict it. The audience filtering still applies.
- If a risk assessment is provided it is the output of a deterministic \
rules engine: report its classification and recovery eligibility \
faithfully; do not re-derive, soften, or escalate it, and do not use the \
words "fraud" or "fraudulent" about any person — describe classifications \
neutrally (e.g. "flagged for manual review").
- If recovery information is provided it describes an action ALREADY decided \
and executed by a deterministic safety-gated engine in a SIMULATED SANDBOX: \
report it faithfully, never claim or promise real money movement, and never \
present recovery as a decision the assistant made.
- Output plain text only: no JSON, no markdown, no headers, no bullet lists.
- For customer audience: at most 4 short sentences, non-technical, no ML \
jargon (never say "XGBoost", "classifier", "probability", "model", "score").
- Bangla output must be natural Bangla (not a word-for-word translation); \
English output must be clear and professional.\
"""

# Fields that must NEVER reach a customer-facing prompt. risk_assessment is
# excluded ENTIRELY: customers get review status only through neutral
# fallback/template phrasing, never raw anomaly data or classifications.
_CUSTOMER_EXCLUDED_FIELDS = (
    "risk_score",
    "safe_to_release_probability",
    "timeline",
    "risk_assessment",
    # Stage 8: the ENTIRE recovery object is stripped for customers — they get
    # resolved/under-review wording through fallback/template phrasing only,
    # never internal statuses, action codes, reasons, or provider references.
    "recovery",
)

# Reconstruction fields excluded from CUSTOMER payloads, ON TOP of the general
# exclusions above. evidence_summary and the stage statuses ARE
# customer-appropriate; missing_events is internal bookkeeping (a diff of the
# expected vs observed event trace) that customers neither need nor should see.
_CUSTOMER_EXCLUDED_RECONSTRUCTION_FIELDS = ("reconstruction.missing_events",)

_TONE_BY_AUDIENCE = {
    Audience.CUSTOMER: (
        "Audience: a bank customer with no technical background. Be warm, "
        "simple and reassuring; avoid all jargon; at most 4 short sentences. "
        "Do not mention internal scores, probabilities, or system internals."
    ),
    Audience.SUPPORT: (
        "Audience: a support agent. Give a concise factual summary covering "
        "status, failure reason, ML prediction, risk, safe-release probability, "
        "decision and policy reason. Professional tone."
    ),
    Audience.SYSTEM: (
        "Audience: an internal system/log consumer. Give a terse, factual, "
        "auditable summary with no pleasantries."
    ),
}


def _payload(context: ExplanationContext) -> dict:
    """Schema-controlled JSON payload for the model, after audience filtering."""
    data = context.model_dump(exclude_none=True)
    # language/audience are conveyed separately as instructions, not data.
    data.pop("language", None)
    data.pop("audience", None)
    if context.audience == Audience.CUSTOMER:
        for field in _CUSTOMER_EXCLUDED_FIELDS:
            data.pop(field, None)
            logger.debug("customer audience: excluded field %s from payload", field)
        recon = data.get("reconstruction")
        if isinstance(recon, dict):
            for field in _CUSTOMER_EXCLUDED_RECONSTRUCTION_FIELDS:
                _, sub = field.split(".", 1)
                recon.pop(sub, None)
                logger.debug("customer audience: excluded field %s from payload", field)
    return data


def build_messages(context: ExplanationContext) -> list[dict]:
    """Build chat.completions-style messages for the provider."""
    payload = _payload(context)
    user_content = (
        f"{_TONE_BY_AUDIENCE[context.audience]}\n\n"
        f"Output language: {'Bangla' if context.language == 'bn' else 'English'}.\n\n"
        "Transaction data (authoritative — explain exactly this, invent nothing):\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

"""
api/services/ai/fallback.py — deterministic template explanations.

When an AI provider is unavailable or returns an unusable answer, the layer
falls back to these templates so an explanation is ALWAYS produced. The
templates use ONLY ExplanationContext fields (all pre-formatted), so they can
never invent data, alter the decision, or recalculate anything — same
guarantee as the AI path, just without a model.

Bangla texts are the reviewed, approved copy; failure reasons are mapped via
a fixed table covering the five Stage-1 reasons, with a safe generic phrase
for anything unknown.
"""

from __future__ import annotations

import logging

from api.services.ai.schemas import Audience, ExplanationContext, Language

logger = logging.getLogger("payment_recovery.ai.fallback")

# Stage-1 failure reasons -> natural Bangla causal phrase (reason -> "কারণে").
_BN_FAILURE_REASONS = {
    "Timeout": "নির্ধারিত সময়ের মধ্যে গেটওয়ে সাড়া না পাওয়ায়",
    "Merchant Disconnect": "মার্চেন্ট সংযোগ বিচ্ছিন্ন হওয়ায়",
    "Insufficient Balance": "অপর্যাপ্ত ব্যালেন্সের কারণে",
    "Network Drop": "নেটওয়ার্ক সংযোগ বিচ্ছিন্ন হওয়ায়",
    "Gateway Error": "গেটওয়ে ত্রুটির কারণে",
}
_BN_FAILURE_UNKNOWN = "প্রযুক্তিগত সমস্যার কারণে"

# Stage-1 failure reasons -> English causal phrase.
_EN_FAILURE_REASONS = {
    "Timeout": "due to a gateway timeout",
    "Merchant Disconnect": "due to a merchant disconnect",
    "Insufficient Balance": "due to insufficient balance",
    "Network Drop": "due to a network drop",
    "Gateway Error": "due to a gateway error",
}
_EN_FAILURE_UNKNOWN = "due to a technical issue"


def _bn_failure(context: ExplanationContext) -> str:
    if not context.failure_reason:
        return _BN_FAILURE_UNKNOWN
    return _BN_FAILURE_REASONS.get(context.failure_reason, _BN_FAILURE_UNKNOWN)


def _en_failure(context: ExplanationContext) -> str:
    if not context.failure_reason:
        return _EN_FAILURE_UNKNOWN
    return _EN_FAILURE_REASONS.get(context.failure_reason, _EN_FAILURE_UNKNOWN)


def _bn_customer(context: ExplanationContext) -> str:
    failure_bn = _bn_failure(context)
    decision = context.recovery_decision
    if decision == "LIMIT_RELEASED":
        return (
            f"আপনার পেমেন্টটি {failure_bn} সম্পন্ন হয়নি। "
            "সিস্টেম লেনদেনটি পর্যালোচনা করে পুনরুদ্ধারের জন্য নিরাপদ বলে "
            "নিশ্চিত হয়েছে। তাই আপনার সাময়িকভাবে আটকে থাকা লিমিট পুনরায় "
            "চালু করা হয়েছে।"
        )
    if decision == "MANUAL_REVIEW":
        return (
            f"আপনার পেমেন্টটি {failure_bn} সম্পন্ন হয়নি। "
            "লেনদেনটি বর্তমানে আমাদের পর্যালোচনাধীন রয়েছে। "
            "পর্যালোচনা শেষে আপনাকে জানানো হবে।"
        )
    if decision == "RECOVERY_REJECTED":
        return (
            f"আপনার পেমেন্টটি {failure_bn} সম্পন্ন হয়নি। "
            "নিরাপত্তা নীতির ভিত্তিতে স্বয়ংক্রিয় পুনরুদ্ধার প্রযোজ্য নয়। "
            "বিস্তারিত জানতে সহায়তা টিমের সঙ্গে যোগাযোগ করুন।"
        )
    return (
        f"আপনার লেনদেনটি {failure_bn} সম্পন্ন হয়নি। "
        "এটি বর্তমানে পর্যালোচনাধীন রয়েছে।"
    )


def _en_customer(context: ExplanationContext) -> str:
    failure_en = _en_failure(context)
    decision = context.recovery_decision
    if decision == "LIMIT_RELEASED":
        return (
            f"Your payment was not completed {failure_en}. "
            "The system reviewed the transaction and confirmed it is safe to "
            "recover, so the limit that was temporarily held has been restored."
        )
    if decision == "MANUAL_REVIEW":
        return (
            f"Your payment was not completed {failure_en}. "
            "The transaction is currently under review, and we will let you "
            "know once the review is complete."
        )
    if decision == "RECOVERY_REJECTED":
        return (
            f"Your payment was not completed {failure_en}. "
            "Automatic recovery does not apply based on our security policy. "
            "Please contact our support team for details."
        )
    return (
        f"Your transaction was not completed {failure_en}. "
        "It is currently under review."
    )


def _support_summary(context: ExplanationContext) -> str:
    """Multi-line factual summary — ONLY fields present in the context."""
    lines: list[str] = []
    if context.language == Language.BN:
        failure = _bn_failure(context)
        lines.append(f"লেনদেন আইডি: {context.transaction_id}")
        lines.append(f"স্ট্যাটাস: {context.transaction_status}")
        lines.append(f"পরিমাণ: {context.amount}")
        if context.failure_reason:
            lines.append(f"ব্যর্থতার কারণ: {context.failure_reason} ({failure})")
        if context.failure_prediction:
            lines.append(f"ব্যর্থতা পূর্বাভাস: {context.failure_prediction}")
        if context.risk_score is not None:
            lines.append(f"ঝুঁকি স্কোর: {context.risk_score}")
        if context.safe_to_release_probability is not None:
            lines.append(f"নিরাপদ রিলিজ সম্ভাবনা: {context.safe_to_release_probability}")
        if context.recovery_decision:
            lines.append(f"সিদ্ধান্ত: {context.recovery_decision}")
        if context.recovery_reason:
            lines.append(f"নীতির কারণ: {context.recovery_reason}")
        if context.timeline:
            lines.append("টাইমলাইন:")
            lines.extend(f"- {entry}" for entry in context.timeline)
        return "\n".join(lines)

    failure = _en_failure(context)
    lines.append(f"Transaction ID: {context.transaction_id}")
    lines.append(f"Status: {context.transaction_status}")
    lines.append(f"Amount: {context.amount}")
    if context.failure_reason:
        lines.append(f"Failure reason: {context.failure_reason} ({failure})")
    if context.failure_prediction:
        lines.append(f"ML failure prediction: {context.failure_prediction}")
    if context.risk_score is not None:
        lines.append(f"Risk score: {context.risk_score}")
    if context.safe_to_release_probability is not None:
        lines.append(f"Safe-to-release probability: {context.safe_to_release_probability}")
    if context.recovery_decision:
        lines.append(f"Decision: {context.recovery_decision}")
    if context.recovery_reason:
        lines.append(f"Policy reason: {context.recovery_reason}")
    if context.timeline:
        lines.append("Timeline:")
        lines.extend(f"- {entry}" for entry in context.timeline)
    return "\n".join(lines)


def fallback_explanation(context: ExplanationContext) -> str:
    """Deterministic, data-faithful explanation built from context only."""
    if context.audience == Audience.CUSTOMER:
        text = (
            _bn_customer(context)
            if context.language == Language.BN
            else _en_customer(context)
        )
    else:  # SUPPORT and SYSTEM share the factual-summary template
        text = _support_summary(context)
    logger.debug(
        "fallback explanation built: tx=%s lang=%s audience=%s",
        context.transaction_id,
        context.language.value,
        context.audience.value,
    )
    return text

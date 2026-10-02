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

# Stage 6 reconstruction root causes -> deterministic Bangla sentence. Fixed
# map: reconstruction evidence is authoritative and deterministic, so the
# fallback rendering of it must be too — no free-text generation.
_BN_ROOT_CAUSES = {
    "NONE": "লেনদেনটি সফলভাবে সম্পন্ন হয়েছে।",
    "INCOMPLETE": "লেনদেনের সম্পূর্ণ তথ্য এখনো পাওয়া যায়নি।",
    "CUSTOMER_DEBIT_FAILED": "কাস্টমার ডেবিট ব্যর্থ হয়েছে।",
    "GATEWAY_TIMEOUT": "গেটওয়ে টাইমআউট হয়েছে।",
    "GATEWAY_ERROR": "গেটওয়ে ত্রুটি হয়েছে।",
    "MERCHANT_CONFIRMATION_TIMEOUT": "মার্চেন্ট কনফার্মেশনের সময়সীমা শেষ হয়ে গেছে।",
    "MERCHANT_ERROR": "মার্চেন্ট ত্রুটি হয়েছে।",
    "SETTLEMENT_FAILED": "সেটেলমেন্ট ব্যর্থ হয়েছে।",
    "SETTLEMENT_NOT_CONFIRMED": "সেটেলমেন্ট নিশ্চিত হয়নি।",
}

# Stage 6 reconstruction root causes -> deterministic English sentence.
_EN_ROOT_CAUSES = {
    "NONE": "The transaction completed successfully.",
    "INCOMPLETE": "The payment flow is incomplete; the final outcome is not yet known.",
    "CUSTOMER_DEBIT_FAILED": "Root cause: customer debit failed.",
    "GATEWAY_TIMEOUT": "Root cause: gateway timeout.",
    "GATEWAY_ERROR": "Root cause: gateway error.",
    "MERCHANT_CONFIRMATION_TIMEOUT": "Root cause: merchant confirmation timeout.",
    "MERCHANT_ERROR": "Root cause: merchant error.",
    "SETTLEMENT_FAILED": "Root cause: settlement failed.",
    "SETTLEMENT_NOT_CONFIRMED": "Root cause: settlement not confirmed.",
}


# Stage 7 anomaly types -> humanized English label. Fixed map: the
# classification comes from the deterministic rules engine, so its rendering
# is deterministic too.
_EN_ANOMALY_TYPES = {
    "GENUINE_FAILURE": "genuine transaction failure",
    "DOUBLE_DEDUCTION": "double deduction",
    "DUPLICATE_TRANSACTION": "duplicate transaction",
    "SUCCESSFUL_BUT_UNCONFIRMED": "successful but unconfirmed",
    "FALSE_COMPLAINT": "possible false complaint",
    "SUSPICIOUS": "suspicious",
    "INCOMPLETE": "incomplete information",
    "UNKNOWN": "unknown",
    "NONE": "no anomaly detected",
}

# Stage 7 anomaly types -> natural Bangla label.
_BN_ANOMALY_TYPES = {
    "GENUINE_FAILURE": "সত্যিকারের লেনদেন ব্যর্থতা",
    "DOUBLE_DEDUCTION": "দ্বিগুণ ডেবিট",
    "DUPLICATE_TRANSACTION": "ডুপ্লিকেট লেনদেন",
    "SUCCESSFUL_BUT_UNCONFIRMED": "সফল কিন্তু নিশ্চিতকরণবিহীন",
    "FALSE_COMPLAINT": "সম্ভাব্য ভুল অভিযোগ",
    "SUSPICIOUS": "সন্দেহজনক",
    "INCOMPLETE": "অসম্পূর্ণ তথ্য",
    "UNKNOWN": "অজানা",
    "NONE": "কোনো অসঙ্গতি নেই",
}


def _risk_line(context: ExplanationContext) -> str | None:
    """Deterministic classification line for SUPPORT audiences from the Stage 7
    risk assessment, or None when no assessment is attached. The classification
    is REPORTED verbatim (it belongs to the rules engine), never re-derived."""
    ra = context.risk_assessment
    if ra is None:
        return None
    if context.language == Language.BN:
        anomaly = _BN_ANOMALY_TYPES.get(ra.anomaly_type, ra.anomaly_type)
        eligibility = (
            "যোগ্য — পুনরুদ্ধার সিদ্ধান্ত অপেক্ষমাণ"
            if ra.recovery_candidate
            else "যোগ্য নয়"
        )
        if not ra.recovery_candidate and ra.recovery_block_reason:
            eligibility += f" — {ra.recovery_block_reason}"
        return (
            f"অসঙ্গতি শ্রেণিবিন্যাস: {anomaly} (ঝুঁকির স্তর: {ra.risk_level})। "
            f"পুনরুদ্ধারের যোগ্যতা: {eligibility}।"
        )
    anomaly = _EN_ANOMALY_TYPES.get(ra.anomaly_type, ra.anomaly_type)
    eligibility = (
        "eligible — pending recovery decision"
        if ra.recovery_candidate
        else "not eligible"
    )
    if not ra.recovery_candidate and ra.recovery_block_reason:
        eligibility += f" — {ra.recovery_block_reason}"
    return (
        f"Anomaly classification: {anomaly} (risk level: {ra.risk_level}). "
        f"Recovery eligibility: {eligibility}."
    )


def _customer_review_line(context: ExplanationContext) -> str | None:
    """Neutral review-status line for CUSTOMER audiences when a risk assessment
    exists. Deliberately contains NO anomaly terminology — customers get
    review status only, never raw classifications."""
    if context.risk_assessment is None:
        return None
    if context.language == Language.BN:
        return (
            "আপনার লেনদেনটি পেমেন্ট সিস্টেমের প্রমাণের ভিত্তিতে মূল্যায়ন করা "
            "হচ্ছে। পুনরুদ্ধারের যোগ্যতা: পর্যালোচনাধীন।"
        )
    return (
        "Your transaction is being evaluated using payment-system evidence. "
        "Recovery eligibility: under review."
    )


def _root_cause_sentence(context: ExplanationContext) -> str | None:
    """Deterministic first sentence for the reconstruction evidence, or None
    when no reconstruction is attached to the context."""
    recon = context.reconstruction
    if recon is None:
        return None
    if context.language == Language.BN:
        return _BN_ROOT_CAUSES.get(recon.root_cause)
    return _EN_ROOT_CAUSES.get(recon.root_cause)


# Stage 8 recovery actions -> humanized English label. Fixed map: the action
# comes from the deterministic policy engine, so its rendering is too.
_EN_RECOVERY_ACTIONS = {
    "RELEASE_LIMIT": "release limit",
    "NO_ACTION": "no action",
    "MANUAL_REVIEW": "manual review",
}

# Stage 8 recovery action statuses -> humanized English label.
_EN_RECOVERY_STATUSES = {
    "PENDING": "pending",
    "EXECUTING": "executing",
    "COMPLETED": "completed",
    "FAILED": "failed",
    "BLOCKED": "blocked",
    "VERIFICATION_PENDING": "verification pending",
    "VERIFIED": "verified",
}


def _recovery_customer_line(context: ExplanationContext) -> str | None:
    """Stage 8 resolved/under-review line for CUSTOMER audiences. The
    resolved-release wording appears ONLY when status == VERIFIED — the system
    never claims a release before verification confirms it. Any other status
    yields neutral under-review wording (no internal statuses, no action
    codes, no provider details). Returns None when no recovery is attached."""
    rec = context.recovery
    if rec is None:
        return None
    if rec.verified:
        reference = rec.provider_reference or "n/a"
        if context.language == Language.BN:
            return (
                "পেমেন্ট সমস্যা সমাধান হয়েছে: আটকে থাকা টাকা স্বয়ংক্রিয়ভাবে "
                f"ছেড়ে দেওয়া হয়েছে (রেফারেন্স {reference})।"
            )
        return (
            "Payment issue resolved: the blocked amount has been automatically "
            f"released (reference {reference})."
        )
    if context.language == Language.BN:
        return (
            "আপনার লেনদেনটি বর্তমানে আমাদের পর্যালোচনাধীন রয়েছে। "
            "পর্যালোচনা শেষে আপনাকে জানানো হবে।"
        )
    return (
        "Your case is currently under review, and we will let you know once "
        "the review is complete."
    )


def _recovery_support_line(context: ExplanationContext) -> str | None:
    """Faithful recovery-outcome line for SUPPORT audiences — full recovery
    detail (action, status, reasons, sandbox provider marker), reported from
    the stored record only."""
    rec = context.recovery
    if rec is None:
        return None
    if context.language == Language.BN:
        lines = [
            f"স্বয়ংক্রিয় পুনরুদ্ধার: {rec.action} — স্ট্যাটাস {rec.status} "
            "(সিমুলেটেড স্যান্ডবক্স প্রোভাইডার)",
        ]
        if rec.decision_reason:
            lines.append(f"সিদ্ধান্তের কারণ: {rec.decision_reason}")
        if rec.blocked_reason:
            lines.append(f"ব্লকের কারণ: {rec.blocked_reason}")
        if rec.failure_reason:
            lines.append(f"ব্যর্থতার কারণ: {rec.failure_reason}")
        if rec.provider_reference:
            lines.append(f"রেফারেন্স: {rec.provider_reference}")
        return " ".join(lines)
    action = _EN_RECOVERY_ACTIONS.get(rec.action, rec.action)
    status = _EN_RECOVERY_STATUSES.get(rec.status, rec.status)
    lines = [
        f"Autonomous recovery: {action} — status {status} "
        "(simulated sandbox provider)",
    ]
    if rec.decision_reason:
        lines.append(f"Decision reason: {rec.decision_reason}")
    if rec.blocked_reason:
        lines.append(f"Blocked reason: {rec.blocked_reason}")
    if rec.failure_reason:
        lines.append(f"Failure reason: {rec.failure_reason}")
    if rec.provider_reference:
        lines.append(f"Reference: {rec.provider_reference}")
    return " ".join(lines)


def _bn_failure(context: ExplanationContext) -> str:
    if not context.failure_reason:
        return _BN_FAILURE_UNKNOWN
    return _BN_FAILURE_REASONS.get(context.failure_reason, _BN_FAILURE_UNKNOWN)


def _en_failure(context: ExplanationContext) -> str:
    if not context.failure_reason:
        return _EN_FAILURE_UNKNOWN
    return _EN_FAILURE_REASONS.get(context.failure_reason, _EN_FAILURE_UNKNOWN)


def _bn_customer(context: ExplanationContext, decision: str | None = None) -> str:
    failure_bn = _bn_failure(context)
    if decision is None:
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


def _en_customer(context: ExplanationContext, decision: str | None = None) -> str:
    failure_en = _en_failure(context)
    if decision is None:
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
        # Stage 8: while a recovery action is NOT yet VERIFIED, the customer
        # text must never claim a release — downgrade a LIMIT_RELEASED decision
        # to the neutral review wording until verification confirms it.
        decision = context.recovery_decision
        if (
            context.recovery is not None
            and not context.recovery.verified
            and decision == "LIMIT_RELEASED"
        ):
            decision = "MANUAL_REVIEW"
        text = (
            _bn_customer(context, decision)
            if context.language == Language.BN
            else _en_customer(context, decision)
        )
    else:  # SUPPORT and SYSTEM share the factual-summary template
        text = _support_summary(context)
    # Reconstruction evidence is authoritative and deterministic — when it is
    # attached, its fixed-map sentence leads the explanation.
    root_cause_sentence = _root_cause_sentence(context)
    if root_cause_sentence:
        text = f"{root_cause_sentence} {text}"
    # Stage 7 risk assessment is likewise deterministic engine output. Support
    # sees the classification; customers see only a neutral review-status line.
    risk_line = _risk_line(context) if context.audience != Audience.CUSTOMER else None
    if risk_line:
        text = f"{risk_line} {text}"
    review_line = (
        _customer_review_line(context) if context.audience == Audience.CUSTOMER else None
    )
    if review_line:
        text = f"{text} {review_line}"
    # Stage 8 recovery outcome is deterministic engine output. Customers get
    # the resolved line ONLY on VERIFIED, else neutral review wording; support
    # sees the full outcome with the sandbox marker.
    recovery_line = (
        _recovery_customer_line(context)
        if context.audience == Audience.CUSTOMER
        else _recovery_support_line(context)
    )
    if recovery_line:
        if context.audience == Audience.CUSTOMER:
            text = f"{text} {recovery_line}"
        else:
            text = f"{recovery_line} {text}"
    logger.debug(
        "fallback explanation built: tx=%s lang=%s audience=%s",
        context.transaction_id,
        context.language.value,
        context.audience.value,
    )
    return text

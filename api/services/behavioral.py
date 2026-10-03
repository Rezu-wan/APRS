"""
api/services/behavioral.py — Stage 11 Phase C: Online Anomaly Intelligence.

An OPTIONAL, fully EXPLAINABLE statistical behavioral-signal layer on top of
the existing data. This is NOT the offline ML stack (3 XGBoost models +
anomaly classifier stay untouched and authoritative): these signals are
rolling statistics (z-score, counts, rates) computed from the EXISTING
transactions / payment_events tables at read time.

Honesty contract (spec §22):
  * When history is insufficient (fewer than MIN_BASELINE comparable
    samples) a signal's level is "UNKNOWN" with a description saying why —
    levels are NEVER fabricated.
  * Signals are ADVISORY analysis of stored data. This module is read-only:
    it never writes transactions, events, or assessments, and it does NOT
    feed the recovery policy or the safety gate.
  * Deterministic given the same DB state (pure functions + thin queries;
    only ``computed_at`` differs between calls).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Sequence

from pydantic import BaseModel

from api.core.payment_lifecycle import (
    OUTCOME_CONFIRMED,
    OUTCOME_ERROR,
    OUTCOME_FAILED,
    OUTCOME_TIMEOUT,
    PaymentSource,
)
from api.core.state_machine import TransactionState
from api.db.models import PaymentEvent, Transaction

FEATURE_VERSION = "behavioral-v1"

# Below this many comparable samples a statistical signal reports UNKNOWN.
MIN_BASELINE = 5

# Cap on prior rows pulled for a user's history (bounded work per request).
HISTORY_CAP = 200

LEVEL_LOW = "LOW"
LEVEL_MEDIUM = "MEDIUM"
LEVEL_HIGH = "HIGH"
LEVEL_UNKNOWN = "UNKNOWN"

# Gateway-event outcomes that count as failures for rate signals.
_GATEWAY_FAILURE_STATUSES = (OUTCOME_FAILED, OUTCOME_TIMEOUT, OUTCOME_ERROR)

_GATEWAY_WINDOW = timedelta(days=7)


class BehavioralSignal(BaseModel):
    """One explainable behavioral signal. ``value`` is the raw statistic,
    ``basis`` names the method (z_score / count_rule / rate), and ``level``
    is the honest verdict — UNKNOWN whenever history is insufficient."""

    code: str
    label: str
    value: float | str | None
    unit: str | None
    level: str  # LOW | MEDIUM | HIGH | UNKNOWN
    description: str
    window: str  # e.g. "24h", "7d", "all_history"
    basis: str  # z_score | ewma | count_rule | rate


class BehavioralReport(BaseModel):
    transaction_id: str
    signals: list[BehavioralSignal]
    summary: dict  # {high_count, medium_count, unknown_count, overall}
    computed_at: str  # tz-aware ISO instant
    feature_version: str = FEATURE_VERSION


# ---------------------------------------------------------------------------
# math helpers (defensive against degenerate statistics)
# ---------------------------------------------------------------------------


def _aware(dt: datetime) -> datetime:
    """SQLite may hand back naive datetimes; treat stored wall time as UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stddev(values: Sequence[float], mean: float) -> float:
    """Sample standard deviation. Single sample (or zero variance) -> 0.0;
    the caller decides whether that makes a z-score meaningful."""
    n = len(values)
    if n < 2:
        return 0.0
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)


def _z_score(value: float, baseline: Sequence[float]) -> float | None:
    """z of ``value`` against ``baseline``. None when the baseline is too
    small (n < MIN_BASELINE). With zero-variance baselines an equal value
    scores 0.0 and ANY different value is maximally unusual (capped at 99.0
    so it stays finite JSON) — an honest reading of a flat history."""
    if len(baseline) < MIN_BASELINE:
        return None
    mean = _mean(baseline)
    std = _stddev(baseline, mean)
    if std == 0.0:
        return 0.0 if value == mean else 99.0
    return (value - mean) / std


def _level_from_z(z: float | None, high: float, medium: float) -> str:
    if z is None:
        return LEVEL_UNKNOWN
    az = abs(z)
    if az >= high:
        return LEVEL_HIGH
    if az >= medium:
        return LEVEL_MEDIUM
    return LEVEL_LOW


def _z_description(label: str, z: float | None, n: int, unit: str) -> str:
    if z is None:
        return (
            f"{label}: insufficient history — {n} comparable sample(s) "
            f"available, {MIN_BASELINE} required for a baseline"
        )
    return f"{label}: z={z:+.2f} against {n} prior samples ({unit})"


# ---------------------------------------------------------------------------
# query helpers (thin, read-only)
# ---------------------------------------------------------------------------


def _user_history(db, tx: Transaction) -> list[Transaction]:
    """The user's prior transactions (all history, newest last, capped)."""
    return (
        db.query(Transaction)
        .filter(
            Transaction.user_id == tx.user_id,
            Transaction.timestamp < tx.timestamp,
        )
        .order_by(Transaction.timestamp.asc(), Transaction.id.asc())
        .limit(HISTORY_CAP)
        .all()
    )


def _merchant_transaction_ids(db, merchant_id: str) -> list[str]:
    return [
        row[0]
        for row in db.query(Transaction.transaction_id)
        .filter(Transaction.merchant_id == merchant_id)
        .all()
    ]


def _gateway_events(
    db,
    before: datetime,
    transaction_ids: list[str] | None = None,
) -> list[PaymentEvent]:
    """GATEWAY-source payment events inside the 7d window ending at
    ``before`` — platform-wide when ``transaction_ids`` is None, otherwise
    restricted to the given transactions."""
    q = db.query(PaymentEvent).filter(
        PaymentEvent.source == PaymentSource.GATEWAY,
        PaymentEvent.event_timestamp >= before - _GATEWAY_WINDOW,
        PaymentEvent.event_timestamp <= before,
    )
    if transaction_ids is not None:
        if not transaction_ids:
            return []
        q = q.filter(PaymentEvent.transaction_id.in_(transaction_ids))
    return q.all()


def _failure_rate(events: Sequence[PaymentEvent]) -> tuple[int, int]:
    """(failures, total) over gateway events (CONFIRMED vs failure trio)."""
    total = sum(1 for e in events if e.status in _GATEWAY_FAILURE_STATUSES or e.status == OUTCOME_CONFIRMED)
    failures = sum(1 for e in events if e.status in _GATEWAY_FAILURE_STATUSES)
    return failures, total


def _rate_level(failures: int, total: int) -> tuple[str, float | None]:
    if total < MIN_BASELINE:
        return LEVEL_UNKNOWN, None
    rate = failures / total if total else 0.0
    if rate >= 0.5:
        return LEVEL_HIGH, rate
    if rate >= 0.25:
        return LEVEL_MEDIUM, rate
    return LEVEL_LOW, rate


# ---------------------------------------------------------------------------
# signal computations
# ---------------------------------------------------------------------------


def _signal_frequency(db, tx: Transaction, prior: list[Transaction]) -> BehavioralSignal:
    window_start = _aware(tx.timestamp) - timedelta(hours=24)
    count = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == tx.user_id,
            Transaction.timestamp >= window_start,
            Transaction.timestamp <= tx.timestamp,
        )
        .count()
    )
    if count <= 1 and not prior:
        level = LEVEL_UNKNOWN
        description = (
            "transaction_frequency: no history at all — only this transaction "
            "exists for the user, so frequency cannot be judged"
        )
    elif count >= 10:
        level = LEVEL_HIGH
        description = f"transaction_frequency: {count} transactions in the 24h window (>= 10)"
    elif count >= 5:
        level = LEVEL_MEDIUM
        description = f"transaction_frequency: {count} transactions in the 24h window (>= 5)"
    else:
        level = LEVEL_LOW
        description = f"transaction_frequency: {count} transactions in the 24h window (< 5)"
    return BehavioralSignal(
        code="transaction_frequency",
        label="Transaction frequency (24h)",
        value=float(count),
        unit="transactions",
        level=level,
        description=description,
        window="24h",
        basis="count_rule",
    )


def _signal_retry(db, tx: Transaction, prior: list[Transaction]) -> BehavioralSignal:
    baseline = [float(p.retry_count) for p in prior[-10:]]
    z = _z_score(float(tx.retry_count), baseline)
    return BehavioralSignal(
        code="retry_frequency",
        label="Retry frequency deviation",
        value=round(z, 4) if z is not None else None,
        unit="z",
        level=_level_from_z(z, high=2.0, medium=1.0),
        description=_z_description("retry_frequency", z, len(baseline), "retries"),
        window="last_10",
        basis="z_score",
    )


def _signal_amount(db, tx: Transaction, prior: list[Transaction]) -> BehavioralSignal:
    baseline = [float(p.amount) for p in prior]
    z = _z_score(float(tx.amount), baseline)
    return BehavioralSignal(
        code="amount_deviation",
        label="Amount deviation",
        value=round(z, 4) if z is not None else None,
        unit="z",
        level=_level_from_z(z, high=3.0, medium=2.0),
        description=_z_description("amount_deviation", z, len(baseline), "BDT"),
        window="all_history",
        basis="z_score",
    )


def _signal_recent_failures(db, tx: Transaction) -> BehavioralSignal:
    window_start = _aware(tx.timestamp) - timedelta(hours=24)
    count = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == tx.user_id,
            Transaction.current_state == TransactionState.FAILED,
            Transaction.timestamp >= window_start,
            Transaction.timestamp <= tx.timestamp,
        )
        .count()
    )
    if count >= 3:
        level, rule = LEVEL_HIGH, ">= 3"
    elif count >= 1:
        level, rule = LEVEL_MEDIUM, ">= 1"
    else:
        level, rule = LEVEL_LOW, "0"
    return BehavioralSignal(
        code="recent_failure_count",
        label="Recent failed transactions (24h)",
        value=float(count),
        unit="transactions",
        level=level,
        description=f"recent_failure_count: {count} FAILED transactions in 24h ({rule})",
        window="24h",
        basis="count_rule",
    )


def _signal_latency(db, tx: Transaction, prior: list[Transaction]) -> BehavioralSignal:
    baseline = [float(p.gateway_latency_ms) for p in prior]
    z = _z_score(float(tx.gateway_latency_ms), baseline)
    return BehavioralSignal(
        code="latency_deviation",
        label="Gateway latency deviation",
        value=round(z, 4) if z is not None else None,
        unit="z",
        level=_level_from_z(z, high=3.0, medium=2.0),
        description=_z_description(
            "latency_deviation", z, len(baseline), "gateway_latency_ms"
        ),
        window="all_history",
        basis="z_score",
    )


def _signal_gateway_rate(db, tx: Transaction) -> BehavioralSignal:
    events = _gateway_events(db, _aware(tx.timestamp))
    failures, total = _failure_rate(events)
    level, rate = _rate_level(failures, total)
    if rate is None:
        description = (
            f"gateway_failure_rate: only {total} gateway event(s) in the 7d "
            f"window — {MIN_BASELINE} required for a rate"
        )
    else:
        user_events = _gateway_events(
            db, _aware(tx.timestamp), [tx.transaction_id]
        )
        user_failures, user_total = _failure_rate(user_events)
        description = (
            f"gateway_failure_rate: platform-wide gateway failure rate "
            f"{rate:.2f} ({failures}/{total} events, 7d); this user: "
            f"{user_failures}/{user_total}"
        )
    return BehavioralSignal(
        code="gateway_failure_rate",
        label="Platform gateway failure rate (7d)",
        value=round(rate, 4) if rate is not None else None,
        unit="rate",
        level=level,
        description=description,
        window="7d",
        basis="rate",
    )


def _signal_merchant_rate(db, tx: Transaction) -> BehavioralSignal:
    tx_ids = _merchant_transaction_ids(db, tx.merchant_id)
    events = _gateway_events(db, _aware(tx.timestamp), tx_ids)
    failures, total = _failure_rate(events)
    level, rate = _rate_level(failures, total)
    if rate is None:
        description = (
            f"merchant_failure_rate: only {total} gateway event(s) for this "
            f"merchant in the 7d window — {MIN_BASELINE} required for a rate"
        )
    else:
        description = (
            f"merchant_failure_rate: gateway failure rate {rate:.2f} "
            f"({failures}/{total} events, 7d, merchant {tx.merchant_id})"
        )
    return BehavioralSignal(
        code="merchant_failure_rate",
        label="Merchant gateway failure rate (7d)",
        value=round(rate, 4) if rate is not None else None,
        unit="rate",
        level=level,
        description=description,
        window="7d",
        basis="rate",
    )


def _signal_timing(prior: list[Transaction], tx: Transaction) -> BehavioralSignal:
    hour = _aware(tx.timestamp).hour
    prior_hours = {_aware(p.timestamp).hour for p in prior}
    if hour not in prior_hours and len(prior) >= MIN_BASELINE:
        level = LEVEL_MEDIUM
        description = (
            f"unusual_timing: hour {hour} has no prior activity for this user "
            f"({len(prior)} prior transactions) — first activity in this hour bucket"
        )
    else:
        level = LEVEL_LOW
        description = f"unusual_timing: hour {hour} is consistent with prior activity"
    return BehavioralSignal(
        code="unusual_timing",
        label="Unusual hour of day",
        value=str(hour),
        unit="hour",
        level=level,
        description=description,
        window="all_history",
        basis="count_rule",
    )


def _signal_burst(db, tx: Transaction) -> BehavioralSignal:
    start = _aware(tx.timestamp) - timedelta(minutes=5)
    end = _aware(tx.timestamp) + timedelta(minutes=5)
    count = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == tx.user_id,
            Transaction.timestamp >= start,
            Transaction.timestamp <= end,
            Transaction.transaction_id != tx.transaction_id,
        )
        .count()
    )
    if count >= 5:
        level, rule = LEVEL_HIGH, ">= 5"
    elif count >= 3:
        level, rule = LEVEL_MEDIUM, ">= 3"
    else:
        level, rule = LEVEL_LOW, "< 3"
    return BehavioralSignal(
        code="transaction_burst",
        label="Transaction burst (+/-5 min)",
        value=float(count),
        unit="transactions",
        level=level,
        description=f"transaction_burst: {count} other transactions within +/-5 minutes ({rule})",
        window="10m",
        basis="count_rule",
    )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def compute_behavioral_signals(db, tx: Transaction) -> BehavioralReport:
    """Compute the full behavioral report for one transaction. Read-only and
    deterministic given the same DB state (only ``computed_at`` varies)."""
    prior = _user_history(db, tx)

    signals = [
        _signal_frequency(db, tx, prior),
        _signal_retry(db, tx, prior),
        _signal_amount(db, tx, prior),
        _signal_recent_failures(db, tx),
        _signal_latency(db, tx, prior),
        _signal_gateway_rate(db, tx),
        _signal_merchant_rate(db, tx),
        _signal_timing(prior, tx),
        _signal_burst(db, tx),
    ]

    high = sum(1 for s in signals if s.level == LEVEL_HIGH)
    medium = sum(1 for s in signals if s.level == LEVEL_MEDIUM)
    unknown = sum(1 for s in signals if s.level == LEVEL_UNKNOWN)
    if high:
        overall = LEVEL_HIGH
    elif medium:
        overall = LEVEL_MEDIUM
    elif unknown == len(signals):
        overall = LEVEL_UNKNOWN
    else:
        overall = LEVEL_LOW

    return BehavioralReport(
        transaction_id=tx.transaction_id,
        signals=signals,
        summary={
            "high_count": high,
            "medium_count": medium,
            "unknown_count": unknown,
            "overall": overall,
        },
        computed_at=datetime.now(timezone.utc).isoformat(),
        feature_version=FEATURE_VERSION,
    )

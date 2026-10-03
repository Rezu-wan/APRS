"""
api/services/relationship.py — Stage 11 Phase 11D transaction relationship
graph (EXPLAINABLE, ADVISORY-ONLY, SANDBOX).

Entity honesty (important): the payment-recovery data model has NO device and
NO account entity. This graph therefore covers exactly five entity types over
EXISTING tables (no new tables, no graph database):

    USER  --OWNED_BY-->        TRANSACTION
    TRANSACTION --PROCESSED_BY--> MERCHANT
    TRANSACTION --VIA_SOURCE-->   GATEWAY   (PaymentEvent.source)
    TRANSACTION --REFERENCES-->   REFERENCE (PaymentEvent.reference_id)
    TRANSACTION --SHARED_REFERENCE--> REFERENCE (shared across distinct txs)
    TRANSACTION --SAME_MERCHANT-->   TRANSACTION (co-occurrence context)

Signals are explainable, structural/advisory observations only. They NEVER
touch the recovery policy, the safety gate, or any action path — the report
is evidence for staff eyeballs, nothing more. The signal vocabulary is
pinned as feature_version "relationship-v1".

All evidence fields are ids or counts only — never amounts, never any user
data beyond the ids already visible to staff roles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from api.db.models import PaymentEvent, Transaction

FEATURE_VERSION = "relationship-v1"

# signal codes (pinned vocabulary)
SIG_REPEATED_TXN = "repeated_transactions_same_merchant"
SIG_REPEATED_FAILED = "repeated_failed_transactions_same_merchant"
SIG_DUP_REFERENCE = "duplicate_reference"
SIG_BURST = "transaction_burst_user"
SIG_MERCHANT_FAILURE_RATE = "merchant_failure_rate_local"
SIG_SHARED_MERCHANT_USERS = "shared_merchant_users"

LEVEL_HIGH = "HIGH"
LEVEL_MEDIUM = "MEDIUM"
LEVEL_LOW = "LOW"
LEVEL_UNKNOWN = "UNKNOWN"

# failure outcomes counted by merchant_failure_rate_local (payment-lifecycle
# outcome constants)
_FAILURE_OUTCOMES = ("FAILED", "TIMEOUT", "ERROR")

_WINDOW_DAYS = 7
_BURST_WINDOW = timedelta(minutes=5)


def _as_utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes even for timezone=True columns; treat
    naive values as UTC so window math never mixes aware/naive."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class RelationshipSignal:
    code: str
    label: str
    description: str
    level: str
    count: int | None
    evidence: list[str] = field(default_factory=list)


@dataclass
class RelationshipReport:
    transaction_id: str
    entities: list[dict[str, str]]
    edges: list[dict[str, str]]
    signals: list[RelationshipSignal]
    computed_at: str
    feature_version: str = FEATURE_VERSION


def _signal(code: str, label: str, count: int | None, evidence: list[str],
            description: str, thresholds: list[tuple[int, str]],
            zero_level: str = LEVEL_UNKNOWN,
            zero_description: str | None = None) -> RelationshipSignal:
    """Shared threshold ladder: the first threshold whose minimum `count`
    satisfies wins; count 0 (or None) yields the zero level."""
    if count is None or count <= 0:
        return RelationshipSignal(
            code=code, label=label, description=(zero_description or description),
            level=zero_level, count=count, evidence=evidence,
        )
    for minimum, level in thresholds:
        if count >= minimum:
            return RelationshipSignal(
                code=code, label=label, description=description,
                level=level, count=count, evidence=evidence,
            )
    return RelationshipSignal(code=code, label=label, description=description,
                              level=LEVEL_UNKNOWN, count=count, evidence=evidence)


def _tx_reference_ids(db: Session, transaction_id: str) -> list[str]:
    """Distinct non-null reference_ids across this transaction's payment
    events (domain evidence, deterministic order)."""
    rows = (
        db.query(PaymentEvent.reference_id)
        .filter(
            PaymentEvent.transaction_id == transaction_id,
            PaymentEvent.reference_id.isnot(None),
        )
        .distinct()
        .order_by(PaymentEvent.reference_id)
        .all()
    )
    return [r[0] for r in rows if r[0]]


def compute_relationships(db: Session, transaction: Transaction) -> RelationshipReport:
    """Build the advisory relationship report for one transaction. Read-only
    over existing tables; performs a handful of indexed queries (no N+1 —
    every query is scoped by user/merchant/transaction_id)."""
    tx_ts = _as_utc(transaction.timestamp)
    window_start = tx_ts - timedelta(days=_WINDOW_DAYS)
    window_end = tx_ts + timedelta(days=_WINDOW_DAYS)

    user_id = transaction.user_id
    merchant_id = transaction.merchant_id
    tid = transaction.transaction_id

    # ---- entities & direct edges for THIS transaction -------------------
    entities: list[dict[str, str]] = [
        {"type": "USER", "id": user_id},
        {"type": "TRANSACTION", "id": tid},
        {"type": "MERCHANT", "id": merchant_id},
    ]
    edges: list[dict[str, str]] = [
        {"from_type": "USER", "from_id": user_id,
         "to_type": "TRANSACTION", "to_id": tid, "relation": "OWNED_BY"},
        {"from_type": "TRANSACTION", "from_id": tid,
         "to_type": "MERCHANT", "to_id": merchant_id, "relation": "PROCESSED_BY"},
    ]

    # gateway (source) entities/edges from this tx's payment events
    source_rows = (
        db.query(PaymentEvent.source)
        .filter(PaymentEvent.transaction_id == tid)
        .distinct()
        .order_by(PaymentEvent.source)
        .all()
    )
    for (source,) in source_rows:
        if not source:
            continue
        entities.append({"type": "GATEWAY", "id": source})
        edges.append({"from_type": "TRANSACTION", "from_id": tid,
                      "to_type": "GATEWAY", "to_id": source,
                      "relation": "VIA_SOURCE"})

    # reference entities/edges
    reference_ids = _tx_reference_ids(db, tid)
    for ref in reference_ids:
        entities.append({"type": "REFERENCE", "id": ref})
        edges.append({"from_type": "TRANSACTION", "from_id": tid,
                      "to_type": "REFERENCE", "to_id": ref,
                      "relation": "REFERENCES"})

    signals: list[RelationshipSignal] = []

    # ---- signal 1: repeated transactions, same merchant, 7d window ------
    same_merchant_ids = [
        row[0]
        for row in (
            db.query(Transaction.transaction_id)
            .filter(
                Transaction.user_id == user_id,
                Transaction.merchant_id == merchant_id,
                Transaction.transaction_id != tid,
                Transaction.timestamp >= window_start,
                Transaction.timestamp <= window_end,
            )
            .order_by(Transaction.timestamp)
            .all()
        )
    ]
    signals.append(_signal(
        code=SIG_REPEATED_TXN,
        label="Repeated transactions at the same merchant",
        count=len(same_merchant_ids),
        evidence=same_merchant_ids,
        description=(
            "This user has {n} other transaction(s) with this merchant within "
            "7 days of this transaction."
        ).format(n=len(same_merchant_ids)),
        thresholds=[(5, LEVEL_HIGH), (3, LEVEL_MEDIUM), (1, LEVEL_LOW)],
        zero_description=(
            "No other transactions with this merchant by this user within the "
            "7-day window."
        ),
    ))

    # ---- signal 2: repeated FAILED transactions, same merchant ----------
    same_merchant_failed_ids = [
        row[0]
        for row in (
            db.query(Transaction.transaction_id)
            .filter(
                Transaction.user_id == user_id,
                Transaction.merchant_id == merchant_id,
                Transaction.transaction_id != tid,
                Transaction.current_state == "FAILED",
                Transaction.timestamp >= window_start,
                Transaction.timestamp <= window_end,
            )
            .order_by(Transaction.timestamp)
            .all()
        )
    ]
    signals.append(_signal(
        code=SIG_REPEATED_FAILED,
        label="Repeated failed transactions at the same merchant",
        count=len(same_merchant_failed_ids),
        evidence=same_merchant_failed_ids,
        description=(
            "This user has {n} other FAILED transaction(s) with this merchant "
            "within 7 days of this transaction."
        ).format(n=len(same_merchant_failed_ids)),
        thresholds=[(3, LEVEL_HIGH), (2, LEVEL_MEDIUM), (1, LEVEL_LOW)],
        zero_description=(
            "No other failed transactions with this merchant by this user "
            "within the 7-day window."
        ),
    ))

    # ---- signal 3: duplicate reference (across ANY user) ----------------
    if reference_ids:
        dup_rows = (
            db.query(PaymentEvent.transaction_id)
            .filter(
                PaymentEvent.reference_id.in_(reference_ids),
                PaymentEvent.transaction_id != tid,
            )
            .distinct()
            .order_by(PaymentEvent.transaction_id)
            .all()
        )
        other_ref_txn_ids = [r[0] for r in dup_rows]
        dup_count = len(other_ref_txn_ids)
        if dup_count >= 1:
            level = LEVEL_HIGH
            description = (
                "Shared payment reference across distinct transactions: {n} "
                "other transaction(s) carry a reference id also seen on this "
                "transaction."
            ).format(n=dup_count)
        else:
            level = LEVEL_LOW
            description = (
                "This transaction has a payment reference id that is unique "
                "among all transactions."
            )
        signals.append(RelationshipSignal(
            code=SIG_DUP_REFERENCE, label="Duplicate payment reference",
            description=description, level=level,
            count=dup_count, evidence=other_ref_txn_ids,
        ))
    else:
        signals.append(RelationshipSignal(
            code=SIG_DUP_REFERENCE, label="Duplicate payment reference",
            description="No reference id on this transaction.",
            level=LEVEL_UNKNOWN, count=None, evidence=[],
        ))

    # ---- signal 4: transaction burst (user, ±5 minutes) -----------------
    burst_ids = [
        row[0]
        for row in (
            db.query(Transaction.transaction_id)
            .filter(
                Transaction.user_id == user_id,
                Transaction.transaction_id != tid,
                Transaction.timestamp >= tx_ts - _BURST_WINDOW,
                Transaction.timestamp <= tx_ts + _BURST_WINDOW,
            )
            .order_by(Transaction.timestamp)
            .all()
        )
    ]
    signals.append(_signal(
        code=SIG_BURST, label="Transaction burst by the same user",
        count=len(burst_ids), evidence=burst_ids,
        description=(
            "{n} other transaction(s) by this user fall within 5 minutes of "
            "this transaction."
        ).format(n=len(burst_ids)),
        thresholds=[(5, LEVEL_HIGH), (3, LEVEL_MEDIUM), (1, LEVEL_LOW)],
        zero_description=(
            "No other transactions by this user within 5 minutes of this one."
        ),
    ))

    # ---- signal 5: local merchant failure rate (event-level, 7d) --------
    merchant_events = (
        db.query(PaymentEvent.status)
        .join(Transaction, PaymentEvent.transaction_id == Transaction.transaction_id)
        .filter(
            Transaction.merchant_id == merchant_id,
            PaymentEvent.event_timestamp >= window_start,
            PaymentEvent.event_timestamp <= window_end,
        )
        .all()
    )
    total_events = len(merchant_events)
    if total_events < 5:
        signals.append(RelationshipSignal(
            code=SIG_MERCHANT_FAILURE_RATE,
            label="Local merchant failure rate",
            description=(
                "Only {n} payment event(s) for this merchant in the 7-day "
                "window — not enough to estimate a failure rate."
            ).format(n=total_events),
            level=LEVEL_UNKNOWN, count=None, evidence=[],
        ))
    else:
        failed_events = sum(
            1 for (status,) in merchant_events if status in _FAILURE_OUTCOMES
        )
        rate = failed_events / total_events
        if rate >= 0.5:
            level = LEVEL_HIGH
        elif rate >= 0.25:
            level = LEVEL_MEDIUM
        else:
            level = LEVEL_LOW
        signals.append(RelationshipSignal(
            code=SIG_MERCHANT_FAILURE_RATE,
            label="Local merchant failure rate",
            description=(
                "{failed} of {total} payment events for this merchant in the "
                "7-day window are FAILED/TIMEOUT/ERROR (rate {rate:.0%})."
            ).format(failed=failed_events, total=total_events, rate=rate),
            level=level, count=failed_events,
            evidence=[],
        ))

    # ---- signal 6: distinct other users at the same merchant (7d) -------
    shared_user_rows = (
        db.query(Transaction.user_id)
        .filter(
            Transaction.merchant_id == merchant_id,
            Transaction.user_id != user_id,
            Transaction.timestamp >= window_start,
            Transaction.timestamp <= window_end,
        )
        .distinct()
        .order_by(Transaction.user_id)
        .all()
    )
    shared_user_ids = [r[0] for r in shared_user_rows]
    shared_count = len(shared_user_ids)
    if shared_count >= 10:
        level = LEVEL_MEDIUM
        description = (
            "{n} distinct other user(s) also transacted with this merchant "
            "within 7 days — this is a structural co-occurrence observation "
            "about the merchant's customer base, not a statement about any "
            "individual."
        ).format(n=shared_count)
    else:
        level = LEVEL_LOW if shared_count > 0 else LEVEL_UNKNOWN
        description = (
            "{n} distinct other user(s) transacted with this merchant within "
            "7 days."
        ).format(n=shared_count)
    signals.append(RelationshipSignal(
        code=SIG_SHARED_MERCHANT_USERS,
        label="Shared merchant activity (co-occurrence)",
        description=description, level=level,
        count=shared_count, evidence=shared_user_ids,
    ))

    # ---- SAME_MERCHANT edges (context edges for the closest neighbours) --
    for other_tid in same_merchant_ids[:10]:
        edges.append({"from_type": "TRANSACTION", "from_id": tid,
                      "to_type": "TRANSACTION", "to_id": other_tid,
                      "relation": "SAME_MERCHANT"})

    return RelationshipReport(
        transaction_id=tid,
        entities=entities,
        edges=edges,
        signals=signals,
        computed_at=datetime.now(timezone.utc).isoformat(),
        feature_version=FEATURE_VERSION,
    )

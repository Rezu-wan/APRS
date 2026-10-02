"""Stage 11 Phase C — behavioral-signal tests.

Covers the z-score math (HIGH / borderline MEDIUM / insufficient-history
UNKNOWN), burst and unusual-timing rules, gateway failure-rate thresholds
and honest UNKNOWN, report invariants (unique codes, level enum, overall
aggregation, feature_version), determinism, the staff-gated API (ADMIN/SUPPORT
200, CUSTOMER 403, unknown tx 404), the MODEL_SIGNAL audit row, and the
read-only guarantee (no Transaction/PaymentEvent/RiskAssessment writes).

Each test uses its own USER-BHV-* id (and unique merchant where relevant) so
the tests stay order-independent against the shared session-scoped DB.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from api.db.database import SessionLocal
from api.db.models import (
    PaymentEvent,
    RiskAssessmentRecord,
    SecurityAuditRecord,
    Transaction,
)
from api.services.behavioral import compute_behavioral_signals

from tests.conftest import ADMIN_KEY, CUSTOMER_KEY, SUPPORT_KEY, SYSTEM_KEY

SIGNALS_URL = "/api/v1/transactions/{tid}/behavioral-signals"

_NOW = datetime.now(timezone.utc)


def _seed_tx(
    *,
    transaction_id: str,
    user_id: str,
    merchant_id: str = "MERCHANT-BHV",
    amount: float = 100.0,
    latency_ms: int = 50,
    retry_count: int = 0,
    timestamp: datetime | None = None,
    state: str = "SUCCESS",
) -> Transaction:
    tx = Transaction(
        transaction_id=transaction_id,
        user_id=user_id,
        merchant_id=merchant_id,
        amount=amount,  # SQLAlchemy coerces to Numeric
        gateway_latency_ms=latency_ms,
        retry_count=retry_count,
        timestamp=timestamp or _NOW,
        current_state=state,
    )
    return tx


def _commit_rows(rows: list) -> None:
    db = SessionLocal()
    try:
        db.add_all(rows)
        db.commit()
    finally:
        db.close()


def _report(tid: str):
    db = SessionLocal()
    try:
        tx = (
            db.query(Transaction)
            .filter(Transaction.transaction_id == tid)
            .one()
        )
        return compute_behavioral_signals(db, tx)
    finally:
        db.close()


def _signal(report, code: str):
    return next(s for s in report.signals if s.code == code)


def _seed_gateway_events(
    merchant_id: str, statuses: list[str], base_time: datetime
) -> None:
    """One transaction per status, each carrying one GATEWAY event."""
    rows: list = []
    for i, status in enumerate(statuses):
        tid = f"BHV-GE-{merchant_id}-{i}"
        rows.append(
            _seed_tx(
                transaction_id=tid,
                user_id=f"USER-BHV-GE-{merchant_id}",
                merchant_id=merchant_id,
                timestamp=base_time - timedelta(hours=1),
            )
        )
        rows.append(
            PaymentEvent(
                transaction_id=tid,
                provider_event_id=f"pevt-{merchant_id}-{i}",
                event_type="GATEWAY_RESPONSE_RECEIVED"
                if status == "CONFIRMED"
                else "GATEWAY_TIMEOUT",
                source="GATEWAY",
                status=status,
                event_timestamp=base_time - timedelta(minutes=30),
            )
        )
    _commit_rows(rows)


# ---------------------------------------------------------------------------
# z-score math
# ---------------------------------------------------------------------------


def test_amount_deviation_high_medium_unknown(client):
    # baseline: 4x100 + 1x200 -> mean 120, sample std ~44.72
    base = _NOW - timedelta(days=2)
    user = "USER-BHV-AMT"
    priors = [
        _seed_tx(
            transaction_id=f"BHV-AMT-P{i}",
            user_id=user,
            amount=100.0 if i < 4 else 200.0,
            timestamp=base - timedelta(hours=i + 1),
        )
        for i in range(5)
    ]
    # queried tx = 300 -> z ~ +4.02 -> HIGH
    priors.append(
        _seed_tx(
            transaction_id="BHV-AMT-HIGH",
            user_id=user,
            amount=300.0,
            timestamp=_NOW,
        )
    )
    # borderline: 220 -> z ~ +2.24 -> MEDIUM (own user, same baseline shape)
    user2 = "USER-BHV-AMT2"
    priors += [
        _seed_tx(
            transaction_id=f"BHV-AMT2-P{i}",
            user_id=user2,
            amount=100.0 if i < 4 else 200.0,
            timestamp=base - timedelta(hours=i + 1),
        )
        for i in range(5)
    ]
    priors.append(
        _seed_tx(
            transaction_id="BHV-AMT-MED",
            user_id=user2,
            amount=220.0,
            timestamp=_NOW,
        )
    )
    # insufficient history: < 5 priors -> UNKNOWN
    user3 = "USER-BHV-AMT3"
    priors += [
        _seed_tx(
            transaction_id=f"BHV-AMT3-P{i}",
            user_id=user3,
            amount=100.0,
            timestamp=base - timedelta(hours=i + 1),
        )
        for i in range(2)
    ]
    priors.append(
        _seed_tx(
            transaction_id="BHV-AMT-UNK",
            user_id=user3,
            amount=999.0,
            timestamp=_NOW,
        )
    )
    _commit_rows(priors)

    high = _signal(_report("BHV-AMT-HIGH"), "amount_deviation")
    assert high.level == "HIGH"
    assert high.basis == "z_score"
    assert high.window == "all_history"

    med = _signal(_report("BHV-AMT-MED"), "amount_deviation")
    assert med.level == "MEDIUM"
    assert 2.0 <= abs(med.value) < 3.0

    unk = _signal(_report("BHV-AMT-UNK"), "amount_deviation")
    assert unk.level == "UNKNOWN"
    assert unk.value is None
    assert "insufficient history" in unk.description


def test_retry_frequency_zero_variance_history(client):
    user = "USER-BHV-RETRY"
    base = _NOW - timedelta(days=2)
    rows = [
        _seed_tx(
            transaction_id=f"BHV-RETRY-P{i}",
            user_id=user,
            retry_count=0,
            timestamp=base - timedelta(hours=i + 1),
        )
        for i in range(6)
    ]
    rows.append(
        _seed_tx(
            transaction_id="BHV-RETRY-Q",
            user_id=user,
            retry_count=5,
            timestamp=_NOW,
        )
    )
    _commit_rows(rows)
    sig = _signal(_report("BHV-RETRY-Q"), "retry_frequency")
    # zero-variance baseline with a different value -> maximally unusual
    assert sig.level == "HIGH"
    # and an equal value -> z 0 -> LOW
    rows.append(
        _seed_tx(
            transaction_id="BHV-RETRY-Q2",
            user_id=user,
            retry_count=0,
            timestamp=_NOW,
        )
    )
    _commit_rows([rows[-1]])
    sig2 = _signal(_report("BHV-RETRY-Q2"), "retry_frequency")
    # baseline now has variance (Q's retry=5 is in it), z near 0 -> LOW
    assert sig2.level == "LOW"
    assert sig2.value is not None and abs(sig2.value) < 1.0


# ---------------------------------------------------------------------------
# count rules
# ---------------------------------------------------------------------------


def test_burst_high_and_low(client):
    near = _NOW - timedelta(minutes=1)
    user = "USER-BHV-BURST"
    rows = [
        _seed_tx(
            transaction_id=f"BHV-BURST-P{i}",
            user_id=user,
            timestamp=near,
        )
        for i in range(5)
    ]
    rows.append(
        _seed_tx(
            transaction_id="BHV-BURST-Q",
            user_id=user,
            timestamp=_NOW,
        )
    )
    _commit_rows(rows)
    assert _signal(_report("BHV-BURST-Q"), "transaction_burst").level == "HIGH"

    # a different user with exactly 1 neighbor -> LOW
    user2 = "USER-BHV-BURST2"
    rows2 = [
        _seed_tx(
            transaction_id="BHV-BURST2-P",
            user_id=user2,
            timestamp=near,
        ),
        _seed_tx(
            transaction_id="BHV-BURST2-Q",
            user_id=user2,
            timestamp=_NOW,
        ),
    ]
    _commit_rows(rows2)
    assert _signal(_report("BHV-BURST2-Q"), "transaction_burst").level == "LOW"


def test_unusual_timing_medium_when_first_in_hour(client):
    user = "USER-BHV-HOUR"
    # anchor 5 days ago at midnight: priors at hour 10 on days -5..-1,
    # queried tx at hour 23 one day after the last prior — strictly latest.
    anchor = (_NOW.replace(hour=0, minute=0, second=0, microsecond=0)) - timedelta(
        days=5
    )
    rows = [
        _seed_tx(
            transaction_id=f"BHV-HOUR-P{i}",
            user_id=user,
            timestamp=anchor + timedelta(days=i, hours=10),
        )
        for i in range(5)
    ]
    rows.append(
        _seed_tx(
            transaction_id="BHV-HOUR-Q",
            user_id=user,
            timestamp=anchor + timedelta(days=4, hours=23),
        )
    )
    _commit_rows(rows)
    sig = _signal(_report("BHV-HOUR-Q"), "unusual_timing")
    assert sig.level == "MEDIUM"
    assert "first activity in this hour bucket" in sig.description


def test_frequency_and_recent_failures(client):
    base = _NOW - timedelta(hours=1)
    user = "USER-BHV-FREQ"
    rows = [
        _seed_tx(
            transaction_id=f"BHV-FREQ-P{i}",
            user_id=user,
            timestamp=base,
            state="FAILED",
        )
        for i in range(6)
    ]
    rows.append(
        _seed_tx(
            transaction_id="BHV-FREQ-Q",
            user_id=user,
            timestamp=_NOW,
            state="FAILED",
        )
    )
    _commit_rows(rows)
    report = _report("BHV-FREQ-Q")
    freq = _signal(report, "transaction_frequency")
    assert freq.value == 7.0  # 6 priors + this tx inside the 24h window
    assert freq.level == "MEDIUM"  # 7 is >= 5 and < 10
    fails = _signal(report, "recent_failure_count")
    assert fails.level == "HIGH"  # 7 FAILED in window >= 3


# ---------------------------------------------------------------------------
# rate signals
# ---------------------------------------------------------------------------


def test_gateway_failure_rate_high_and_unknown(client):
    # Use a 30-days-past window so ONLY our seeded events fall inside it —
    # the platform-wide rate must not depend on demo-corpus events near now.
    old = _NOW - timedelta(days=30)
    # platform-wide: 6 gateway events, 4 FAILED / 2 CONFIRMED -> 0.67 HIGH
    _seed_gateway_events(
        "MERCHANT-BHV-GW1",
        ["FAILED", "FAILED", "FAILED", "FAILED", "CONFIRMED", "CONFIRMED"],
        old,
    )
    _commit_rows(
        [
            _seed_tx(
                transaction_id="BHV-GW-Q",
                user_id="USER-BHV-GW",
                merchant_id="MERCHANT-BHV-CLEAN",
                timestamp=old,
            )
        ]
    )
    sig = _signal(_report("BHV-GW-Q"), "gateway_failure_rate")
    assert sig.level == "HIGH"
    assert abs(sig.value - 4 / 6) < 1e-3
    assert sig.window == "7d"
    assert sig.basis == "rate"
    assert "this user" in sig.description

    # merchant with the same failing events -> HIGH too
    _commit_rows(
        [
            _seed_tx(
                transaction_id="BHV-GW-MERCH-Q",
                user_id="USER-BHV-GW-MERCH",
                merchant_id="MERCHANT-BHV-GW1",
                timestamp=old,
            )
        ]
    )
    merch = _signal(_report("BHV-GW-MERCH-Q"), "merchant_failure_rate")
    assert merch.level == "HIGH"

    # no gateway events in the 7d window (timestamp 60d back) -> UNKNOWN
    older = _NOW - timedelta(days=60)
    _commit_rows(
        [
            _seed_tx(
                transaction_id="BHV-GW-OLD",
                user_id="USER-BHV-OLD",
                timestamp=older,
            )
        ]
    )
    unk = _signal(_report("BHV-GW-OLD"), "gateway_failure_rate")
    assert unk.level == "UNKNOWN"
    assert unk.value is None
    assert "required for a rate" in unk.description


# ---------------------------------------------------------------------------
# report invariants + determinism
# ---------------------------------------------------------------------------

EXPECTED_CODES = {
    "transaction_frequency",
    "retry_frequency",
    "amount_deviation",
    "recent_failure_count",
    "latency_deviation",
    "gateway_failure_rate",
    "merchant_failure_rate",
    "unusual_timing",
    "transaction_burst",
}
VALID_LEVELS = {"LOW", "MEDIUM", "HIGH", "UNKNOWN"}


def test_report_invariants_and_determinism(client):
    report = _report("BHV-FREQ-Q")  # seeded by the frequency test above

    codes = [s.code for s in report.signals]
    assert len(codes) == len(set(codes)) == 9
    assert set(codes) == EXPECTED_CODES
    for s in report.signals:
        assert s.level in VALID_LEVELS
        assert s.basis in {"z_score", "ewma", "count_rule", "rate"}
        assert s.window
        assert s.description

    summary = report.summary
    assert summary["high_count"] == sum(
        1 for s in report.signals if s.level == "HIGH"
    )
    assert summary["medium_count"] == sum(
        1 for s in report.signals if s.level == "MEDIUM"
    )
    assert summary["unknown_count"] == sum(
        1 for s in report.signals if s.level == "UNKNOWN"
    )
    levels = {s.level for s in report.signals}
    if "HIGH" in levels:
        assert summary["overall"] == "HIGH"
    elif "MEDIUM" in levels:
        assert summary["overall"] == "MEDIUM"

    assert report.feature_version == "behavioral-v1"
    assert report.transaction_id == "BHV-FREQ-Q"

    # determinism: same DB state -> identical payload except computed_at
    again = _report("BHV-FREQ-Q")
    a, b = report.model_dump(), again.model_dump()
    a.pop("computed_at"), b.pop("computed_at")
    assert a == b


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


def _db_count(model, **filters) -> int:
    db = SessionLocal()
    try:
        return db.query(model).filter_by(**filters).count()
    finally:
        db.close()


def test_api_roles_and_404(client):
    url = SIGNALS_URL.format(tid="BHV-GW-Q")
    assert client.get(url, headers=SYSTEM_KEY).status_code == 200
    assert client.get(url, headers=ADMIN_KEY).status_code == 200
    assert client.get(url, headers=SUPPORT_KEY).status_code == 200

    resp = client.get(url, headers=CUSTOMER_KEY)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "INSUFFICIENT_PERMISSIONS"

    resp = client.get(SIGNALS_URL.format(tid="BHV-DOES-NOT-EXIST"), headers=ADMIN_KEY)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_api_admin_response_shape(client):
    resp = client.get(SIGNALS_URL.format(tid="BHV-GW-Q"), headers=ADMIN_KEY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["transaction_id"] == "BHV-GW-Q"
    assert body["feature_version"] == "behavioral-v1"
    assert {"high_count", "medium_count", "unknown_count", "overall"} <= set(
        body["summary"]
    )
    assert len(body["signals"]) == 9
    assert set(body["signals"][0]) == {
        "code",
        "label",
        "value",
        "unit",
        "level",
        "description",
        "window",
        "basis",
    }


def test_audit_model_signal_row_written(client):
    url = SIGNALS_URL.format(tid="BHV-GW-Q")
    before = _db_count(SecurityAuditRecord, action="MODEL_SIGNAL")
    resp = client.get(url, headers=ADMIN_KEY)
    assert resp.status_code == 200
    assert _db_count(SecurityAuditRecord, action="MODEL_SIGNAL") >= before + 1


def test_endpoint_is_read_only(client):
    before = (
        _db_count(Transaction),
        _db_count(PaymentEvent),
        _db_count(RiskAssessmentRecord),
    )
    resp = client.get(SIGNALS_URL.format(tid="BHV-GW-Q"), headers=ADMIN_KEY)
    assert resp.status_code == 200
    after = (
        _db_count(Transaction),
        _db_count(PaymentEvent),
        _db_count(RiskAssessmentRecord),
    )
    assert before == after

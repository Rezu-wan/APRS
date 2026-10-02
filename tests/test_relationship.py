"""
Stage 11 Phase 11D — transaction relationship graph tests.

The router is registered by the integrator in api.main; this module
registers it idempotently itself so the tests are self-contained during
parallel development. All seeding is direct model insertion (SQLite test
db); Decimal amounts; tz-aware UTC timestamps.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from api.db.database import SessionLocal
from api.db.models import PaymentEvent, Transaction
from api.main import app
from api.routes import relationship as relationship_route
from api.services.audit import AUDIT_GRAPH_ANALYSIS

from tests.conftest import ADMIN_KEY, CUSTOMER_KEY, SUPPORT_KEY

URL = "/api/v1/transactions/{tid}/relationships"
LEVELS = {"LOW", "MEDIUM", "HIGH", "UNKNOWN"}
FEATURE_VERSION = "relationship-v1"


@pytest.fixture(scope="module", autouse=True)
def _register_router():
    """Idempotent registration so the tests pass before/after integration."""
    for route in app.routes:
        if getattr(route, "path", None) == URL:
            break
    else:
        app.include_router(relationship_route.router)
    yield


_seq = 0


def _next_id(prefix: str) -> str:
    global _seq
    _seq += 1
    return f"{prefix}-{uuid.uuid4().hex[:8]}-{_seq}"


def _seed_tx(
    tid: str,
    user_id: str,
    merchant_id: str,
    ts: datetime,
    state: str = "CONFIRMED",
    amount: str = "100.00",
) -> Transaction:
    db = SessionLocal()
    try:
        tx = Transaction(
            transaction_id=tid,
            user_id=user_id,
            merchant_id=merchant_id,
            amount=Decimal(amount),
            currency="BDT",
            timestamp=ts,
            current_state=state,
        )
        db.add(tx)
        db.commit()
        return tx
    finally:
        db.close()


def _seed_event(
    tid: str,
    ts: datetime,
    status: str = "CONFIRMED",
    source: str = "GATEWAY",
    reference_id: str | None = None,
) -> None:
    db = SessionLocal()
    try:
        db.add(
            PaymentEvent(
                transaction_id=tid,
                provider_event_id=_next_id("PEV"),
                event_type="GATEWAY_RESPONSE_RECEIVED",
                source=source,
                status=status,
                event_timestamp=ts,
                reference_id=reference_id,
            )
        )
        db.commit()
    finally:
        db.close()


def _signal(report: dict, code: str) -> dict:
    matches = [s for s in report["signals"] if s["code"] == code]
    assert len(matches) == 1
    return matches[0]


def _counts(db):
    tx = db.query(Transaction).count()
    pe = db.query(PaymentEvent).count()
    return tx, pe


# ---------------------------------------------------------------- invariants


def test_report_invariants(client):
    tid = _next_id("TXN-INV")
    ts = datetime.now(timezone.utc)
    _seed_tx(tid, "U-INV", "M-INV", ts)
    _seed_event(tid, ts, reference_id="REF-INV")

    resp = client.get(URL.format(tid=tid), headers=ADMIN_KEY)
    assert resp.status_code == 200
    report = resp.json()["report"]

    assert report["transaction_id"] == tid
    assert report["feature_version"] == FEATURE_VERSION

    # signal vocabulary: six pinned codes, unique
    codes = [s["code"] for s in report["signals"]]
    assert len(codes) == len(set(codes)) == 6
    for s in report["signals"]:
        assert s["level"] in LEVELS
        assert s["evidence"] == [str(e) for e in s["evidence"]] or all(
            isinstance(e, str) for e in s["evidence"]
        )
        # evidence carries NO amounts: every item is an id string, never a
        # number/Decimal
        assert all(not isinstance(e, (int, float)) for e in s["evidence"])

    # entities/edges contain this transaction
    assert {"type": "TRANSACTION", "id": tid} in [
        {"type": e["type"], "id": e["id"]} for e in report["entities"]
    ]
    assert any(
        e["relation"] == "OWNED_BY" and e["to_id"] == tid
        for e in report["edges"]
    )
    assert any(
        e["relation"] == "PROCESSED_BY" and e["from_id"] == tid
        for e in report["edges"]
    )
    assert any(
        e["relation"] == "VIA_SOURCE" and e["from_id"] == tid
        for e in report["edges"]
    )
    assert any(
        e["relation"] == "REFERENCES" and e["to_id"] == "REF-INV"
        for e in report["edges"]
    )


def test_signal_codes_pinned(client):
    tid = _next_id("TXN-CODES")
    _seed_tx(tid, "U-C", "M-C", datetime.now(timezone.utc))
    resp = client.get(URL.format(tid=tid), headers=ADMIN_KEY)
    codes = {s["code"] for s in resp.json()["report"]["signals"]}
    assert codes == {
        "repeated_transactions_same_merchant",
        "repeated_failed_transactions_same_merchant",
        "duplicate_reference",
        "transaction_burst_user",
        "merchant_failure_rate_local",
        "shared_merchant_users",
    }


# ---------------------------------------------------------- duplicate_reference


def test_duplicate_reference_high(client):
    ts = datetime.now(timezone.utc)
    t1 = _next_id("TXN-DUP1")
    t2 = _next_id("TXN-DUP2")
    _seed_tx(t1, "U-DUP", "M-DUP", ts)
    _seed_tx(t2, "U-DUP2", "M-DUP", ts + timedelta(minutes=1))
    _seed_event(t1, ts, reference_id="REF-X")
    _seed_event(t2, ts, reference_id="REF-X")

    sig = _signal(
        client.get(URL.format(tid=t1), headers=ADMIN_KEY).json()["report"],
        "duplicate_reference",
    )
    assert sig["level"] == "HIGH"
    assert sig["count"] >= 1
    assert t2 in sig["evidence"]
    assert "shared payment reference across distinct transactions" in (
        sig["description"].lower()
    )


def test_no_reference_unknown(client):
    tid = _next_id("TXN-NOREF")
    _seed_tx(tid, "U-NOREF", "M-NOREF", datetime.now(timezone.utc))
    _seed_event(tid, datetime.now(timezone.utc), reference_id=None)

    sig = _signal(
        client.get(URL.format(tid=tid), headers=ADMIN_KEY).json()["report"],
        "duplicate_reference",
    )
    assert sig["level"] == "UNKNOWN"
    assert sig["count"] is None
    assert "no reference id" in sig["description"].lower()


# ------------------------------------------------------------------- burst


def test_burst_high(client):
    ts = datetime.now(timezone.utc)
    tid = _next_id("TXN-BURST0")
    _seed_tx(tid, "U-BURST", "M-BURST", ts)
    for i in range(1, 6):  # 5 other txs within 3 minutes
        _seed_tx(
            _next_id(f"TXN-BURST{i}"), "U-BURST", "M-BURST",
            ts + timedelta(seconds=30 * i),
        )

    sig = _signal(
        client.get(URL.format(tid=tid), headers=ADMIN_KEY).json()["report"],
        "transaction_burst_user",
    )
    assert sig["level"] == "HIGH"
    assert sig["count"] == 5


def test_burst_spread_low_or_unknown(client):
    ts = datetime.now(timezone.utc)
    tid = _next_id("TXN-SPREAD")
    _seed_tx(tid, "U-SPREAD", "M-SPREAD", ts)
    for i in range(1, 6):  # hours apart — outside the ±5-minute window
        _seed_tx(
            _next_id(f"TXN-SPREAD{i}"), "U-SPREAD", "M-SPREAD",
            ts + timedelta(hours=i),
        )

    sig = _signal(
        client.get(URL.format(tid=tid), headers=ADMIN_KEY).json()["report"],
        "transaction_burst_user",
    )
    assert sig["level"] in {"LOW", "UNKNOWN"}
    assert sig["count"] == 0
    assert sig["evidence"] == []


# ------------------------------------------------------- repeated failures


def test_repeated_failed_merchant_medium_and_high(client):
    ts = datetime.now(timezone.utc)
    t1 = _next_id("TXN-RF1")
    _seed_tx(t1, "U-RF", "M-RF", ts, state="FAILED")
    _seed_tx(_next_id("TXN-RF2"), "U-RF", "M-RF", ts + timedelta(hours=1),
             state="FAILED")
    t3 = _next_id("TXN-RF3")
    _seed_tx(t3, "U-RF", "M-RF", ts + timedelta(hours=2), state="FAILED")

    sig = _signal(
        client.get(URL.format(tid=t1), headers=ADMIN_KEY).json()["report"],
        "repeated_failed_transactions_same_merchant",
    )
    assert sig["count"] == 2
    assert sig["level"] == "MEDIUM"
    assert t3 in sig["evidence"]

    # a fourth failure pushes it to HIGH (>=3 other failed txs)
    t5 = _next_id("TXN-RF5")
    _seed_tx(t5, "U-RF", "M-RF", ts + timedelta(hours=3), state="FAILED")
    sig2 = _signal(
        client.get(URL.format(tid=t1), headers=ADMIN_KEY).json()["report"],
        "repeated_failed_transactions_same_merchant",
    )
    assert sig2["count"] == 3
    assert sig2["level"] == "HIGH"


# ------------------------------------------------ merchant_failure_rate_local


def test_merchant_failure_rate_high(client):
    ts = datetime.now(timezone.utc)
    merchant = "M-FAILRATE"
    # this tx + 5 other txs on the same merchant, mostly failing events
    tids = [_next_id(f"TXN-MFR{i}") for i in range(6)]
    for i, tid in enumerate(tids):
        _seed_tx(tid, f"U-MFR{i}", merchant, ts + timedelta(hours=i),
                 state="FAILED")
        # 2 events per tx: one CONFIRMED, one FAILED (12 total, 6 failed
        # -> 0.5 >= HIGH threshold)
        _seed_event(tid, ts, status="CONFIRMED")
        _seed_event(tid, ts, status="FAILED")

    sig = _signal(
        client.get(URL.format(tid=tids[0]), headers=ADMIN_KEY).json()["report"],
        "merchant_failure_rate_local",
    )
    assert sig["level"] == "HIGH"
    assert sig["count"] == 6


def test_merchant_failure_rate_sparse_unknown(client):
    tid = _next_id("TXN-MFR-SPARSE")
    ts = datetime.now(timezone.utc)
    _seed_tx(tid, "U-SPARSE", "M-SPARSE", ts)
    _seed_event(tid, ts, status="FAILED")  # 1 event < 5 minimum

    sig = _signal(
        client.get(URL.format(tid=tid), headers=ADMIN_KEY).json()["report"],
        "merchant_failure_rate_local",
    )
    assert sig["level"] == "UNKNOWN"
    assert sig["count"] is None


# ---------------------------------------------------- shared_merchant_users


def test_shared_merchant_users_neutral_count(client):
    ts = datetime.now(timezone.utc)
    tid = _next_id("TXN-SMU")
    _seed_tx(tid, "U-SMU1", "M-SMU", ts)
    _seed_tx(_next_id("TXN-SMU-U2"), "U-SMU2", "M-SMU", ts)
    _seed_tx(_next_id("TXN-SMU-U3"), "U-SMU3", "M-SMU", ts)

    sig = _signal(
        client.get(URL.format(tid=tid), headers=ADMIN_KEY).json()["report"],
        "shared_merchant_users",
    )
    assert sig["count"] == 2  # U-SMU2 and U-SMU3 (others, not self)
    assert set(sig["evidence"]) == {"U-SMU2", "U-SMU3"}
    # neutral/structural wording — no fraud language about persons
    text = sig["description"].lower()
    assert "fraud" not in text
    assert "suspicious" not in text
    assert "structural co-occurrence" in text or "transacted" in text


# --------------------------------------------------------------- API layer


def test_api_roles_and_404(client):
    tid = _next_id("TXN-ROLES")
    _seed_tx(tid, "U-ROLES", "M-ROLES", datetime.now(timezone.utc))

    for key in (ADMIN_KEY, SUPPORT_KEY):
        resp = client.get(URL.format(tid=tid), headers=key)
        assert resp.status_code == 200, (key, resp.text)

    assert client.get(URL.format(tid=tid), headers=CUSTOMER_KEY).status_code == 403
    assert client.get(
        URL.format(tid="TXN-DOES-NOT-EXIST"), headers=ADMIN_KEY
    ).status_code == 404


def test_api_audit_row_and_read_only(client):
    tid = _next_id("TXN-AUDIT")
    _seed_tx(tid, "U-AUDIT", "M-AUDIT", datetime.now(timezone.utc))
    _seed_event(tid, datetime.now(timezone.utc))

    from api.db.models import SecurityAuditRecord

    db = SessionLocal()
    try:
        tx_before, pe_before = _counts(db)
    finally:
        db.close()

    resp = client.get(URL.format(tid=tid), headers=ADMIN_KEY)
    assert resp.status_code == 200

    db = SessionLocal()
    try:
        tx_after, pe_after = _counts(db)
        audit_rows = (
            db.query(SecurityAuditRecord)
            .filter(
                SecurityAuditRecord.action == AUDIT_GRAPH_ANALYSIS,
                SecurityAuditRecord.resource_id == tid,
            )
            .count()
        )
    finally:
        db.close()

    assert audit_rows >= 1
    assert (tx_after, pe_after) == (tx_before, pe_before)  # read-only

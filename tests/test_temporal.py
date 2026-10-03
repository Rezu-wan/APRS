"""
Stage 11 Phase 11B — Temporal Digital Twin tests (spec §29: S11-3, S11-8).

The router is registered by the integrator in api.main; this module
registers it idempotently itself so the tests are self-contained during
parallel development. Seeding is direct model insertion (SQLite test db);
Decimal amounts; tz-aware UTC timestamps.

The critical acceptance property is future isolation (S11-3): a query at
time T must use ONLY events with event_timestamp <= T — a late settlement
event must never make a historical view look confirmed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from api.db.database import SessionLocal
from api.db.models import DigitalTwinEvent, PaymentEvent, Transaction
from api.main import app
from api.routes import temporal as temporal_route
from api.services.audit import AUDIT_TEMPORAL_QUERY
from api.services.event_reconstruction import reconstruct_from_events
from api.services.temporal import state_at

from tests.conftest import ADMIN_KEY, CUSTOMER_KEY, SUPPORT_KEY

URL = "/api/v1/transactions/{tid}/state-at"

T0 = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
T_LATE = T0 + timedelta(minutes=60)  # late SETTLEMENT_CONFIRMED
T_MID = T0 + timedelta(minutes=30)  # between timeout and late settlement

# merchant-timeout chain (fixed timestamps): debit ok, gateway ok,
# merchant confirmation times out — settlement evidence arrives LATE.
CHAIN = [
    ("CUSTOMER_DEBIT_CONFIRMED", T0, "CONFIRMED", "BANK"),
    ("GATEWAY_REQUEST_SENT", T0 + timedelta(minutes=1), "PROGRESS", "GATEWAY"),
    ("GATEWAY_RESPONSE_RECEIVED", T0 + timedelta(minutes=2), "CONFIRMED", "GATEWAY"),
    ("MERCHANT_CONFIRMATION_REQUESTED", T0 + timedelta(minutes=3), "PROGRESS", "MERCHANT"),
    ("MERCHANT_CONFIRMATION_TIMEOUT", T0 + timedelta(minutes=10), "TIMEOUT", "MERCHANT"),
]
LATE_EVENT = ("SETTLEMENT_CONFIRMED", T_LATE, "CONFIRMED", "SETTLEMENT")


@pytest.fixture(scope="module", autouse=True)
def _register_router():
    """Idempotent registration so the tests pass before/after integration."""
    for route in app.routes:
        if getattr(route, "path", None) == URL:
            break
    else:
        app.include_router(temporal_route.router)
    yield


@pytest.fixture(autouse=True)
def _schema(schema):
    """Direct SessionLocal seeding needs tables to exist even in tests that
    don't use the client fixture."""


_seq = 0


def _next_id(prefix: str) -> str:
    global _seq
    _seq += 1
    return f"{prefix}-{uuid.uuid4().hex[:8]}-{_seq}"


def _seed_tx(tid: str) -> Transaction:
    db = SessionLocal()
    try:
        tx = Transaction(
            transaction_id=tid,
            user_id=f"U-{tid[-6:]}",
            merchant_id=f"M-{tid[-6:]}",
            amount=Decimal("250.00"),
            currency="BDT",
            timestamp=T0,
            current_state="FAILED",  # mutable current state — must NOT leak
        )
        db.add(tx)
        db.commit()
        return tx
    finally:
        db.close()


def _seed_payment_event(tid: str, event_type: str, ts: datetime, status: str,
                        source: str) -> None:
    db = SessionLocal()
    try:
        db.add(
            PaymentEvent(
                transaction_id=tid,
                provider_event_id=_next_id("PEV"),
                event_type=event_type,
                source=source,
                status=status,
                event_timestamp=ts,
            )
        )
        db.commit()
    finally:
        db.close()


def _seed_chain(tid: str, with_late_settlement: bool = True) -> None:
    for event_type, ts, status, source in CHAIN:
        _seed_payment_event(tid, event_type, ts, status, source)
    if with_late_settlement:
        event_type, ts, status, source = LATE_EVENT
        _seed_payment_event(tid, event_type, ts, status, source)


def _seed_twin_event(tid: str, ts: datetime, new_state: str,
                     event_type: str | None = None) -> None:
    db = SessionLocal()
    try:
        db.add(
            DigitalTwinEvent(
                transaction_id=tid,
                timestamp=ts,
                event_type=event_type or f"STATE_{new_state}",
                previous_state=None,
                new_state=new_state,
            )
        )
        db.commit()
    finally:
        db.close()


def _state_at(tid: str, at: datetime):
    db = SessionLocal()
    try:
        tx = db.query(Transaction).filter_by(transaction_id=tid).one()
        return state_at(db, tx, at)
    finally:
        db.close()


def _counts(db):
    return (
        db.query(Transaction).count(),
        db.query(PaymentEvent).count(),
        db.query(DigitalTwinEvent).count(),
    )


# ------------------------------------------------- S11-3: future isolation

def test_future_event_never_rewrites_the_past():
    tid = _next_id("TXN-TMP-ISO")
    _seed_tx(tid)
    _seed_chain(tid, with_late_settlement=True)

    # BEFORE the late settlement: settlement must be NOT_OBSERVED and the
    # late event only REPORTED as excluded — never used.
    past = _state_at(tid, T_MID)
    assert past.observed_event_count == 5
    assert past.excluded_event_count == 1
    assert past.reconstruction.stages["settlement"] == "NOT_OBSERVED"
    assert past.reconstruction.root_cause == "MERCHANT_CONFIRMATION_TIMEOUT"
    assert past.uncertainty_note is not None
    assert "1 later event(s) exist" in past.uncertainty_note
    assert "EXCLUDED" in past.uncertainty_note

    # AT/AFTER the late settlement: it is now included.
    now = _state_at(tid, T_LATE + timedelta(minutes=1))
    assert now.observed_event_count == 6
    assert now.excluded_event_count == 0
    assert now.reconstruction.stages["settlement"] == "CONFIRMED"
    assert now.uncertainty_note is None


# -------------------------------------------------------- S11-8: boundary

def test_boundary_query_exactly_at_event_timestamp():
    """<= semantics: a query exactly AT an event timestamp includes it."""
    tid = _next_id("TXN-TMP-BOUND")
    _seed_tx(tid)
    _seed_chain(tid, with_late_settlement=True)

    at_late = _state_at(tid, T_LATE)
    assert at_late.observed_event_count == 6
    assert at_late.excluded_event_count == 0
    assert at_late.reconstruction.stages["settlement"] == "CONFIRMED"

    one_second_before = _state_at(tid, T_LATE - timedelta(seconds=1))
    assert one_second_before.observed_event_count == 5
    assert one_second_before.excluded_event_count == 1


# ---------------------------------------------------- state_then derivation

def test_state_then_birth_state_without_twin_events():
    tid = _next_id("TXN-TMP-BIRTH")
    _seed_tx(tid)
    _seed_chain(tid, with_late_settlement=False)

    report = _state_at(tid, T_MID)
    assert report.state_then == "INITIATED"
    assert report.last_twin_event_type is None


def test_state_then_progression_from_twin_timeline():
    tid = _next_id("TXN-TMP-PROG")
    _seed_tx(tid)
    _seed_chain(tid, with_late_settlement=False)

    t_a = T0 + timedelta(minutes=5)
    t_b = T0 + timedelta(minutes=20)
    _seed_twin_event(tid, t_a, "PROCESSING", "STATE_ENTERED_PROCESSING")
    _seed_twin_event(tid, t_b, "FAILED", "STATE_ENTERED_FAILED")

    before = _state_at(tid, T0 + timedelta(minutes=4))
    assert before.state_then == "INITIATED"

    between = _state_at(tid, T0 + timedelta(minutes=12))
    assert between.state_then == "PROCESSING"
    assert between.last_twin_event_type == "STATE_ENTERED_PROCESSING"

    after = _state_at(tid, T0 + timedelta(minutes=30))
    assert after.state_then == "FAILED"
    assert after.last_twin_event_type == "STATE_ENTERED_FAILED"

    # boundary: exactly at the twin event's timestamp counts as "at/before"
    exactly = _state_at(tid, t_b)
    assert exactly.state_then == "FAILED"


def test_state_then_ignores_mutable_current_state():
    """current_state is FAILED in the seed, but with no twin events at/before
    T the historical state is the birth state — never the mutable value."""
    tid = _next_id("TXN-TMP-MUTABLE")
    _seed_tx(tid)
    _seed_chain(tid, with_late_settlement=True)
    report = _state_at(tid, T0 - timedelta(minutes=1))
    assert report.state_then == "INITIATED"
    assert report.state_then != "FAILED"


# ------------------------------------------------------ no-evidence branch

def test_no_evidence_at_time_note():
    tid = _next_id("TXN-TMP-NOEV")
    _seed_tx(tid)  # no payment events at all

    report = _state_at(tid, T_MID)
    assert report.observed_event_count == 0
    assert report.excluded_event_count == 0
    assert report.reconstruction.root_cause == "INCOMPLETE"
    assert report.reconstruction.reconstruction_confidence == 0.0
    assert report.uncertainty_note == "no evidence existed at this time"


def test_no_observed_but_excluded_note_is_exclusion_only():
    """observed==0 but later events exist: only the exclusion clause applies
    (the no-evidence clause requires excluded == 0 too)."""
    tid = _next_id("TXN-TMP-NOEV2")
    _seed_tx(tid)
    _seed_chain(tid, with_late_settlement=False)

    report = _state_at(tid, T0 - timedelta(minutes=5))
    assert report.observed_event_count == 0
    assert report.excluded_event_count == 5
    assert report.reconstruction.root_cause == "INCOMPLETE"
    assert report.uncertainty_note is not None
    assert "5 later event(s) exist" in report.uncertainty_note
    assert "no evidence existed at this time" not in report.uncertainty_note


# -------------------------------------------------------------- purity

def test_state_at_is_pure():
    tid = _next_id("TXN-TMP-PURE")
    _seed_tx(tid)
    _seed_chain(tid, with_late_settlement=True)
    _seed_twin_event(tid, T0 + timedelta(minutes=11), "STALLED")

    db = SessionLocal()
    try:
        before = _counts(db)
    finally:
        db.close()

    for _ in range(3):
        report = _state_at(tid, T_MID)
        assert report.observed_event_count == 5

    db = SessionLocal()
    try:
        after = _counts(db)
    finally:
        db.close()
    assert after == before


def test_determinism_two_calls_identical():
    tid = _next_id("TXN-TMP-DET")
    _seed_tx(tid)
    _seed_chain(tid, with_late_settlement=True)

    r1 = _state_at(tid, T_MID)
    r2 = _state_at(tid, T_MID)
    assert r1.model_dump() == r2.model_dump()


def test_service_matches_live_engine_on_filtered_set():
    """The temporal reconstruction must equal the live engine run directly on
    the same filtered set — same engine, no divergence."""
    tid = _next_id("TXN-TMP-ENGINE")
    _seed_tx(tid)
    _seed_chain(tid, with_late_settlement=True)

    report = _state_at(tid, T_MID)
    db = SessionLocal()
    try:
        observed = [
            e
            for e in db.query(PaymentEvent)
            .filter_by(transaction_id=tid)
            .all()
            if e.event_timestamp.replace(tzinfo=timezone.utc) <= T_MID
        ]
        direct = reconstruct_from_events(tid, observed, T_MID)
    finally:
        db.close()

    assert report.reconstruction.root_cause == direct.root_cause
    assert (
        report.reconstruction.reconstruction_confidence
        == direct.reconstruction_confidence
    )
    assert report.reconstruction.stages["settlement"] == direct.settlement_status
    assert report.reconstruction.missing_events == direct.missing_events


# ------------------------------------------------------------- API layer

def _get(client, tid: str, ts: str, headers):
    # params= (not f-string interpolation) so '+' in '+00:00' is encoded
    return client.get(
        URL.format(tid=tid), params={"timestamp": ts}, headers=headers
    )


def test_api_admin_shape(client):
    tid = _next_id("TXN-TMP-API1")
    _seed_tx(tid)
    _seed_chain(tid, with_late_settlement=True)

    resp = _get(client, tid, "2026-03-01T10:30:00Z", ADMIN_KEY)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["transaction_id"] == tid
    assert body["as_of"] == "2026-03-01T10:30:00+00:00"
    assert body["state_then"] == "INITIATED"
    assert body["last_twin_event_type"] is None
    assert body["observed_event_count"] == 5
    assert body["excluded_event_count"] == 1
    assert set(body["reconstruction"].keys()) == {
        "root_cause",
        "reconstruction_confidence",
        "stages",
        "missing_events",
    }
    assert set(body["reconstruction"]["stages"].keys()) == {
        "bank_debit", "gateway", "merchant_confirmation", "settlement",
    }
    assert body["reconstruction"]["stages"]["settlement"] == "NOT_OBSERVED"
    assert isinstance(body["uncertainty_note"], str)


def test_api_support_allowed_customer_forbidden_and_404(client):
    tid = _next_id("TXN-TMP-API2")
    _seed_tx(tid)
    _seed_chain(tid, with_late_settlement=False)

    assert _get(client, tid, "2026-03-01T10:30:00Z", SUPPORT_KEY).status_code == 200
    assert _get(client, tid, "2026-03-01T10:30:00Z", CUSTOMER_KEY).status_code == 403
    assert _get(
        client, "TXN-DOES-NOT-EXIST", "2026-03-01T10:30:00Z", ADMIN_KEY
    ).status_code == 404


def test_api_missing_timestamp_is_4xx(client):
    tid = _next_id("TXN-TMP-API3")
    _seed_tx(tid)
    _seed_chain(tid, with_late_settlement=False)

    resp = client.get(URL.format(tid=tid), headers=ADMIN_KEY)
    assert resp.status_code in (400, 422)


def test_api_garbage_timestamp_is_422(client):
    tid = _next_id("TXN-TMP-API4")
    _seed_tx(tid)
    _seed_chain(tid, with_late_settlement=False)

    resp = _get(client, tid, "not-a-timestamp", ADMIN_KEY)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_api_z_suffix_and_offset_equivalence(client):
    """The same instant expressed with Z and +00:00 gives identical views."""
    tid = _next_id("TXN-TMP-API5")
    _seed_tx(tid)
    _seed_chain(tid, with_late_settlement=True)

    z = _get(client, tid, "2026-03-01T10:30:00Z", ADMIN_KEY)
    off = _get(client, tid, "2026-03-01T10:30:00+00:00", ADMIN_KEY)
    assert z.status_code == 200 and off.status_code == 200
    bz, boff = z.json(), off.json()
    assert bz["observed_event_count"] == boff["observed_event_count"]
    assert bz["as_of"] == boff["as_of"]
    assert bz["reconstruction"] == boff["reconstruction"]


def test_api_naive_timestamp_assumed_utc(client):
    tid = _next_id("TXN-TMP-API6")
    _seed_tx(tid)
    _seed_chain(tid, with_late_settlement=True)

    resp = _get(client, tid, "2026-03-01T10:30:00", ADMIN_KEY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["as_of"] == "2026-03-01T10:30:00+00:00"
    assert body["observed_event_count"] == 5


def test_api_audit_row_written(client):
    tid = _next_id("TXN-TMP-API7")
    _seed_tx(tid)
    _seed_chain(tid, with_late_settlement=True)

    from api.db.models import SecurityAuditRecord

    db = SessionLocal()
    try:
        before = _counts(db)
    finally:
        db.close()

    resp = _get(client, tid, "2026-03-01T10:30:00Z", ADMIN_KEY)
    assert resp.status_code == 200

    db = SessionLocal()
    try:
        rows = (
            db.query(SecurityAuditRecord)
            .filter(
                SecurityAuditRecord.action == AUDIT_TEMPORAL_QUERY,
                SecurityAuditRecord.resource_id == tid,
            )
            .all()
        )
        after = _counts(db)
    finally:
        db.close()

    assert len(rows) >= 1
    meta = rows[-1].audit_metadata or {}
    assert meta.get("observed") == 5
    assert meta.get("excluded") == 1
    assert "as_of" in meta
    # read-only besides the audit row: transaction/event/twin counts unchanged
    assert after == before

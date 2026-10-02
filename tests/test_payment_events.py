"""Stage 6 payment-domain event ingestion: batch endpoint, replay
idempotency, timestamp-ordered reads, and root-cause twin integration."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from api.db.models import Transaction
from tests.conftest import ADMIN_KEY, SUPPORT_KEY, SYSTEM_KEY, make_event

EVENT_URL = "/api/v1/transaction/event"
PE_URL = "/api/v1/transactions/{tid}/payment-events"


def _unique_id(prefix: str = "TXN-PE") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _ts(minute: int) -> datetime:
    return datetime(2026, 10, 2, 12, minute, tzinfo=timezone.utc)


def _create_tx(client, tid) -> None:
    resp = client.post(
        EVENT_URL, json=make_event(tid, amount=50.00, status="SUCCESS"),
        headers=SYSTEM_KEY,
    )
    assert resp.status_code == 200


def _batch(tid, provider_prefix, timestamps) -> dict:
    triples = [
        ("CUSTOMER_DEBIT_CONFIRMED", "BANK", "CONFIRMED"),
        ("GATEWAY_REQUEST_SENT", "GATEWAY", "OBSERVED"),
        ("GATEWAY_TIMEOUT", "GATEWAY", "TIMEOUT"),
    ]
    return {
        "events": [
            {
                "provider_event_id": f"{provider_prefix}-{i}",
                "event_type": event_type,
                "source": source,
                "status": outcome,
                "event_timestamp": ts.isoformat(),
            }
            for i, ((event_type, source, outcome), ts) in enumerate(
                zip(triples, timestamps), start=1
            )
        ]
    }


def test_ingest_unknown_tx_is_404(client):
    resp = client.post(
        PE_URL.format(tid="TXN-DOES-NOT-EXIST"),
        json=_batch("TXN-DOES-NOT-EXIST", "EVT-X", [_ts(1)] * 3),
        headers=SYSTEM_KEY,
    )
    assert resp.status_code == 404


def test_valid_batch_created_and_ordered_by_timestamp(client):
    tid = _unique_id()
    _create_tx(client, tid)

    # first batch: 3 events created
    resp = client.post(
        PE_URL.format(tid=tid), json=_batch(tid, f"PE-{tid}", [_ts(1), _ts(2), _ts(3)]),
        headers=SYSTEM_KEY,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["transaction_id"] == tid
    assert body["created"] == 3
    assert body["duplicates"] == 0
    assert len(body["events"]) == 3
    assert [e["event_type"] for e in body["events"]] == [
        "CUSTOMER_DEBIT_CONFIRMED", "GATEWAY_REQUEST_SENT", "GATEWAY_TIMEOUT",
    ]

    # second batch sent OUT OF ORDER is stored and read back in domain-time order
    resp2 = client.post(
        PE_URL.format(tid=tid), json=_batch(tid, f"PE2-{tid}", [_ts(6), _ts(4), _ts(5)]),
        headers=SYSTEM_KEY,
    )
    assert resp2.status_code == 200
    assert resp2.json()["created"] == 3

    from api.db.database import SessionLocal
    from api.services.payment_event_service import get_payment_events

    db = SessionLocal()
    try:
        events = get_payment_events(db, tid)
        assert len(events) == 6
        timestamps = [e.event_timestamp for e in events]
        assert timestamps == sorted(timestamps)
        assert [e.event_type for e in events] == [
            "CUSTOMER_DEBIT_CONFIRMED", "GATEWAY_REQUEST_SENT", "GATEWAY_TIMEOUT",
            "GATEWAY_REQUEST_SENT", "GATEWAY_TIMEOUT", "CUSTOMER_DEBIT_CONFIRMED",
        ]
    finally:
        db.close()


def test_replay_is_idempotent(client):
    tid = _unique_id()
    _create_tx(client, tid)
    payload = _batch(tid, f"PE-R-{tid}", [_ts(1), _ts(2), _ts(3)])

    first = client.post(PE_URL.format(tid=tid), json=payload, headers=SYSTEM_KEY)
    assert first.status_code == 200
    assert first.json()["created"] == 3

    # same provider_event_ids again -> duplicates, no new rows
    second = client.post(PE_URL.format(tid=tid), json=payload, headers=SYSTEM_KEY)
    assert second.status_code == 200
    body = second.json()
    assert body["created"] == 0
    assert body["duplicates"] == 3
    assert body["events"] == []


def test_invalid_event_type_is_422(client):
    tid = _unique_id()
    _create_tx(client, tid)
    payload = _batch(tid, f"PE-T1-{tid}", [_ts(1)] * 3)
    payload["events"][0]["event_type"] = "NOT_A_REAL_EVENT"
    resp = client.post(PE_URL.format(tid=tid), json=payload, headers=SYSTEM_KEY)
    assert resp.status_code == 422


def test_invalid_source_is_422(client):
    tid = _unique_id()
    _create_tx(client, tid)
    payload = _batch(tid, f"PE-T2-{tid}", [_ts(1)] * 3)
    payload["events"][0]["source"] = "MOON"
    resp = client.post(PE_URL.format(tid=tid), json=payload, headers=SYSTEM_KEY)
    assert resp.status_code == 422


def test_invalid_status_is_422(client):
    tid = _unique_id()
    _create_tx(client, tid)
    payload = _batch(tid, f"PE-T3-{tid}", [_ts(1)] * 3)
    payload["events"][0]["status"] = "MAYBE"
    resp = client.post(PE_URL.format(tid=tid), json=payload, headers=SYSTEM_KEY)
    assert resp.status_code == 422


def test_auth_required(client):
    tid = _unique_id()
    _create_tx(client, tid)
    assert client.post(
        PE_URL.format(tid=tid), json=_batch(tid, f"PE-A-{tid}", [_ts(1)] * 3),
    ).status_code == 401
    assert client.post(
        PE_URL.format(tid=tid), json=_batch(tid, f"PE-B-{tid}", [_ts(1)] * 3),
        headers=SUPPORT_KEY,
    ).status_code == 403


def test_record_root_cause_event_idempotent_on_same_cause(client):
    from api.db.database import SessionLocal
    from api.db.models import DigitalTwinEvent
    from api.services.payment_event_service import record_root_cause_event
    from sqlalchemy import select, func

    tid = _unique_id()
    _create_tx(client, tid)

    db = SessionLocal()
    try:
        tx = db.scalars(
            select(Transaction).where(Transaction.transaction_id == tid)
        ).one()

        def _count() -> int:
            return db.scalars(
                select(func.count()).select_from(DigitalTwinEvent).where(
                    DigitalTwinEvent.transaction_id == tid,
                    DigitalTwinEvent.event_type == "ROOT_CAUSE_IDENTIFIED",
                )
            ).one()

        reconstruction = {
            "root_cause": "GATEWAY_TIMEOUT_NO_RESPONSE",
            "failure_stage": "GATEWAY",
            "last_successful_stage": "BANK_DEBIT",
            "stage_statuses": {
                "BANK_DEBIT": "CONFIRMED", "GATEWAY": "TIMEOUT",
                "MERCHANT_CONFIRMATION": "NOT_OBSERVED", "SETTLEMENT": "NOT_OBSERVED",
            },
            "missing_events": ["GATEWAY_RESPONSE_RECEIVED"],
        }

        assert record_root_cause_event(db, tx, reconstruction) is True
        db.commit()
        assert _count() == 1

        # same root cause -> no duplicate
        assert record_root_cause_event(db, tx, reconstruction) is False
        db.commit()
        assert _count() == 1

        # changed verdict -> appends a new observation
        changed = dict(reconstruction, root_cause="MERCHANT_DISCONNECT")
        assert record_root_cause_event(db, tx, changed) is True
        db.commit()
        assert _count() == 2

        events = list(db.scalars(
            select(DigitalTwinEvent).where(
                DigitalTwinEvent.transaction_id == tid,
                DigitalTwinEvent.event_type == "ROOT_CAUSE_IDENTIFIED",
            ).order_by(DigitalTwinEvent.id.asc())
        ))
        first = events[0]
        # observation, not a transition
        assert first.previous_state == first.new_state == tx.current_state
        assert first.reason == "Root cause: GATEWAY_TIMEOUT_NO_RESPONSE"
        meta = first.event_metadata
        assert meta["failure_stage"] == "GATEWAY"
        assert meta["reconstruction_version"] == "1"
        assert meta["stage_statuses"]["MERCHANT_CONFIRMATION"] == "NOT_OBSERVED"
    finally:
        db.close()

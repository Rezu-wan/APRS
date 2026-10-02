"""Reconstruction API tests: GET /api/v1/transactions/{id}/reconstruction.

Payment events are seeded by inserting PaymentEvent rows directly through a
SessionLocal session (the ingestion endpoint belongs to a parallel slice);
the transaction itself is created through the normal ingestion endpoint.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from api.core.payment_lifecycle import HAPPY_PATH_EVENTS, EVENT_TYPE_INFO
from api.db.database import SessionLocal
from api.db.models import PaymentEvent

from tests.conftest import CLEAN_FAILED_TX, CUSTOMER_KEY, SYSTEM_KEY, make_event

EVENT_URL = "/api/v1/transaction/event"
RECON_URL = "/api/v1/transactions/{tid}/reconstruction"
TIMELINE_URL = "/api/v1/transactions/{tid}/timeline"

_T0 = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)


def _unique_id() -> str:
    return f"TXN-RECON-{uuid.uuid4().hex[:12]}"


def _seed_tx(client, tid: str) -> None:
    payload = make_event(tid, **CLEAN_FAILED_TX)
    resp = client.post(EVENT_URL, json=payload, headers=SYSTEM_KEY)
    assert resp.status_code == 200


def _seed_events(tid: str, event_types: list[str]) -> None:
    """Insert PaymentEvent rows directly (engine path under test)."""
    db = SessionLocal()
    try:
        base = datetime.now(timezone.utc) - timedelta(hours=1)
        for i, event_type in enumerate(event_types):
            info = EVENT_TYPE_INFO[event_type]
            db.add(
                PaymentEvent(
                    transaction_id=tid,
                    provider_event_id=f"{tid}-{event_type}-{i}",
                    event_type=event_type,
                    source=info["source"],
                    status=info["outcome"],
                    event_timestamp=base + timedelta(minutes=i),
                )
            )
        db.commit()
    finally:
        db.close()


def _get(client, tid: str):
    return client.get(RECON_URL.format(tid=tid), headers=SYSTEM_KEY)


def test_reconstruction_success_scenario(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    _seed_events(tid, list(HAPPY_PATH_EVENTS))

    resp = _get(client, tid)
    assert resp.status_code == 200
    body = resp.json()
    assert body["transaction_id"] == tid
    assert body["root_cause"] == "NONE"
    assert body["current_stage"] == "SETTLEMENT"
    assert body["reconstruction_confidence"] == 1.0
    assert body["digital_twin_event_recorded"] is True
    assert len(body["ordered_events"]) == 7
    assert body["ordered_events"][0]["event_type"] == "CUSTOMER_DEBIT_CONFIRMED"
    assert "Payment completed successfully." in body["evidence_summary"]
    # UtcDatetime: offset-designated ISO-8601 regardless of serializer form
    ts = body["ordered_events"][0]["event_timestamp"]
    assert ts.endswith("+00:00") or ts.endswith("Z")


def test_reconstruction_gateway_timeout_scenario(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    _seed_events(
        tid,
        ["CUSTOMER_DEBIT_CONFIRMED", "GATEWAY_REQUEST_SENT", "GATEWAY_TIMEOUT"],
    )
    resp = _get(client, tid)
    assert resp.status_code == 200
    body = resp.json()
    assert body["root_cause"] == "GATEWAY_TIMEOUT"
    assert body["failure_stage"] == "GATEWAY"
    assert body["last_successful_stage"] == "BANK_DEBIT"
    assert body["reconstruction_confidence"] == 0.43


def test_twin_append_idempotent_on_repeat(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    _seed_events(
        tid,
        ["CUSTOMER_DEBIT_CONFIRMED", "GATEWAY_REQUEST_SENT", "GATEWAY_TIMEOUT"],
    )

    first = _get(client, tid)
    assert first.status_code == 200
    assert first.json()["digital_twin_event_recorded"] is True

    timeline = client.get(TIMELINE_URL.format(tid=tid), headers=SYSTEM_KEY).json()
    root_cause_events = [
        e for e in timeline["events"] if e["event_type"] == "ROOT_CAUSE_IDENTIFIED"
    ]
    assert len(root_cause_events) == 1
    assert root_cause_events[0]["metadata"]["root_cause"] == "GATEWAY_TIMEOUT"

    second = _get(client, tid)
    assert second.status_code == 200
    assert second.json()["digital_twin_event_recorded"] is False

    timeline_after = client.get(
        TIMELINE_URL.format(tid=tid), headers=SYSTEM_KEY
    ).json()
    assert timeline_after["event_count"] == timeline["event_count"]


def test_reconstruction_unknown_tx_is_404(client):
    resp = _get(client, "TXN-DOES-NOT-EXIST")
    assert resp.status_code == 404


def test_reconstruction_no_key_is_401(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    resp = client.get(RECON_URL.format(tid=tid))
    assert resp.status_code == 401


def test_reconstruction_customer_key_is_403(client):
    tid = _unique_id()
    _seed_tx(client, tid)
    resp = client.get(RECON_URL.format(tid=tid), headers=CUSTOMER_KEY)
    assert resp.status_code == 403

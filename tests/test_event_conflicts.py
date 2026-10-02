"""Stage 9 event-ingestion hardening: payload validation (422 paths),
replay-vs-conflict semantics (409 EVENT_CONFLICT), and out-of-order / late
arrival acceptance."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from tests.conftest import SYSTEM_KEY, make_event

EVENT_URL = "/api/v1/transaction/event"
PE_URL = "/api/v1/transactions/{tid}/payment-events"


def _unique_id(prefix: str = "TXN-EC") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _create_tx(client, tid) -> None:
    resp = client.post(
        EVENT_URL, json=make_event(tid, amount=50.00, status="SUCCESS"),
        headers=SYSTEM_KEY,
    )
    assert resp.status_code == 200


def _event(provider_event_id: str, ts: datetime, **overrides) -> dict:
    payload = {
        "provider_event_id": provider_event_id,
        "event_type": "GATEWAY_TIMEOUT",
        "source": "GATEWAY",
        "status": "TIMEOUT",
        "event_timestamp": ts.isoformat(),
    }
    payload.update(overrides)
    return payload


def _single(tid, provider_event_id: str, ts: datetime, **overrides) -> dict:
    return {"events": [_event(provider_event_id, ts, **overrides)]}


def _post(client, tid, payload):
    return client.post(PE_URL.format(tid=tid), json=payload, headers=SYSTEM_KEY)


class TestReplayVsConflict:
    def test_exact_duplicate_batch_is_replay_created_zero(self, client):
        tid = _unique_id()
        _create_tx(client, tid)
        payload = _single(tid, f"EVT-{tid}-1", datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc))
        first = _post(client, tid, payload)
        assert first.status_code == 200
        assert first.json()["created"] == 1
        second = _post(client, tid, payload)
        assert second.status_code == 200
        body = second.json()
        assert body["created"] == 0
        assert body["duplicates"] == 1

    def test_same_id_same_payload_different_metadata_is_replay(self, client):
        """metadata is NOT part of the identity digest — a redelivery that
        only differs in (non-identity) metadata is still a replay."""
        tid = _unique_id()
        _create_tx(client, tid)
        ts = datetime(2026, 10, 2, 12, 1, tzinfo=timezone.utc)
        peid = f"EVT-{tid}-1"
        first = _post(client, tid, _single(tid, peid, ts, metadata={"seq": 1}))
        assert first.json()["created"] == 1
        second = _post(client, tid, _single(tid, peid, ts, metadata={"seq": 2}))
        assert second.status_code == 200
        assert second.json()["duplicates"] == 1

    def test_same_id_different_status_is_409_event_conflict(self, client):
        tid = _unique_id()
        _create_tx(client, tid)
        ts = datetime(2026, 10, 2, 12, 2, tzinfo=timezone.utc)
        peid = f"EVT-{tid}-1"
        first = _post(client, tid, _single(tid, peid, ts))
        assert first.json()["created"] == 1
        conflict = _post(
            client, tid,
            _single(tid, peid, ts, event_type="GATEWAY_REQUEST_SENT",
                    source="GATEWAY", status="OBSERVED"),
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "EVENT_CONFLICT"
        # no partial write: the stored row is unchanged and only one row exists
        from api.db.database import SessionLocal
        from api.db.models import PaymentEvent
        from sqlalchemy import select, func
        db = SessionLocal()
        try:
            rows = list(db.scalars(
                select(PaymentEvent).where(PaymentEvent.provider_event_id == peid)
            ))
            assert len(rows) == 1
            assert rows[0].event_type == "GATEWAY_TIMEOUT"
        finally:
            db.close()

    def test_same_id_different_timestamp_is_409(self, client):
        tid = _unique_id()
        _create_tx(client, tid)
        peid = f"EVT-{tid}-1"
        first = _post(client, tid, _single(tid, peid, datetime(2026, 10, 2, 12, 3, tzinfo=timezone.utc)))
        assert first.json()["created"] == 1
        conflict = _post(
            client, tid,
            _single(tid, peid, datetime(2026, 10, 2, 13, 3, tzinfo=timezone.utc)),
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "EVENT_CONFLICT"

    def test_conflict_in_second_event_of_batch_does_not_write_first(self, client):
        """A conflict anywhere in the batch must leave NO partial write."""
        tid = _unique_id()
        _create_tx(client, tid)
        ts = datetime(2026, 10, 2, 12, 4, tzinfo=timezone.utc)
        peid = f"EVT-{tid}-1"
        first = _post(client, tid, _single(tid, peid, ts))
        assert first.json()["created"] == 1
        batch = {
            "events": [
                _event(f"EVT-{tid}-2", datetime(2026, 10, 2, 12, 5, tzinfo=timezone.utc)),
                _event(peid, ts, event_type="GATEWAY_REQUEST_SENT",
                       source="GATEWAY", status="OBSERVED"),
            ]
        }
        resp = _post(client, tid, batch)
        assert resp.status_code == 409
        from api.db.database import SessionLocal
        from api.db.models import PaymentEvent
        from sqlalchemy import select, func
        db = SessionLocal()
        try:
            count = db.scalars(
                select(func.count()).select_from(PaymentEvent).where(
                    PaymentEvent.transaction_id == tid)
            ).one()
            assert count == 1  # only the original row; EVT-2 was rolled back
        finally:
            db.close()

    def test_rapid_identical_redelivery_is_replay(self, client):
        """Two sequential identical batches (simulating concurrent redelivery)
        — the second must be fully counted as duplicates."""
        tid = _unique_id()
        _create_tx(client, tid)
        payload = {
            "events": [
                _event(f"EVT-{tid}-{i}", datetime(2026, 10, 2, 12, i, tzinfo=timezone.utc))
                for i in range(3)
            ]
        }
        first = _post(client, tid, payload)
        assert first.status_code == 200
        assert first.json()["created"] == 3
        second = _post(client, tid, payload)
        assert second.status_code == 200
        assert second.json()["created"] == 0
        assert second.json()["duplicates"] == 3


class TestOrderingAndSkew:
    def test_out_of_order_arrival_still_stored(self, client):
        tid = _unique_id()
        _create_tx(client, tid)
        payload = {
            "events": [
                _event(f"EVT-{tid}-3", datetime(2026, 10, 2, 12, 30, tzinfo=timezone.utc)),
                _event(f"EVT-{tid}-1", datetime(2026, 10, 2, 12, 10, tzinfo=timezone.utc)),
                _event(f"EVT-{tid}-2", datetime(2026, 10, 2, 12, 20, tzinfo=timezone.utc)),
            ]
        }
        resp = _post(client, tid, payload)
        assert resp.status_code == 200
        assert resp.json()["created"] == 3

        from api.db.database import SessionLocal
        from api.services.payment_event_service import get_payment_events
        db = SessionLocal()
        try:
            events = get_payment_events(db, tid)
            assert [e.provider_event_id for e in events] == [
                f"EVT-{tid}-1", f"EVT-{tid}-2", f"EVT-{tid}-3",
            ]
        finally:
            db.close()

    def test_late_event_far_in_the_past_is_accepted(self, client):
        tid = _unique_id()
        _create_tx(client, tid)
        resp = _post(client, tid, _single(
            tid, f"EVT-{tid}-late", datetime(2020, 1, 1, tzinfo=timezone.utc)))
        assert resp.status_code == 200
        assert resp.json()["created"] == 1

    def test_future_event_beyond_24h_is_422(self, client):
        tid = _unique_id()
        _create_tx(client, tid)
        far_future = datetime.now(timezone.utc) + timedelta(hours=25)
        resp = _post(client, tid, _single(tid, f"EVT-{tid}-ff", far_future))
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_future_event_within_24h_is_accepted(self, client):
        tid = _unique_id()
        _create_tx(client, tid)
        near_future = datetime.now(timezone.utc) + timedelta(hours=1)
        resp = _post(client, tid, _single(tid, f"EVT-{tid}-nf", near_future))
        assert resp.status_code == 200
        assert resp.json()["created"] == 1


class TestPayloadValidation:
    def _create_and_post(self, client, provider_event_id=None, ts=None, **overrides):
        tid = _unique_id()
        _create_tx(client, tid)
        if provider_event_id is None:
            provider_event_id = f"EVT-{tid}-1"
        payload = _single(
            tid, provider_event_id,
            ts or datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
            **overrides,
        )
        return _post(client, tid, payload)

    def test_oversized_metadata_is_422(self, client):
        big_metadata = {"blob": "x" * 5000}
        resp = self._create_and_post(client, metadata=big_metadata)
        assert resp.status_code == 422

    def test_metadata_just_under_limit_is_accepted(self, client):
        ok_metadata = {"blob": "x" * 1000}
        resp = self._create_and_post(client, metadata=ok_metadata)
        assert resp.status_code == 200

    def test_non_string_metadata_key_rejected(self, client):
        # JSON objects cannot carry non-string keys over the wire, so this is
        # a schema-level check: the validator must reject them directly
        import pytest
        from pydantic import ValidationError
        from api.schemas.payment_events import PaymentEventIn
        base = dict(
            provider_event_id="EVT-x-1",
            event_type="GATEWAY_TIMEOUT",
            source="GATEWAY",
            status="TIMEOUT",
            event_timestamp="2026-10-02T12:00:00+00:00",
        )
        with pytest.raises(ValidationError):
            PaymentEventIn(**base, metadata={1: "one"})

    def test_negative_latency_is_422(self, client):
        resp = self._create_and_post(client, latency_ms=-1)
        assert resp.status_code == 422

    def test_latency_over_one_hour_is_422(self, client):
        resp = self._create_and_post(client, latency_ms=3_600_001)
        assert resp.status_code == 422

    def test_latency_boundary_values_accepted(self, client):
        resp = self._create_and_post(client, latency_ms=0)
        assert resp.status_code == 200
        resp2 = self._create_and_post(client, latency_ms=3_600_000)
        assert resp2.status_code == 200

    def test_malformed_provider_event_id_is_422(self, client):
        resp = self._create_and_post(client, provider_event_id="bad id!")
        assert resp.status_code == 422

    def test_empty_provider_event_id_is_422(self, client):
        resp = self._create_and_post(client, provider_event_id="")
        assert resp.status_code == 422

    def test_provider_event_id_over_128_chars_is_422(self, client):
        resp = self._create_and_post(client, provider_event_id="a" * 129)
        assert resp.status_code == 422

    def test_allowed_provider_event_id_characters_accepted(self, client):
        resp = self._create_and_post(
            client, provider_event_id="EVT.prov_1:x-2"[:128].replace("_", "-"))
        assert resp.status_code == 200

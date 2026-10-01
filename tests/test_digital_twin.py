"""Digital Twin: append-only timeline ordering and state consistency."""

from __future__ import annotations

import uuid

from tests.conftest import CLEAN_FAILED_TX, SYSTEM_KEY, make_event

EVENT_URL = "/api/v1/transaction/event"


def _unique_id(prefix: str = "TXN-TWIN") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def test_timeline_order_and_state_consistency(client):
    tid = _unique_id()
    resp = client.post(EVENT_URL, json=make_event(tid, **CLEAN_FAILED_TX),
                       headers=SYSTEM_KEY)
    assert resp.status_code == 200

    release = client.post("/api/v1/recovery/release-limit",
                          json={"transaction_id": tid}, headers=SYSTEM_KEY)
    assert release.status_code == 200
    assert release.json()["decision"] == "LIMIT_RELEASED"

    timeline = client.get(f"/api/v1/transactions/{tid}/timeline",
                          headers=SYSTEM_KEY).json()
    assert timeline["transaction_id"] == tid
    assert timeline["current_state"] == "LIMIT_RELEASED"
    events = timeline["events"]
    assert timeline["event_count"] == len(events)

    expected_types = [
        "TRANSACTION_CREATED",
        "PAYMENT_PROCESSING",
        "PAYMENT_FAILED",
        "ML_RISK_ASSESSED",
        "RECOVERY_CHECKED",
        "LIMIT_RELEASED",
    ]
    assert [e["event_type"] for e in events] == expected_types

    # every event's new_state matches the transaction state at that point
    assert events[0]["previous_state"] is None
    assert events[0]["new_state"] == "INITIATED"
    for prev, cur in zip(events, events[1:]):
        assert cur["previous_state"] == prev["new_state"]
    assert events[-1]["new_state"] == "LIMIT_RELEASED"

    # ML snapshot present on the assessment event
    ml_event = next(e for e in events if e["event_type"] == "ML_RISK_ASSESSED")
    assert ml_event["risk_score"] is not None
    assert ml_event["safe_to_release"] is True
    assert ml_event["failure_prediction"] is not None
    assert ml_event["new_state"] == "RISK_ASSESSED"

    # timestamps are non-decreasing (append-only log, ordered)
    stamps = [e["timestamp"] for e in events]
    assert stamps == sorted(stamps)


def test_duplicate_recovery_produced_no_extra_events(client):
    tid = _unique_id()
    resp = client.post(EVENT_URL, json=make_event(
        tid, amount=1250.00, gateway_latency_ms=2800, retry_count=3,
        network_quality="Poor", previous_failures=2, account_age_days=40,
        status="FAILED", failure_reason="Timeout",
    ), headers=SYSTEM_KEY)
    assert resp.status_code == 200

    first = client.post("/api/v1/recovery/release-limit",
                        json={"transaction_id": tid}, headers=SYSTEM_KEY)
    assert first.status_code == 200

    t1 = client.get(f"/api/v1/transactions/{tid}/timeline",
                    headers=SYSTEM_KEY).json()
    types1 = [e["event_type"] for e in t1["events"]]
    assert types1[-1] == "MANUAL_REVIEW_TRIGGERED"

    second = client.post("/api/v1/recovery/release-limit",
                         json={"transaction_id": tid}, headers=SYSTEM_KEY)
    assert second.status_code == 200
    assert second.json()["already_applied"] is True

    t2 = client.get(f"/api/v1/transactions/{tid}/timeline",
                    headers=SYSTEM_KEY).json()
    assert t2["event_count"] == t1["event_count"]
    assert [e["event_type"] for e in t2["events"]] == types1
    assert sum(1 for e in t2["events"]
               if e["event_type"] == "MANUAL_REVIEW_TRIGGERED") == 1

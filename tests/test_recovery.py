"""Recovery policy: LIMIT_RELEASED / MANUAL_REVIEW / RECOVERY_REJECTED,
idempotency, and non-recoverable states."""

from __future__ import annotations

import uuid

from tests.conftest import CLEAN_FAILED_TX, SYSTEM_KEY, make_event

EVENT_URL = "/api/v1/transaction/event"
RELEASE_URL = "/api/v1/recovery/release-limit"


def _unique_id(prefix: str = "TXN-RECOVERY") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _ingest_failed(client, tid, **overrides):
    payload = dict(
        transaction_id=tid, user_id="USER-001", merchant_id="MERCHANT-001",
        currency="BDT",
    )
    payload.update(CLEAN_FAILED_TX)
    payload.update(overrides)
    return client.post(EVENT_URL, json=payload, headers=SYSTEM_KEY)


def test_clean_failed_tx_releases_limit(client):
    tid = _unique_id()
    assert _ingest_failed(client, tid).status_code == 200
    resp = client.post(RELEASE_URL, json={"transaction_id": tid},
                       headers=SYSTEM_KEY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "LIMIT_RELEASED"
    assert body["safe_to_release"] is True
    assert body["safe_to_release_probability"] >= 0.90
    assert body["already_applied"] is False
    assert body["current_state"] == "LIMIT_RELEASED"

    tx = client.get(f"/api/v1/transactions/{tid}", headers=SYSTEM_KEY).json()
    assert tx["current_state"] == "LIMIT_RELEASED"


def test_risky_tx_goes_to_manual_review(client):
    tid = _unique_id()
    assert _ingest_failed(
        client, tid,
        amount=1250.00, gateway_latency_ms=2800, retry_count=3,
        network_quality="Poor", previous_failures=2, account_age_days=40,
        failure_reason="Timeout",
    ).status_code == 200
    resp = client.post(RELEASE_URL, json={"transaction_id": tid},
                       headers=SYSTEM_KEY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "MANUAL_REVIEW"
    assert body["safe_to_release"] is False
    assert body["current_state"] == "MANUAL_REVIEW"


def test_over_cap_tx_is_rejected(client):
    tid = _unique_id()
    assert _ingest_failed(client, tid, amount=2500.00).status_code == 200
    resp = client.post(RELEASE_URL, json={"transaction_id": tid},
                       headers=SYSTEM_KEY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "RECOVERY_REJECTED"
    assert body["current_state"] == "RECOVERY_REJECTED"


def test_duplicate_release_is_idempotent_and_appends_no_events(client):
    tid = _unique_id()
    assert _ingest_failed(client, tid).status_code == 200

    first = client.post(RELEASE_URL, json={"transaction_id": tid},
                        headers=SYSTEM_KEY)
    assert first.status_code == 200
    assert first.json()["already_applied"] is False
    assert first.json()["decision"] == "LIMIT_RELEASED"

    timeline_after_first = client.get(
        f"/api/v1/transactions/{tid}/timeline", headers=SYSTEM_KEY).json()
    count_after_first = timeline_after_first["event_count"]

    second = client.post(RELEASE_URL, json={"transaction_id": tid},
                         headers=SYSTEM_KEY)
    assert second.status_code == 200
    replay = second.json()
    assert replay["already_applied"] is True
    assert replay["decision"] == "LIMIT_RELEASED"

    timeline_after_second = client.get(
        f"/api/v1/transactions/{tid}/timeline", headers=SYSTEM_KEY).json()
    assert timeline_after_second["event_count"] == count_after_first
    assert replay["current_state"] == "LIMIT_RELEASED"


def test_recovery_on_success_tx_is_409(client):
    tid = _unique_id()
    resp = client.post(EVENT_URL, json=make_event(tid, amount=50.00, status="SUCCESS"),
                       headers=SYSTEM_KEY)
    assert resp.status_code == 200
    conflict = client.post(RELEASE_URL, json={"transaction_id": tid},
                           headers=SYSTEM_KEY)
    assert conflict.status_code == 409


def test_recovery_on_unknown_tx_is_404(client):
    resp = client.post(RELEASE_URL, json={"transaction_id": "TXN-DOES-NOT-EXIST"},
                       headers=SYSTEM_KEY)
    assert resp.status_code == 404

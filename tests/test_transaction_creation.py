"""Transaction creation: valid input and input-validation (422) paths."""

from __future__ import annotations

import uuid

from tests.conftest import ADMIN_KEY, SYSTEM_KEY, make_event

URL = "/api/v1/transaction/event"


def _unique_id(prefix: str = "TXN-CREATE") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class TestValidCreation:
    def test_valid_failed_event_creates_transaction(self, client):
        tid = _unique_id()
        resp = client.post(URL, json=make_event(tid, **{
            "amount": 30.00,
            "gateway_latency_ms": 45,
            "retry_count": 0,
            "network_quality": "Excellent",
            "previous_failures": 0,
            "account_age_days": 2000,
            "status": "FAILED",
            "failure_reason": "Gateway Error",
        }), headers=SYSTEM_KEY)
        assert resp.status_code == 200
        body = resp.json()
        assert body["transaction_id"] == tid
        assert body["created"] is True
        assert body["state_changed"] is True
        assert body["current_state"] == "RECOVERY_PENDING"

        fetch = client.get(f"/api/v1/transactions/{tid}", headers=SYSTEM_KEY)
        assert fetch.status_code == 200
        tx = fetch.json()
        assert tx["transaction_id"] == tid
        assert tx["amount"] == "30.00" or float(tx["amount"]) == 30.00
        assert tx["currency"] == "BDT"
        assert tx["failure_reason"] == "Gateway Error"
        assert tx["current_state"] == "RECOVERY_PENDING"

    def test_valid_success_event(self, client):
        tid = _unique_id()
        resp = client.post(URL, json=make_event(tid, amount=50.00, status="SUCCESS"),
                           headers=ADMIN_KEY)
        assert resp.status_code == 200
        assert resp.json()["current_state"] == "SUCCESS"


class TestInvalidInput:
    def test_failed_without_failure_reason_is_422(self, client):
        tid = _unique_id()
        resp = client.post(URL, json=make_event(tid, amount=30.00, status="FAILED"),
                           headers=SYSTEM_KEY)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
        # nothing must have been persisted
        assert client.get(f"/api/v1/transactions/{tid}",
                          headers=SYSTEM_KEY).status_code == 404

    def test_unknown_network_quality_is_422(self, client):
        tid = _unique_id()
        resp = client.post(URL, json=make_event(
            tid, amount=30.00, status="FAILED",
            failure_reason="Gateway Error", network_quality="Telepathic",
        ), headers=SYSTEM_KEY)
        assert resp.status_code == 422

    def test_zero_amount_is_422(self, client):
        tid = _unique_id()
        resp = client.post(URL, json=make_event(tid, amount=0, status="SUCCESS"),
                           headers=SYSTEM_KEY)
        assert resp.status_code == 422

    def test_negative_amount_is_422(self, client):
        tid = _unique_id()
        resp = client.post(URL, json=make_event(tid, amount=-10.00, status="SUCCESS"),
                           headers=SYSTEM_KEY)
        assert resp.status_code == 422

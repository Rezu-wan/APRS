"""API surface: health, auth (401), RBAC (403), unknown resources (404)."""

from __future__ import annotations

import uuid

from tests.conftest import (
    ADMIN_KEY,
    CLEAN_FAILED_TX,
    CUSTOMER_KEY,
    SUPPORT_KEY,
    SYSTEM_KEY,
    make_event,
)

EVENT_URL = "/api/v1/transaction/event"
RELEASE_URL = "/api/v1/recovery/release-limit"


def _unique_id(prefix: str = "TXN-API") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _make_failed_tx(client, tid):
    resp = client.post(EVENT_URL, json=make_event(tid, **CLEAN_FAILED_TX),
                       headers=SYSTEM_KEY)
    assert resp.status_code == 200
    return tid


class TestHealth:
    def test_health_is_public_and_healthy(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["database"] == "connected"
        assert body["ml_models"] == "loaded"


class TestAuthentication:
    def test_missing_key_is_401(self, client):
        assert client.post(EVENT_URL, json=make_event("TXN-X1", amount=1, status="PROCESSING")).status_code == 401
        assert client.post(RELEASE_URL, json={"transaction_id": "TXN-X1"}).status_code == 401
        assert client.get("/api/v1/transactions/TXN-X1").status_code == 401
        assert client.get("/api/v1/transactions/TXN-X1/timeline").status_code == 401

    def test_bad_key_is_401(self, client):
        bad = {"X-API-Key": "not-a-real-key"}
        assert client.post(EVENT_URL, json=make_event("TXN-X2", amount=1, status="PROCESSING"), headers=bad).status_code == 401
        assert client.post(RELEASE_URL, json={"transaction_id": "TXN-X2"}, headers=bad).status_code == 401
        assert client.get("/api/v1/transactions/TXN-X2", headers=bad).status_code == 401


class TestAuthorization:
    def test_customer_cannot_write(self, client):
        assert client.post(EVENT_URL, json=make_event("TXN-X3", amount=1, status="PROCESSING"),
                           headers=CUSTOMER_KEY).status_code == 403
        assert client.post(RELEASE_URL, json={"transaction_id": "TXN-X3"},
                           headers=CUSTOMER_KEY).status_code == 403

    def test_support_cannot_write(self, client):
        assert client.post(EVENT_URL, json=make_event("TXN-X4", amount=1, status="PROCESSING"),
                           headers=SUPPORT_KEY).status_code == 403
        assert client.post(RELEASE_URL, json={"transaction_id": "TXN-X4"},
                           headers=SUPPORT_KEY).status_code == 403

    def test_staff_roles_can_read(self, client):
        tid = _make_failed_tx(client, _unique_id())
        for key in (SYSTEM_KEY, ADMIN_KEY, SUPPORT_KEY):
            assert client.get(f"/api/v1/transactions/{tid}", headers=key).status_code == 200
            assert client.get(f"/api/v1/transactions/{tid}/timeline", headers=key).status_code == 200

    def test_customer_cannot_read(self, client):
        """CUSTOMER is excluded from reads until identity is user-bound:
        there is no ownership scoping yet, so unscoped reads would be an
        IDOR-style data exposure."""
        tid = _make_failed_tx(client, _unique_id())
        assert client.get(f"/api/v1/transactions/{tid}", headers=CUSTOMER_KEY).status_code == 403
        assert client.get(f"/api/v1/transactions/{tid}/timeline", headers=CUSTOMER_KEY).status_code == 403


class TestNotFound:
    def test_unknown_transaction_is_404(self, client):
        assert client.get("/api/v1/transactions/TXN-NOTHING", headers=SYSTEM_KEY).status_code == 404
        assert client.get("/api/v1/transactions/TXN-NOTHING/timeline", headers=SYSTEM_KEY).status_code == 404

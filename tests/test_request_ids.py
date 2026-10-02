"""Stage 9: X-Request-ID propagation and error-body correlation."""

from __future__ import annotations

import re
import uuid

from fastapi.testclient import TestClient

from api.core.request_context import get_request_id, new_request_id, set_request_id

VALID_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _valid_request_id() -> str:
    return uuid.uuid4().hex[:16]


class TestRequestContext:
    def test_new_request_id_shape(self):
        rid = new_request_id()
        assert VALID_ID.match(rid)

    def test_set_and_get(self):
        token_val = "abc-123"
        set_request_id(token_val)
        assert get_request_id() == token_val


class TestRequestIdHeader:
    def test_every_response_carries_request_id(self, client: TestClient):
        for resp in (
            client.get("/health"),
            client.get("/api/v1/transactions/TXN-NOTHING", headers={"X-API-Key": "dev-system-key"}),
            client.get("/api/v1/auth/me"),
        ):
            assert VALID_ID.match(resp.headers["X-Request-ID"]), resp.headers.get("X-Request-ID")

    def test_valid_client_id_is_echoed(self, client: TestClient):
        rid = _valid_request_id()
        resp = client.get("/health", headers={"X-Request-ID": rid})
        assert resp.headers["X-Request-ID"] == rid

    def test_malformed_client_id_is_replaced(self, client: TestClient):
        bad_ids = [
            "bad id with spaces",
            "id/with/slashes",
            "x" * 65,  # over 64 chars
        ]
        for bad in bad_ids:
            resp = client.get("/health", headers={"X-Request-ID": bad})
            echoed = resp.headers["X-Request-ID"]
            assert echoed != bad
            assert VALID_ID.match(echoed)


class TestRequestIdInErrorBodies:
    def test_app_error_body_includes_request_id(self, client: TestClient):
        rid = _valid_request_id()
        resp = client.get(
            "/api/v1/transactions/TXN-NOTHING",
            headers={"X-API-Key": "dev-system-key", "X-Request-ID": rid},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["request_id"] == rid

    def test_unauthenticated_error_body_includes_request_id(self, client: TestClient):
        resp = client.get("/api/v1/auth/me", headers={"X-Request-ID": _valid_request_id()})
        assert resp.status_code == 401
        assert VALID_ID.match(resp.json()["error"]["request_id"])

    def test_validation_error_body_includes_request_id(self, client: TestClient):
        resp = client.post(
            "/api/v1/transaction/event",
            json={},
            headers={"X-API-Key": "dev-system-key", "X-Request-ID": _valid_request_id()},
        )
        assert resp.status_code == 422
        assert VALID_ID.match(resp.json()["error"]["request_id"])

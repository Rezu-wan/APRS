"""Stage 9: customer identity + ownership scoping on transaction reads.

Ownership matrix pinned here:
  * alice's key (customer_id "alice") can read HER transaction across
    transaction / timeline / reconstruction / explanation.
  * bob's key gets the SAME generic non-enumerating 403 on all of them
    (the body must not reveal whether the id exists).
  * dev-customer-key owns nothing → still 403 everywhere (regression parity
    with tests/test_api.py::TestAuthorization::test_customer_cannot_read).
  * CUSTOMER remains 403 on endpoints that were never opened: risk
    assessment, recovery (get/evaluate/process), stats.
"""

from __future__ import annotations

import uuid

from tests.conftest import CLEAN_FAILED_TX, CUSTOMER_KEY, SYSTEM_KEY, make_event

EVENT_URL = "/api/v1/transaction/event"
ALICE_KEY = {"X-API-Key": "dev-customer-alice"}  # customer_id "alice"
BOB_KEY = {"X-API-Key": "dev-customer-bob"}  # customer_id "bob"
GENERIC_403 = "transaction not accessible"


def _unique_id(prefix: str = "TXN-OWN") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _ingest_alice_tx(client) -> str:
    """Ingest a failed transaction owned by user_id 'alice'."""
    tid = _unique_id()
    resp = client.post(
        EVENT_URL,
        json=make_event(tid, user_id="alice", **CLEAN_FAILED_TX),
        headers=SYSTEM_KEY,
    )
    assert resp.status_code == 200, resp.text
    return tid


def _read_urls(tid: str) -> dict[str, tuple[str, str]]:
    return {
        "transaction": ("GET", f"/api/v1/transactions/{tid}"),
        "timeline": ("GET", f"/api/v1/transactions/{tid}/timeline"),
        "reconstruction": ("GET", f"/api/v1/transactions/{tid}/reconstruction"),
        "explanation": (
            "POST",
            "/api/v1/explanations/transaction",
        ),
    }


class TestOwnedAccess:
    def test_alice_can_read_her_transaction_everywhere(self, client):
        tid = _ingest_alice_tx(client)
        assert client.get(f"/api/v1/transactions/{tid}", headers=ALICE_KEY).status_code == 200
        assert (
            client.get(f"/api/v1/transactions/{tid}/timeline", headers=ALICE_KEY).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/v1/transactions/{tid}/reconstruction", headers=ALICE_KEY
            ).status_code
            == 200
        )

    def test_alice_can_explain_her_own_transaction(self, client):
        tid = _ingest_alice_tx(client)
        resp = client.post(
            "/api/v1/explanations/transaction",
            json={"transaction_id": tid, "language": "bn", "audience": "customer"},
            headers=ALICE_KEY,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # provider-agnostic assertions only — the explanation must exist
        assert body["explanation"].strip() != ""
        assert body["transaction_id"] == tid

    def test_customer_explanation_is_audience_locked(self, client):
        """Whatever audience/language a CUSTOMER asks for, the response is
        locked to customer/bn — no support/system framing."""
        tid = _ingest_alice_tx(client)
        resp = client.post(
            "/api/v1/explanations/transaction",
            json={"transaction_id": tid, "language": "en", "audience": "system"},
            headers=ALICE_KEY,
        )
        assert resp.status_code == 200
        assert resp.json()["audience"] == "customer"


class TestUnownedAccess:
    def test_bob_gets_non_enumerating_403_everywhere(self, client):
        tid = _ingest_alice_tx(client)
        for method, url in _read_urls(tid).values():
            if method == "GET":
                resp = client.get(url, headers=BOB_KEY)
            else:
                resp = client.post(
                    url,
                    json={"transaction_id": tid, "language": "bn", "audience": "customer"},
                    headers=BOB_KEY,
                )
            assert resp.status_code == 403, f"{url}: {resp.status_code}"
            # the 403 body must not reveal whether the id exists
            assert resp.json()["error"]["message"] == GENERIC_403

    def test_unknown_id_for_customer_is_403_not_404(self, client):
        """Non-enumeration: unknown ids look identical to unowned ones."""
        ghost = _unique_id("TXN-GHOST")
        for url in (
            f"/api/v1/transactions/{ghost}",
            f"/api/v1/transactions/{ghost}/timeline",
            f"/api/v1/transactions/{ghost}/reconstruction",
        ):
            resp = client.get(url, headers=BOB_KEY)
            assert resp.status_code == 403
            assert resp.json()["error"]["message"] == GENERIC_403

    def test_legacy_customer_key_still_403_regression(self, client):
        """dev-customer-key maps to identity 'dev-customer' which owns
        nothing — unowned access stays 403 (tests/test_api.py parity)."""
        tid = _ingest_alice_tx(client)
        assert client.get(f"/api/v1/transactions/{tid}", headers=CUSTOMER_KEY).status_code == 403
        assert (
            client.get(
                f"/api/v1/transactions/{tid}/timeline", headers=CUSTOMER_KEY
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/transactions/{tid}/reconstruction", headers=CUSTOMER_KEY
            ).status_code
            == 403
        )


class TestNeverOpenedEndpoints:
    def test_customer_still_403_on_staff_endpoints(self, client):
        """Risk assessment, recovery (get/evaluate/process) and stats remain
        staff-only — ownership is irrelevant there."""
        tid = _ingest_alice_tx(client)
        checks = [
            ("GET", f"/api/v1/transactions/{tid}/risk-assessment", None, ALICE_KEY),
            ("GET", f"/api/v1/transactions/{tid}/recovery", None, ALICE_KEY),
            ("POST", f"/api/v1/transactions/{tid}/recovery/evaluate", None, ALICE_KEY),
            ("POST", f"/api/v1/transactions/{tid}/recovery/process", None, ALICE_KEY),
            ("GET", "/api/v1/stats/summary", None, ALICE_KEY),
            ("GET", f"/api/v1/transactions/{tid}/risk-assessment", None, CUSTOMER_KEY),
            ("GET", "/api/v1/stats/summary", None, CUSTOMER_KEY),
        ]
        for method, url, _payload, key in checks:
            resp = (
                client.get(url, headers=key)
                if method == "GET"
                else client.post(url, json={}, headers=key)
            )
            assert resp.status_code == 403, f"{url} with {key}: {resp.status_code}"


class TestAuthMeIdentity:
    def test_customer_key_reports_bound_identity(self, client):
        body = client.get("/api/v1/auth/me", headers=ALICE_KEY).json()
        assert body["role"] == "CUSTOMER"
        assert body["customer_id"] == "alice"

    def test_staff_customer_id_is_null(self, client):
        body = client.get("/api/v1/auth/me", headers=SYSTEM_KEY).json()
        assert body["customer_id"] is None

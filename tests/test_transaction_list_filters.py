"""Customer-dashboard list/summary endpoints: role-scoped aggregates,
state and date-range filters on GET /transactions, and GET /transactions/summary.

Scoping contract (same rule as every transaction read): CUSTOMER callers are
ALWAYS scoped to their own user_id server-side; staff see everything.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi.testclient import TestClient

from tests.conftest import CLEAN_FAILED_TX, SYSTEM_KEY, make_event

EVENT_URL = "/api/v1/transaction/event"
ALICE_KEY = {"X-API-Key": "dev-customer-alice"}
BOB_KEY = {"X-API-Key": "dev-customer-bob"}


def _unique_id(prefix: str = "TXN-FLT") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _ingest_failed_tx(client: TestClient, user_id: str) -> str:
    """Ingest a failed transaction for `user_id`; returns the id."""
    tid = _unique_id()
    resp = client.post(
        EVENT_URL,
        json=make_event(tid, user_id=user_id, **CLEAN_FAILED_TX),
        headers=SYSTEM_KEY,
    )
    assert resp.status_code == 200, resp.text
    return tid


class TestSummaryScoping:
    def test_customer_summary_is_scoped_to_own_transactions(self, client):
        tid = _ingest_failed_tx(client, "alice")
        resp = client.get("/api/v1/transactions/summary", headers=ALICE_KEY)
        assert resp.status_code == 200
        body = resp.json()
        # CLEAN_FAILED_TX chains FAILED -> RISK_ASSESSED -> RECOVERY_PENDING
        assert body["by_state"].get("RECOVERY_PENDING", 0) >= 1
        assert body["total"] == sum(body["by_state"].values())
        # the freshly ingested transaction's amount is inside her totals
        assert float(body["amounts_by_currency"]["BDT"]) >= CLEAN_FAILED_TX["amount"]
        # and the ingested id really belongs to her scoped list
        ids = [
            item["transaction_id"]
            for item in client.get(
                "/api/v1/transactions?limit=200", headers=ALICE_KEY
            ).json()["items"]
        ]
        assert tid in ids

    def test_customer_summary_never_contains_other_customers(self, client):
        _ingest_failed_tx(client, "bob")
        alice = client.get("/api/v1/transactions/summary", headers=ALICE_KEY).json()
        bob = client.get("/api/v1/transactions/summary", headers=BOB_KEY).json()
        alice_items = [
            item["transaction_id"]
            for item in client.get(
                "/api/v1/transactions?limit=200", headers=ALICE_KEY
            ).json()["items"]
        ]
        bob_items = [
            item["transaction_id"]
            for item in client.get(
                "/api/v1/transactions?limit=200", headers=BOB_KEY
            ).json()["items"]
        ]
        assert not set(alice_items) & set(bob_items)
        assert alice["total"] == len(alice_items)
        assert bob["total"] == len(bob_items)

    def test_staff_summary_covers_every_customer(self, client):
        alice = client.get("/api/v1/transactions/summary", headers=ALICE_KEY).json()
        bob = client.get("/api/v1/transactions/summary", headers=BOB_KEY).json()
        staff = client.get("/api/v1/transactions/summary", headers=SYSTEM_KEY).json()
        assert staff["total"] >= alice["total"] + bob["total"]


class TestListFilters:
    def test_state_filter_matches_only_that_state(self, client):
        tid = _ingest_failed_tx(client, "alice")
        tx = client.get(f"/api/v1/transactions/{tid}", headers=SYSTEM_KEY).json()
        state = tx["current_state"]
        resp = client.get(
            f"/api/v1/transactions?state={state}&limit=200", headers=ALICE_KEY
        )
        assert resp.status_code == 200
        body = resp.json()
        assert all(item["current_state"] == state for item in body["items"])
        assert tid in [item["transaction_id"] for item in body["items"]]

    def test_state_filter_rejects_unknown_state(self, client):
        resp = client.get("/api/v1/transactions?state=BOGUS", headers=ALICE_KEY)
        assert resp.status_code == 422
        assert "RECOVERY_PENDING" in resp.json()["error"]["message"]

    def test_date_range_filter(self, client):
        _ingest_failed_tx(client, "alice")
        now = datetime.now(timezone.utc)

        def param(name: str, delta: timedelta) -> str:
            # percent-encoded ISO datetime ('+' would parse as a space)
            return f"{name}={quote((now + delta).isoformat())}"

        inside_from = param("date_from", -timedelta(hours=1))
        inside_to = param("date_to", timedelta(hours=1))
        inside = client.get(
            f"/api/v1/transactions?{inside_from}&{inside_to}&limit=200",
            headers=ALICE_KEY,
        )
        assert inside.status_code == 200
        assert inside.json()["total"] >= 1

        # a window entirely in the past must not include the fresh transaction
        past_from = param("date_from", -timedelta(days=30))
        past_to = param("date_to", -timedelta(days=29))
        past = client.get(
            f"/api/v1/transactions?{past_from}&{past_to}&limit=200",
            headers=ALICE_KEY,
        )
        assert past.status_code == 200
        assert past.json()["total"] == 0

    def test_filters_combine_with_pagination(self, client):
        resp = client.get(
            "/api/v1/transactions?state=RECOVERY_PENDING&limit=1&offset=1",
            headers=ALICE_KEY,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["limit"] == 1 and body["offset"] == 1
        assert len(body["items"]) <= 1

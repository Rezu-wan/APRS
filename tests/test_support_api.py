"""Support workspace API tests.

Covers the customer-care contract end to end against the real app:

* roles — SUPPORT reads + case writes; CUSTOMER 403 everywhere; the engine
  (recovery process) stays SYSTEM/ADMIN;
* overview — real aggregates only (zeros stay zeros, no fabrication);
* customer search — registry fields (name/email/phone/id), transaction-id
  resolution, and honest fallbacks for registry-less users;
* customer profile — 404 for unknown customers, aggregates from real
  transactions;
* transaction list — filters (q/state/user), ordering;
* case lifecycle — create (customer derived from the transaction), legal
  transitions, ILLEGAL transition rejection, notes thread, audit rows,
  bus publish (the SSE feed's source).
"""

from __future__ import annotations

import uuid

import pytest

from api.db.database import SessionLocal
from api.db.models import Account, Customer
from tests.conftest import ADMIN_KEY, CLEAN_FAILED_TX, CUSTOMER_KEY, SUPPORT_KEY, SYSTEM_KEY, make_event


def _seed_registry_customer(**overrides) -> Customer:
    """Insert one synthetic registry customer directly (the test DB has no
    dataset loaded — loaders are for real environments, tests pin their own
    fixtures)."""
    suffix = uuid.uuid4().hex[:8]
    customer = Customer(
        customer_id=overrides.get("customer_id", f"CUST-TEST-{suffix}"),
        full_name=overrides.get("full_name", "Test Nur Rahim"),
        email=overrides.get("email", f"test.{suffix}@example.com"),
        phone=overrides.get("phone", f"+8801700{suffix[:6]}"),
        country="BD",
        status="active",
        segment="retail",
        risk_profile="LOW",
    )
    db = SessionLocal()
    try:
        db.add(customer)
        db.add(
            Account(
                account_id=f"ACCT-TEST-{suffix}",
                customer_id=customer.customer_id,
                account_type="savings",
                currency="BDT",
                is_primary=True,
            )
        )
        db.commit()
    finally:
        db.close()
    return customer


def _ingest_failed_transaction(client, transaction_id: str, user_id: str = "USER-SUP-1"):
    resp = client.post(
        "/api/v1/transaction/event",
        json=make_event(transaction_id, user_id=user_id, **CLEAN_FAILED_TX),
        headers=SYSTEM_KEY,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _create_case(client, transaction_id: str, subject: str = "Customer reports a failed payment", **overrides):
    payload = {"transaction_id": transaction_id, "subject": subject, **overrides}
    return client.post("/api/v1/support/cases", json=payload, headers=SUPPORT_KEY)


# --------------------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------------------



class TestSupportRoles:
    def test_customer_role_is_forbidden_everywhere_in_support(self, client):
        for method, path in (
            ("get", "/api/v1/support/overview"),
            ("get", "/api/v1/support/customers/search?q=x"),
            ("get", "/api/v1/support/customers/CUST-000001"),
            ("get", "/api/v1/support/transactions"),
            ("get", "/api/v1/support/cases"),
            ("post", "/api/v1/support/cases"),
            ("post", "/api/v1/support/stream-ticket"),
        ):
            resp = client.request(method, path, headers=CUSTOMER_KEY)
            assert resp.status_code == 403, (method, path, resp.status_code)

    def test_support_role_can_read_overview(self, client):
        resp = client.get("/api/v1/support/overview", headers=SUPPORT_KEY)
        assert resp.status_code == 200
        body = resp.json()
        # honest zeros on an empty test DB — no fabricated metrics
        assert body["cases_by_status"]["open"] == 0
        assert isinstance(body["needs_attention"], list)
        assert isinstance(body["recent_activity"], list)

    def test_support_cannot_run_recovery_but_admin_can(self, client):
        tx = _ingest_failed_transaction(client, f"TXN-{uuid.uuid4().hex[:10]}")
        tid = tx["transaction_id"]
        resp = client.post(
            f"/api/v1/transactions/{tid}/recovery/process", headers=SUPPORT_KEY
        )
        assert resp.status_code == 403
        resp = client.post(
            f"/api/v1/transactions/{tid}/recovery/process", headers=ADMIN_KEY
        )
        assert resp.status_code == 200

    def test_support_can_read_engine_evidence_it_does_not_own(self, client):
        """SUPPORT stays a read-wide role: engine evidence for a transaction
        is readable, only engine ACTIONS are admin-gated."""
        tx = _ingest_failed_transaction(client, f"TXN-{uuid.uuid4().hex[:10]}")
        tid = tx["transaction_id"]
        assert client.get(f"/api/v1/transactions/{tid}", headers=SUPPORT_KEY).status_code == 200
        assert client.get(f"/api/v1/transactions/{tid}/timeline", headers=SUPPORT_KEY).status_code == 200


# --------------------------------------------------------------------------------------
# Customer search + profile
# --------------------------------------------------------------------------------------


class TestCustomerSearchAndProfile:
    def test_search_by_name_email_phone_and_id(self, client):
        customer = _seed_registry_customer()
        queries = [customer.customer_id, customer.email, customer.phone, "Nur Rahim"]
        for query in queries:
            resp = client.get(
                "/api/v1/support/customers/search", params={"q": query}, headers=SUPPORT_KEY
            )
            assert resp.status_code == 200, (query, resp.text)
            ids = [r["customer_id"] for r in resp.json()["results"]]
            assert customer.customer_id in ids, query

    def test_search_by_transaction_id_resolves_customer(self, client):
        customer = _seed_registry_customer()
        tid = f"TXN-{uuid.uuid4().hex[:10]}"
        _ingest_failed_transaction(client, tid, user_id=customer.customer_id)
        resp = client.get(
            "/api/v1/support/customers/search", params={"q": tid}, headers=SUPPORT_KEY
        )
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) >= 1
        assert results[0]["customer_id"] == customer.customer_id
        assert results[0]["dataset_known"] is True

    def test_search_no_hits_is_honest_empty(self, client):
        resp = client.get(
            "/api/v1/support/customers/search", params={"q": "zzz-no-such-customer"}, headers=SUPPORT_KEY
        )
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_profile_returns_registry_row_with_accounts(self, client):
        customer = _seed_registry_customer()
        _ingest_failed_transaction(
            client, f"TXN-{uuid.uuid4().hex[:10]}", user_id=customer.customer_id
        )
        resp = client.get(f"/api/v1/support/customers/{customer.customer_id}", headers=SUPPORT_KEY)
        assert resp.status_code == 200
        body = resp.json()
        assert body["customer"]["full_name"] == "Test Nur Rahim"
        assert body["dataset_known"] is True
        assert len(body["accounts"]) == 1
        assert body["aggregate"]["transaction_count"] == 1
        assert body["aggregate"]["failed_count"] == 1
        # the support contract exposes customer_id (ORM stores user_id)
        assert body["recent_transactions"][0]["customer_id"] == customer.customer_id

    def test_profile_404_for_completely_unknown_customer(self, client):
        resp = client.get("/api/v1/support/customers/NOPE-123", headers=SUPPORT_KEY)
        assert resp.status_code == 404

    def test_profile_honest_fallback_for_ingest_only_user(self, client):
        user_id = f"USER-ONLY-{uuid.uuid4().hex[:6]}"
        _ingest_failed_transaction(client, f"TXN-{uuid.uuid4().hex[:10]}", user_id=user_id)
        resp = client.get(f"/api/v1/support/customers/{user_id}", headers=SUPPORT_KEY)
        assert resp.status_code == 200
        body = resp.json()
        assert body["dataset_known"] is False
        assert body["customer"]["email"] == ""  # unknown stays unknown
        assert body["aggregate"]["transaction_count"] == 1


# --------------------------------------------------------------------------------------
# Transaction list
# --------------------------------------------------------------------------------------



class TestTransactionList:
    def test_list_filters_by_state_and_q(self, client):
        tid = f"TXN-{uuid.uuid4().hex[:10]}"
        _ingest_failed_transaction(client, tid, user_id="USER-LIST-1")

        resp = client.get(
            "/api/v1/support/transactions", params={"q": tid}, headers=SUPPORT_KEY
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["transactions"][0]["transaction_id"] == tid

        resp = client.get(
            "/api/v1/support/transactions", params={"user_id": "USER-LIST-1"}, headers=SUPPORT_KEY
        )
        assert any(t["transaction_id"] == tid for t in resp.json()["transactions"])

    def test_list_is_newest_first(self, client):
        resp = client.get("/api/v1/support/transactions", params={"limit": 5}, headers=SUPPORT_KEY)
        stamps = [t["timestamp"] for t in resp.json()["transactions"]]
        assert stamps == sorted(stamps, reverse=True)


# --------------------------------------------------------------------------------------
# Case lifecycle
# --------------------------------------------------------------------------------------



class TestCaseLifecycle:
    def test_create_case_derives_customer_and_audits(self, client):
        tid = f"TXN-{uuid.uuid4().hex[:10]}"
        _ingest_failed_transaction(client, tid, user_id="USER-CASE-1")
        resp = _create_case(client, tid, priority="HIGH")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "OPEN"
        assert body["customer_id"] == "USER-CASE-1"
        assert body["created_by"] == "api_key_support"
        assert body["notes"] == []

        # audit row exists with the key NAME (never the raw key)
        audit = client.get(
            "/api/v1/audit", params={"action": "SUPPORT_CASE_CREATED"}, headers=SYSTEM_KEY
        )
        assert audit.status_code == 200
        actions = [a["action"] for a in audit.json()["rows"]]
        assert "SUPPORT_CASE_CREATED" in actions

    def test_create_case_unknown_transaction_404(self, client):
        resp = _create_case(client, "TXN-DOES-NOT-EXIST")
        assert resp.status_code == 404

    def test_legal_transition_path_with_notes(self, client):
        tid = f"TXN-{uuid.uuid4().hex[:10]}"
        _ingest_failed_transaction(client, tid)
        case_id = _create_case(client, tid).json()["case_id"]

        resp = client.patch(
            f"/api/v1/support/cases/{case_id}",
            json={"status": "IN_PROGRESS", "note": "investigating", "assignee": "agent-1"},
            headers=SUPPORT_KEY,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "IN_PROGRESS"
        assert body["assignee"] == "agent-1"
        assert body["notes"][-1]["text"] == "investigating"
        assert body["notes"][-1]["role"] == "SUPPORT"

        resp = client.patch(
            f"/api/v1/support/cases/{case_id}",
            json={"status": "WAITING_FOR_CUSTOMER"},
            headers=SUPPORT_KEY,
        )
        assert resp.json()["status"] == "WAITING_FOR_CUSTOMER"

        resp = client.patch(
            f"/api/v1/support/cases/{case_id}",
            json={"status": "RESOLVED", "note": "refund issued"},
            headers=SUPPORT_KEY,
        )
        body = resp.json()
        assert body["status"] == "RESOLVED"
        assert body["resolved_at"] is not None

        # reopen is legal; CLOSE is terminal
        assert client.patch(
            f"/api/v1/support/cases/{case_id}", json={"status": "OPEN"}, headers=SUPPORT_KEY
        ).json()["status"] == "OPEN"
        assert client.patch(
            f"/api/v1/support/cases/{case_id}", json={"status": "CLOSED"}, headers=SUPPORT_KEY
        ).json()["status"] == "CLOSED"
        assert (
            client.patch(
                f"/api/v1/support/cases/{case_id}", json={"status": "OPEN"}, headers=SUPPORT_KEY
            ).status_code
            == 400
        )

    def test_illegal_transition_rejected(self, client):
        tid = f"TXN-{uuid.uuid4().hex[:10]}"
        _ingest_failed_transaction(client, tid)
        case_id = _create_case(client, tid).json()["case_id"]
        resp = client.patch(
            f"/api/v1/support/cases/{case_id}", json={"status": "IN_PROGRESS"}, headers=SUPPORT_KEY
        )
        assert resp.status_code == 200
        # ESCALATED -> WAITING_FOR_CUSTOMER is not in the transition map
        client.patch(
            f"/api/v1/support/cases/{case_id}", json={"status": "ESCALATED"}, headers=SUPPORT_KEY
        )
        resp = client.patch(
            f"/api/v1/support/cases/{case_id}",
            json={"status": "WAITING_FOR_CUSTOMER"},
            headers=SUPPORT_KEY,
        )
        assert resp.status_code == 400

    def test_admin_can_act_on_cases_too(self, client):
        tid = f"TXN-{uuid.uuid4().hex[:10]}"
        _ingest_failed_transaction(client, tid)
        case_id = _create_case(client, tid).json()["case_id"]
        resp = client.patch(
            f"/api/v1/support/cases/{case_id}", json={"status": "ESCALATED"}, headers=ADMIN_KEY
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ESCALATED"

    def test_case_create_publishes_to_bus(self, client):
        from api.services.eventbus.in_memory import get_event_bus

        bus = get_event_bus()
        before = len(bus.replay())
        tid = f"TXN-{uuid.uuid4().hex[:10]}"
        _ingest_failed_transaction(client, tid)
        _create_case(client, tid)
        after = bus.replay()
        assert len(after) > before
        assert any(e.event_type == "SUPPORT_CASE_CREATED" for e in after)


# --------------------------------------------------------------------------------------
# Stream tickets (SSE auth)
# --------------------------------------------------------------------------------------



class TestStreamTickets:
    """Ticket issuance + single-use redemption semantics. The live SSE flow
    itself is exercised by the browser E2E (an infinite event-stream does not
    fit the TestClient portal — it never completes)."""

    def test_ticket_is_bound_to_caller_and_single_use(self, client):
        resp = client.post("/api/v1/support/stream-ticket", headers=SUPPORT_KEY)
        assert resp.status_code == 200
        ticket = resp.json()["ticket"]
        assert resp.json()["expires_in_seconds"] > 0

        from api.services.support_stream import get_ticket_store

        store = get_ticket_store()
        redeemed = store.redeem(ticket)
        assert redeemed is not None
        assert redeemed == ("api_key_support", "SUPPORT")
        assert store.redeem(ticket) is None  # single use

    def test_garbage_ticket_401(self, client):
        resp = client.get("/api/v1/support/stream", params={"ticket": "x" * 32}, headers={})
        assert resp.status_code == 401

    def test_ticket_requires_staff_role(self, client):
        resp = client.post("/api/v1/support/stream-ticket", headers=CUSTOMER_KEY)
        assert resp.status_code == 403

    def test_expired_ticket_returns_none(self, client):
        from api.services.support_stream import StreamTicketStore

        store = StreamTicketStore(ttl_seconds=-1)
        ticket = store.issue("api_key_support", "SUPPORT")
        assert store.redeem(ticket) is None


# --------------------------------------------------------------------------------------
# Overview reflects real state
# --------------------------------------------------------------------------------------



class TestOverviewReflectsState:
    def test_case_counts_follow_the_queue(self, client):
        tid = f"TXN-{uuid.uuid4().hex[:10]}"
        _ingest_failed_transaction(client, tid)
        case_id = _create_case(client, tid).json()["case_id"]

        before = client.get("/api/v1/support/overview", headers=SUPPORT_KEY).json()
        assert before["cases_by_status"]["open"] >= 1

        client.patch(
            f"/api/v1/support/cases/{case_id}", json={"status": "RESOLVED"}, headers=SUPPORT_KEY
        )
        after = client.get("/api/v1/support/overview", headers=SUPPORT_KEY).json()
        assert after["cases_by_status"]["open"] == before["cases_by_status"]["open"] - 1
        assert after["cases_by_status"]["resolved"] >= 1

    def test_recent_activity_carries_customer_names_when_known(self, client):
        body = client.get("/api/v1/support/overview", headers=SUPPORT_KEY).json()
        assert len(body["recent_activity"]) > 0
        top = body["recent_activity"][0]
        assert {"transaction_id", "customer_id", "amount", "current_state"} <= set(top)

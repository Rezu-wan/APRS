"""Customer problem reports: filing, idempotent replay, ownership scoping,
twin observation, audit row — and the new transaction LIST endpoint with
CUSTOMER auto-scoping. Reports are EVIDENCE ONLY: nothing here may change a
transaction's state."""

from __future__ import annotations

import uuid

from tests.conftest import ADMIN_KEY, CLEAN_FAILED_TX, CUSTOMER_KEY, SUPPORT_KEY, SYSTEM_KEY, make_event

REPORT_URL = "/api/v1/transactions/{tx}/customer-report"
LIST_URL = "/api/v1/transactions"

# the legacy dev-customer-key maps to identity "dev-customer" (conftest)
CUSTOMER_ID = "dev-customer"


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _create_tx(client, tx_id: str, user_id: str = CUSTOMER_ID) -> str:
    resp = client.post(
        "/api/v1/transaction/event",
        json=make_event(tx_id, user_id=user_id, **CLEAN_FAILED_TX),
        headers=SYSTEM_KEY,
    )
    assert resp.status_code == 200, resp.text
    return tx_id


class TestFileReport:
    def test_customer_files_report_on_own_transaction(self, client):
        tx = _create_tx(client, _unique("TXN-RPT"))
        resp = client.post(
            REPORT_URL.format(tx=tx),
            json={
                "problem_type": "DOUBLE_CHARGED",
                "stage": "CARD_DEBIT",
                "description": "my account was debited twice for one payment",
            },
            headers=CUSTOMER_KEY,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["already_reported"] is False
        assert body["digital_twin_event_recorded"] is True
        report = body["report"]
        assert report["transaction_id"] == tx
        assert report["customer_id"] == CUSTOMER_ID
        assert report["problem_type"] == "DOUBLE_CHARGED"
        assert report["stage"] == "CARD_DEBIT"
        assert report["status"] == "OPEN"
        assert report["report_id"]

        # the twin observation is on the timeline (prev == new: no transition)
        timeline = client.get(
            f"/api/v1/transactions/{tx}/timeline", headers=SYSTEM_KEY
        ).json()
        types = [e["event_type"] for e in timeline["events"]]
        assert types.count("CUSTOMER_REPORT_FILED") == 1
        filed = next(e for e in timeline["events"] if e["event_type"] == "CUSTOMER_REPORT_FILED")
        assert filed["previous_state"] == filed["new_state"]

        # the transaction state is untouched by the report
        tx_body = client.get(f"/api/v1/transactions/{tx}", headers=SYSTEM_KEY).json()
        assert tx_body["current_state"] == "RECOVERY_PENDING"  # FAILED → assessed chain

    def test_refile_replays_stored_report(self, client):
        tx = _create_tx(client, _unique("TXN-RPT"))
        first = client.post(
            REPORT_URL.format(tx=tx),
            json={"problem_type": "PAYMENT_FAILED", "stage": "GATEWAY", "description": "x"},
            headers=CUSTOMER_KEY,
        ).json()
        second = client.post(
            REPORT_URL.format(tx=tx),
            json={"problem_type": "OTHER", "stage": "NOT_SURE", "description": "changed my mind"},
            headers=CUSTOMER_KEY,
        )
        assert second.status_code == 200
        body = second.json()
        assert body["already_reported"] is True
        assert body["digital_twin_event_recorded"] is False
        # the ORIGINAL report is replayed — updates are not possible by re-filing
        assert body["report"]["report_id"] == first["report"]["report_id"]
        assert body["report"]["problem_type"] == "PAYMENT_FAILED"

    def test_customer_cannot_report_foreign_transaction(self, client):
        foreign = _create_tx(client, _unique("TXN-RPT"), user_id="someone-else")
        resp = client.post(
            REPORT_URL.format(tx=foreign),
            json={"problem_type": "OTHER", "stage": "NOT_SURE"},
            headers=CUSTOMER_KEY,
        )
        assert resp.status_code == 403
        # non-enumerating: same shape as for unknown ids
        unknown = client.post(
            REPORT_URL.format(tx=_unique("TXN-UNKNOWN")),
            json={"problem_type": "OTHER", "stage": "NOT_SURE"},
            headers=CUSTOMER_KEY,
        )
        assert unknown.status_code == 403
        assert resp.json()["error"]["message"] == unknown.json()["error"]["message"]

    def test_customer_cannot_report_unknown_transaction(self, client):
        resp = client.post(
            REPORT_URL.format(tx=_unique("TXN-NONE")),
            json={"problem_type": "OTHER", "stage": "NOT_SURE"},
            headers=CUSTOMER_KEY,
        )
        assert resp.status_code == 403

    def test_staff_can_file_and_support_cannot(self, client):
        tx = _create_tx(client, _unique("TXN-RPT"), user_id="U-STAFF-CASE")
        ok = client.post(
            REPORT_URL.format(tx=tx),
            json={"problem_type": "MONEY_NOT_RECEIVED", "stage": "SETTLEMENT"},
            headers=ADMIN_KEY,
        )
        assert ok.status_code == 200
        assert ok.json()["report"]["customer_id"].startswith("staff/")
        denied = client.post(
            REPORT_URL.format(tx=tx),
            json={"problem_type": "OTHER", "stage": "NOT_SURE"},
            headers=SUPPORT_KEY,
        )
        assert denied.status_code == 403

    def test_invalid_vocabulary_rejected(self, client):
        tx = _create_tx(client, _unique("TXN-RPT"))
        resp = client.post(
            REPORT_URL.format(tx=tx),
            json={"problem_type": "SOMETHING_ELSE", "stage": "NOT_SURE"},
            headers=CUSTOMER_KEY,
        )
        assert resp.status_code == 422
        resp = client.post(
            REPORT_URL.format(tx=tx),
            json={"problem_type": "OTHER", "stage": "MOON"},
            headers=CUSTOMER_KEY,
        )
        assert resp.status_code == 422


class TestReadReport:
    def test_customer_reads_own_report(self, client):
        tx = _create_tx(client, _unique("TXN-RPT"))
        client.post(
            REPORT_URL.format(tx=tx),
            json={"problem_type": "UNAUTHORIZED", "stage": "CARD_DEBIT"},
            headers=CUSTOMER_KEY,
        )
        resp = client.get(REPORT_URL.format(tx=tx), headers=CUSTOMER_KEY)
        assert resp.status_code == 200
        assert resp.json()["problem_type"] == "UNAUTHORIZED"

    def test_customer_gets_404_without_report_but_403_on_foreign(self, client):
        own_no_report = _create_tx(client, _unique("TXN-RPT"))
        assert (
            client.get(REPORT_URL.format(tx=own_no_report), headers=CUSTOMER_KEY).status_code
            == 404
        )
        foreign = _create_tx(client, _unique("TXN-RPT"), user_id="other-user")
        assert (
            client.get(REPORT_URL.format(tx=foreign), headers=CUSTOMER_KEY).status_code
            == 403
        )

    def test_staff_reads_latest_report(self, client):
        tx = _create_tx(client, _unique("TXN-RPT"), user_id="U-STAFF-READ")
        filed = client.post(
            REPORT_URL.format(tx=tx),
            json={"problem_type": "OTHER", "stage": "GATEWAY"},
            headers=ADMIN_KEY,
        ).json()
        resp = client.get(REPORT_URL.format(tx=tx), headers=SYSTEM_KEY)
        assert resp.status_code == 200
        assert resp.json()["report_id"] == filed["report"]["report_id"]


class TestAuditRow:
    def test_filing_writes_customer_report_audit_row(self, client):
        tx = _create_tx(client, _unique("TXN-RPT"))
        report_id = client.post(
            REPORT_URL.format(tx=tx),
            json={"problem_type": "OTHER", "stage": "NOT_SURE"},
            headers=CUSTOMER_KEY,
        ).json()["report"]["report_id"]
        audit = client.get("/api/v1/audit?limit=200", headers=ADMIN_KEY).json()
        rows = audit["rows"]
        assert any(
            r.get("action") == "CUSTOMER_REPORT" and r.get("resource_id") == tx
            for r in rows
        ), "expected a CUSTOMER_REPORT audit row"


class TestTransactionList:
    def test_customer_sees_only_own_transactions(self, client):
        mine = [_create_tx(client, _unique("TXN-LIST")) for _ in range(3)]
        _create_tx(client, _unique("TXN-LIST"), user_id="someone-else")

        resp = client.get(LIST_URL, headers=CUSTOMER_KEY)
        assert resp.status_code == 200
        body = resp.json()
        ids = [t["transaction_id"] for t in body["items"]]
        assert all(i in ids for i in mine)
        assert all(t["user_id"] == CUSTOMER_ID for t in body["items"])

        # an ignored ?user_id= param cannot widen the customer's scope
        resp = client.get(f"{LIST_URL}?user_id=someone-else", headers=CUSTOMER_KEY)
        assert all(t["user_id"] == CUSTOMER_ID for t in resp.json()["items"])

    def test_staff_sees_all_and_can_filter(self, client):
        target_user = _unique("U-FILTER")
        tx = _create_tx(client, _unique("TXN-LIST"), user_id=target_user)
        resp = client.get(f"{LIST_URL}?user_id={target_user}", headers=SYSTEM_KEY)
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items and all(t["user_id"] == target_user for t in items)
        assert tx in [t["transaction_id"] for t in items]

    def test_list_pagination_contract(self, client):
        resp = client.get(f"{LIST_URL}?limit=2&offset=0", headers=ADMIN_KEY)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) <= 2
        assert body["limit"] == 2
        assert body["total"] >= len(body["items"])

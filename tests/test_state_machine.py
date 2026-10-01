"""State machine behaviour: legal chains, illegal transitions, retry path."""

from __future__ import annotations

import uuid

from tests.conftest import CLEAN_FAILED_TX, SYSTEM_KEY, make_event

URL = "/api/v1/transaction/event"


def _unique_id(prefix: str = "TXN-STATE") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _ingest(client, payload):
    return client.post(URL, json=payload, headers=SYSTEM_KEY)


class TestLegalChains:
    def test_single_event_walks_full_chain(self, client):
        """INITIATED -> PROCESSING -> FAILED -> RISK_ASSESSED -> RECOVERY_PENDING
        must happen in ONE event call, with one twin event per hop."""
        tid = _unique_id()
        resp = _ingest(client, make_event(tid, **CLEAN_FAILED_TX))
        assert resp.status_code == 200
        body = resp.json()
        assert body["current_state"] == "RECOVERY_PENDING"

        event_types = [e["event_type"] for e in body["new_events"]]
        assert event_types == [
            "TRANSACTION_CREATED",
            "PAYMENT_PROCESSING",
            "PAYMENT_FAILED",
            "ML_RISK_ASSESSED",
            "RECOVERY_CHECKED",
        ]

    def test_stalled_to_processing_retry_is_legal_in_state_machine(self):
        """STALLED -> PROCESSING retry must be a legal transition (unit level)."""
        from api.core.state_machine import TransactionState, transition_path
        path = transition_path(TransactionState.STALLED, TransactionState.PROCESSING)
        assert path == [TransactionState.STALLED, TransactionState.PROCESSING]

    def test_stalled_rests_and_accepts_retry(self, client):
        """A STALLED transaction RESTS in STALLED (no auto-assessment — a stall
        may resolve), and accepts a PROCESSING retry event."""
        tid = _unique_id()
        resp = _ingest(client, make_event(
            tid, amount=100.00, gateway_latency_ms=4000, retry_count=1,
            network_quality="Poor", previous_failures=1, account_age_days=200,
            status="STALLED",
        ))
        assert resp.status_code == 200
        body = resp.json()
        assert body["current_state"] == "STALLED"
        assert body["ml_assessment"] is None  # not assessed while it may resolve

        retry = _ingest(client, make_event(tid, amount=100.00, status="PROCESSING"))
        assert retry.status_code == 200
        body = retry.json()
        assert body["created"] is False
        assert body["current_state"] == "PROCESSING"
        hops = [(e["event_type"], e["new_state"]) for e in body["new_events"]]
        assert ("PAYMENT_PROCESSING", "PROCESSING") in hops


class TestIllegalTransitions:
    def test_success_event_on_manual_review_tx_is_400(self, client):
        tid = _unique_id()
        # risky transaction -> MANUAL_REVIEW after release-limit
        resp = _ingest(client, make_event(
            tid, amount=1250.00, gateway_latency_ms=2800, retry_count=3,
            network_quality="Poor", previous_failures=2, account_age_days=40,
            status="FAILED", failure_reason="Timeout",
        ))
        assert resp.status_code == 200
        rel = client.post("/api/v1/recovery/release-limit",
                          json={"transaction_id": tid}, headers=SYSTEM_KEY)
        assert rel.status_code == 200
        assert rel.json()["decision"] == "MANUAL_REVIEW"

        bad = _ingest(client, make_event(tid, amount=1250.00, status="SUCCESS"))
        assert bad.status_code == 400

    def test_event_on_terminal_success_tx_is_400(self, client):
        tid = _unique_id()
        assert _ingest(client, make_event(tid, amount=50.00, status="SUCCESS")
                       ).json()["current_state"] == "SUCCESS"
        assert _ingest(client, make_event(tid, amount=50.00, status="FAILED",
                                          failure_reason="Timeout")
                       ).status_code == 400

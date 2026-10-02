"""Stage 9 state-machine integrity (spec §12): the Stage-8-adjacent illegal
transitions must be rejected END-TO-END through the API (HTTP 400 with
INVALID_STATE_TRANSITION), proving the central validator guards every
write path — plus the exact-pair unit assertions."""

from __future__ import annotations

import uuid

from api.core.state_machine import TransactionState, validate_transition
from api.core.exceptions import InvalidStateTransitionError
from tests.conftest import CLEAN_FAILED_TX, SYSTEM_KEY, make_event

URL = "/api/v1/transaction/event"


def _unique_id(prefix: str = "TXN-SI") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _ingest(client, payload):
    return client.post(URL, json=payload, headers=SYSTEM_KEY)


class TestIllegalPairsAtUnitLevel:
    """The exact pinned pairs must be rejected by the central validator."""

    def test_limit_released_to_failed_is_illegal(self):
        import pytest
        with pytest.raises(InvalidStateTransitionError):
            validate_transition(TransactionState.LIMIT_RELEASED, TransactionState.FAILED)

    def test_success_to_processing_is_illegal(self):
        import pytest
        with pytest.raises(InvalidStateTransitionError):
            validate_transition(TransactionState.SUCCESS, TransactionState.PROCESSING)

    def test_initiated_to_success_is_illegal(self):
        import pytest
        with pytest.raises(InvalidStateTransitionError):
            validate_transition(TransactionState.INITIATED, TransactionState.SUCCESS)

    def test_recovery_pending_to_initiated_is_illegal(self):
        import pytest
        with pytest.raises(InvalidStateTransitionError):
            validate_transition(
                TransactionState.RECOVERY_PENDING, TransactionState.INITIATED)


class TestIllegalTransitionsEndToEnd:
    def test_success_tx_cannot_go_back_to_processing(self, client):
        """SUCCESS is terminal — a late PROCESSING event is 400, state unchanged."""
        tid = _unique_id()
        resp = _ingest(client, make_event(tid, amount=50.00, status="SUCCESS"))
        assert resp.status_code == 200
        assert resp.json()["current_state"] == "SUCCESS"

        illegal = _ingest(client, make_event(tid, amount=50.00, status="PROCESSING"))
        assert illegal.status_code == 400
        assert illegal.json()["error"]["code"] == "INVALID_STATE_TRANSITION"

        fetch = client.get(f"/api/v1/transactions/{tid}", headers=SYSTEM_KEY)
        assert fetch.json()["current_state"] == "SUCCESS"

    def test_limit_released_tx_cannot_fail(self, client):
        """LIMIT_RELEASED is terminal — a FAILED event is 400, state unchanged."""
        tid = _unique_id()
        resp = _ingest(client, make_event(tid, **CLEAN_FAILED_TX))
        assert resp.status_code == 200
        assert resp.json()["current_state"] == "RECOVERY_PENDING"

        rel = client.post(
            "/api/v1/recovery/release-limit", json={"transaction_id": tid},
            headers=SYSTEM_KEY,
        )
        assert rel.status_code == 200

        fetch = client.get(f"/api/v1/transactions/{tid}", headers=SYSTEM_KEY)
        assert fetch.json()["current_state"] == "LIMIT_RELEASED"

        failed_payload = dict(CLEAN_FAILED_TX, failure_reason="Gateway Error")
        illegal = _ingest(client, make_event(tid, **failed_payload))
        assert illegal.status_code == 400
        assert illegal.json()["error"]["code"] == "INVALID_STATE_TRANSITION"

        fetch = client.get(f"/api/v1/transactions/{tid}", headers=SYSTEM_KEY)
        assert fetch.json()["current_state"] == "LIMIT_RELEASED"

    def test_initiated_to_success_single_hop_is_illegal_chain_walk_is_legal(self, client):
        """INITIATED -> SUCCESS as a SINGLE HOP is rejected by the central
        validator (unit level); end-to-end, a SUCCESS event on an INITIATED
        transaction is a legal CHAIN WALK (INITIATED -> PROCESSING -> SUCCESS,
        one twin event per hop) — every hop still goes through
        validate_transition, which is the integrity guarantee being pinned."""
        tid = _unique_id()
        # seed a transaction parked in INITIATED (the implicit create state)
        from api.db.database import SessionLocal
        from api.db.models import Transaction
        db = SessionLocal()
        try:
            db.add(Transaction(transaction_id=tid,
                               user_id="USER-001", merchant_id="MERCHANT-001",
                               current_state=TransactionState.INITIATED,
                               amount=50.00, currency="BDT"))
            db.commit()
        finally:
            db.close()

        walk = _ingest(client, make_event(tid, amount=50.00, status="SUCCESS"))
        assert walk.status_code == 200
        body = walk.json()
        assert body["current_state"] == "SUCCESS"
        hops = [e["new_state"] for e in body["new_events"]]
        assert hops == ["PROCESSING", "SUCCESS"]  # never a direct INITIATED jump

        # once at SUCCESS (terminal), the single-hop illegality bites e2e:
        another = _ingest(client, make_event(tid, amount=50.00, status="PROCESSING"))
        assert another.status_code == 400
        assert another.json()["error"]["code"] == "INVALID_STATE_TRANSITION"

    def test_recovery_pending_tx_cannot_restart(self, client):
        """RECOVERY_PENDING cannot go back to INITIATED (validator unit-level)
        nor restart via PROCESSING (end-to-end 400)."""
        import pytest
        with pytest.raises(InvalidStateTransitionError):
            validate_transition(
                TransactionState.RECOVERY_PENDING, TransactionState.INITIATED)

        tid = _unique_id()
        resp = _ingest(client, make_event(tid, **CLEAN_FAILED_TX))
        assert resp.status_code == 200
        assert resp.json()["current_state"] == "RECOVERY_PENDING"

        stalled_payload = dict(CLEAN_FAILED_TX, status="PROCESSING")
        illegal = _ingest(client, make_event(tid, **stalled_payload))
        assert illegal.status_code == 400
        assert illegal.json()["error"]["code"] == "INVALID_STATE_TRANSITION"

        fetch = client.get(f"/api/v1/transactions/{tid}", headers=SYSTEM_KEY)
        assert fetch.json()["current_state"] == "RECOVERY_PENDING"

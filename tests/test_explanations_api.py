"""POST /api/v1/explanations/transaction end-to-end: role enforcement, Bangla
customer explanations, support detail, response caching, deterministic
fallback on provider failure, error shapes, and decision immutability (GenAI
explains — it never mutates state)."""

from __future__ import annotations

import uuid

import pytest

from api.main import app
from api.services.ai import get_ai_provider
from api.services.ai.mock_provider import FailingMockProvider
from tests.conftest import CLEAN_FAILED_TX, CUSTOMER_KEY, SUPPORT_KEY, SYSTEM_KEY

EVENT_URL = "/api/v1/transaction/event"
RELEASE_URL = "/api/v1/recovery/release-limit"
EXPLANATION_URL = "/api/v1/explanations/transaction"


def _unique_id(prefix: str = "TXN-EXPLAIN") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _ingest_failed(client, tid, **overrides):
    payload = dict(
        transaction_id=tid, user_id="USER-001", merchant_id="MERCHANT-001",
        currency="BDT",
    )
    payload.update(CLEAN_FAILED_TX)
    payload.update(overrides)
    return client.post(EVENT_URL, json=payload, headers=SYSTEM_KEY)


def _released_tx(client, tid):
    """Ingest a clean failed transaction and run the recovery decision so it
    sits in LIMIT_RELEASED."""
    assert _ingest_failed(client, tid).status_code == 200
    resp = client.post(RELEASE_URL, json={"transaction_id": tid},
                       headers=SYSTEM_KEY)
    assert resp.status_code == 200
    assert resp.json()["decision"] == "LIMIT_RELEASED"


def _explain(client, tid, language="bn", audience="customer", headers=SYSTEM_KEY,
             **extra):
    body = {"transaction_id": tid, "language": language, "audience": audience}
    body.update(extra)
    return client.post(EXPLANATION_URL, json=body, headers=headers)


def _has_bengali(text: str) -> bool:
    return any("ঀ" <= ch <= "৿" for ch in text)


def test_customer_bangla_explanation_from_mock_provider(client):
    tid = _unique_id()
    _released_tx(client, tid)

    resp = _explain(client, tid)
    assert resp.status_code == 200
    body = resp.json()

    assert body["transaction_id"] == tid
    assert body["explanation"].strip() != ""
    assert _has_bengali(body["explanation"])
    assert body["provider"] == "mock"
    assert body["prompt_version"] == "v1"
    assert body["is_fallback"] is False
    assert body["cached"] is False


def test_support_english_explanation_contains_decision_and_id(client):
    tid = _unique_id()
    _released_tx(client, tid)

    resp = _explain(client, tid, language="en", audience="support",
                    headers=SUPPORT_KEY)
    assert resp.status_code == 200
    body = resp.json()

    assert body["explanation"].strip() != ""
    assert tid in body["explanation"]
    assert "LIMIT_RELEASED" in body["explanation"]


def test_identical_second_call_is_served_from_cache(client):
    tid = _unique_id()
    _released_tx(client, tid)

    first = _explain(client, tid)
    assert first.status_code == 200
    assert first.json()["cached"] is False

    second = _explain(client, tid)
    assert second.status_code == 200
    replay = second.json()
    assert replay["cached"] is True
    assert replay["explanation"] == first.json()["explanation"]


def test_provider_failure_falls_back_deterministically(client):
    tid = _unique_id()
    _released_tx(client, tid)

    app.dependency_overrides[get_ai_provider] = lambda: FailingMockProvider()
    try:
        resp = _explain(client, tid)
    finally:
        app.dependency_overrides.pop(get_ai_provider, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["is_fallback"] is True
    assert body["explanation"].strip() != ""


def test_unknown_transaction_is_404_not_found(client):
    resp = _explain(client, "TXN-DOES-NOT-EXIST")
    assert resp.status_code == 404
    error = resp.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert error["message"]


def test_explanations_require_api_key_and_role(client):
    tid = _unique_id()

    resp = _explain(client, tid, headers={})
    assert resp.status_code == 401

    # CUSTOMER role must not see explanations at all.
    assert _explain(client, tid, headers=CUSTOMER_KEY).status_code == 403


def test_client_cannot_inject_decision_fields(client):
    """Extra body fields are ignored — the explanation must reflect the
    decision stored in the DB (LIMIT_RELEASED), never caller input."""
    tid = _unique_id()
    _released_tx(client, tid)

    resp = _explain(client, tid, language="en", audience="support",
                    safe_to_release=False, decision="RECOVERY_REJECTED")
    assert resp.status_code == 200
    body = resp.json()

    assert body["explanation"].strip() != ""
    assert "LIMIT_RELEASED" in body["explanation"]
    assert "RECOVERY_REJECTED" not in body["explanation"]


def test_explanations_never_mutate_transaction_state(client):
    tid = _unique_id()
    _released_tx(client, tid)

    def _snapshot():
        tx = client.get(f"/api/v1/transactions/{tid}", headers=SYSTEM_KEY).json()
        timeline = client.get(f"/api/v1/transactions/{tid}/timeline",
                              headers=SYSTEM_KEY).json()
        fields = ("current_state", "risk_score", "safe_to_release", "decision")
        return {k: tx[k] for k in fields if k in tx}, timeline["event_count"]

    before_tx, before_events = _snapshot()

    assert _explain(client, tid).status_code == 200
    assert _explain(client, tid, language="en", audience="support").status_code == 200

    after_tx, after_events = _snapshot()
    assert after_tx == before_tx
    assert after_events == before_events

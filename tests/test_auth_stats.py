"""Frontend-support endpoints: /api/v1/auth/me identity probe and
/api/v1/stats/summary honest aggregates (pure reads, no fabrication)."""

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

ME_URL = "/api/v1/auth/me"
STATS_URL = "/api/v1/stats/summary"
EVENT_URL = "/api/v1/transaction/event"


def _unique_id(prefix: str = "TXN-STATS") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def test_auth_me_echoes_role_for_all_four_keys(client):
    expected = [
        (SYSTEM_KEY, "SYSTEM"),
        (ADMIN_KEY, "ADMIN"),
        (SUPPORT_KEY, "SUPPORT"),
        (CUSTOMER_KEY, "CUSTOMER"),
    ]
    for headers, role in expected:
        resp = client.get(ME_URL, headers=headers)
        assert resp.status_code == 200, f"{role} key failed"
        body = resp.json()
        assert body["role"] == role
        assert body["key_name"]


def test_auth_me_requires_a_key(client):
    assert client.get(ME_URL).status_code == 401


def test_auth_me_rejects_garbage_key(client):
    resp = client.get(ME_URL, headers={"X-API-Key": "not-a-real-key"})
    assert resp.status_code == 401


def test_stats_summary_roles(client):
    for headers in (SYSTEM_KEY, ADMIN_KEY, SUPPORT_KEY):
        resp = client.get(STATS_URL, headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "total", "by_state", "decisions", "risk_assessments",
            "autonomous_recovery",  # Stage 8
        }
        assert set(body["decisions"]) == {
            "LIMIT_RELEASED", "MANUAL_REVIEW", "RECOVERY_REJECTED",
        }
        assert set(body["risk_assessments"]) == {
            "total", "by_anomaly_type", "by_risk_level", "recovery_candidates",
        }


def test_stats_reflects_ingested_transaction(client):
    before = client.get(STATS_URL, headers=SYSTEM_KEY).json()

    tid = _unique_id()
    payload = dict(
        transaction_id=tid, user_id="USER-001", merchant_id="MERCHANT-001",
        currency="BDT",
    )
    payload.update(CLEAN_FAILED_TX)
    assert client.post(EVENT_URL, json=payload, headers=SYSTEM_KEY).status_code == 200

    after = client.get(STATS_URL, headers=SYSTEM_KEY).json()
    assert after["total"] >= 1
    assert after["total"] >= before["total"]
    assert after["by_state"].get("RECOVERY_PENDING", 0) >= 1


def test_stats_forbidden_for_customer(client):
    assert client.get(STATS_URL, headers=CUSTOMER_KEY).status_code == 403


def test_stats_requires_a_key(client):
    assert client.get(STATS_URL).status_code == 401


def test_stats_is_deterministic(client):
    first = client.get(STATS_URL, headers=SYSTEM_KEY)
    second = client.get(STATS_URL, headers=SYSTEM_KEY)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()

"""tests/test_audit.py — Stage 9 security audit trail + sandbox reset.

Covers: AUTH_FAILURE rows on bad keys, role gating on GET /api/v1/audit,
newest-first ordering with request-id correlation, the sandbox reset
endpoint (staff-only, simulated, wipes the simulated ledger), and the
DEMO_RESET audit row it leaves behind.
"""

from __future__ import annotations

import pytest

from api.services.audit import AUDIT_AUTH_FAILURE, AUDIT_DEMO_RESET
from api.services.payment_provider import get_payment_provider

from tests.conftest import ADMIN_KEY, CUSTOMER_KEY, SUPPORT_KEY, SYSTEM_KEY

AUDIT_URL = "/api/v1/audit"
RESET_URL = "/api/v1/sandbox/reset"


# --------------------------------------------------------------------------
# AUTH_FAILURE audit rows
# --------------------------------------------------------------------------

PROTECTED_URL = "/api/v1/auth/me"  # any key-gated endpoint works


def test_bad_key_creates_auth_failure_row(client) -> None:
    resp = client.get(PROTECTED_URL, headers={"X-API-Key": "not-a-key"})
    assert resp.status_code == 401

    rows = client.get(AUDIT_URL, headers=SYSTEM_KEY).json()["rows"]
    auth_failures = [r for r in rows if r["action"] == AUDIT_AUTH_FAILURE]
    assert auth_failures, "expected at least one AUTH_FAILURE audit row"
    latest = auth_failures[0]
    assert latest["actor_type"] == "ANONYMOUS"
    assert latest["result"] == "DENIED"
    assert "not-a-key" not in json_dumps(latest)  # never leaks the key value


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj)


def test_missing_header_creates_auth_failure_row(client) -> None:
    resp = client.get(PROTECTED_URL)  # no X-API-Key at all
    assert resp.status_code == 401
    rows = client.get(AUDIT_URL, headers=SYSTEM_KEY).json()["rows"]
    assert any(
        r["action"] == AUDIT_AUTH_FAILURE and r["reason"] == "missing X-API-Key header"
        for r in rows
    )


# --------------------------------------------------------------------------
# GET /audit role gating
# --------------------------------------------------------------------------

def test_audit_requires_staff_roles(client) -> None:
    assert client.get(AUDIT_URL, headers=SUPPORT_KEY).status_code == 403
    assert client.get(AUDIT_URL, headers=CUSTOMER_KEY).status_code == 403
    assert client.get(AUDIT_URL).status_code == 401
    assert client.get(AUDIT_URL, headers=SYSTEM_KEY).status_code == 200
    assert client.get(AUDIT_URL, headers=ADMIN_KEY).status_code == 200


def test_audit_rows_newest_first_with_request_id(client) -> None:
    # deterministic row with an explicit request_id
    from api.services.audit import record_security_event

    record_security_event(
        actor_type="SYSTEM",
        actor_id="api_key_system",
        action="TEST_PROBE",
        request_id="req-test-123",
    )
    body = client.get(
        f"{AUDIT_URL}?action=TEST_PROBE", headers=SYSTEM_KEY
    ).json()
    assert body["count"] >= 1
    probe = next(r for r in body["rows"] if r["request_id"] == "req-test-123")
    for field in ("audit_id", "created_at", "actor_type", "actor_id", "action",
                  "resource_type", "resource_id", "request_id", "result",
                  "reason"):
        assert field in probe

    # unfiltered listing is newest-first (created_at non-increasing)
    rows = client.get(AUDIT_URL, headers=SYSTEM_KEY).json()["rows"]
    timestamps = [r["created_at"] for r in rows]
    assert timestamps == sorted(timestamps, reverse=True)


def test_audit_limit_and_action_filter(client) -> None:
    body = client.get(
        f"{AUDIT_URL}?limit=2&action={AUDIT_AUTH_FAILURE}", headers=SYSTEM_KEY
    ).json()
    assert body["count"] <= 2
    assert all(r["action"] == AUDIT_AUTH_FAILURE for r in body["rows"])
    # limit is capped
    resp = client.get(f"{AUDIT_URL}?limit=9999", headers=SYSTEM_KEY)
    assert resp.status_code == 422


# --------------------------------------------------------------------------
# Sandbox reset endpoint
# --------------------------------------------------------------------------

def test_sandbox_reset_system_and_admin(client) -> None:
    provider = get_payment_provider()
    provider.ensure_hold("txn-audit-reset-1", 500.0, "BDT")
    assert provider.get_ledger_entry("txn-audit-reset-1") is not None

    for key in (SYSTEM_KEY, ADMIN_KEY):
        resp = client.post(RESET_URL, headers=key)
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "reset": True, "simulated": True, "request_id": body["request_id"],
        }
        assert provider.get_ledger_entry("txn-audit-reset-1") is None
        # re-arm for the second iteration
        provider.ensure_hold("txn-audit-reset-1", 500.0, "BDT")


def test_sandbox_reset_forbidden_for_customer(client) -> None:
    assert client.post(RESET_URL, headers=CUSTOMER_KEY).status_code == 403


def test_demo_reset_audit_row_exists(client) -> None:
    resp = client.post(RESET_URL, headers=SYSTEM_KEY)
    assert resp.status_code == 200
    rows = client.get(
        f"{AUDIT_URL}?action={AUDIT_DEMO_RESET}", headers=SYSTEM_KEY
    ).json()["rows"]
    assert rows, "reset must leave a DEMO_RESET audit row"
    latest = rows[0]
    assert latest["actor_type"] in ("SYSTEM", "ADMIN")
    assert latest["actor_id"] == "api_key_system"
    assert latest["result"] == "ALLOWED"
    assert latest["resource_type"] == "sandbox_ledger"

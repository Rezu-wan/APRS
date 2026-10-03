"""Stage 11 Phase 11F — chaos & safety tests.

Each scenario runs through the REAL chaos runner (which itself runs the REAL
recovery engine — autonomous_recovery.process_transaction; nothing here can
fake a successful recovery). Assertions are on the runner's verdict plus the
named invariants; the DB_FAILURE_SIMULATION scenario honestly SKIPs.

The router is registered idempotently (module-scoped fixture, same pattern
as tests/test_relationship.py) so the tests are self-contained before the
integrator wires api/main.py.
"""

from __future__ import annotations

import pytest

from api.db.database import SessionLocal
from api.db.models import SecurityAuditRecord
from api.main import app
from api.routes import chaos as chaos_route
from api.services.audit import AUDIT_CHAOS_TEST
from api.services.chaos import (
    CATALOG,
    SCENARIO_ORDER,
    purge_chaos_rows,
    run_chaos_scenario,
    scenario_start,
)
from api.services.ml_service import get_ml_service
from api.services.payment_provider import MockPaymentProvider, get_payment_provider

from tests.conftest import ADMIN_KEY, CUSTOMER_KEY, SUPPORT_KEY

RUN_URL = "/api/v1/chaos/run"
SCENARIOS_URL = "/api/v1/chaos/scenarios"

RELEASE_SCENARIOS = (
    "GATEWAY_TIMEOUT",
    "GATEWAY_ERROR",
    "MERCHANT_TIMEOUT",
    "DUPLICATE_EVENT",
    "OUT_OF_ORDER_EVENT",
    "CONCURRENT_RECOVERY",
)
BLOCKED_SCENARIOS = (
    "LATE_SETTLEMENT",
    "PROVIDER_TIMEOUT",
    "PROVIDER_ERROR",
)


@pytest.fixture(scope="module", autouse=True)
def _register_router():
    """Idempotent registration so the tests pass before/after integration."""
    paths = {getattr(route, "path", None) for route in app.routes}
    if RUN_URL not in paths:
        app.include_router(chaos_route.router)
    yield


@pytest.fixture(scope="module")
def chaos_env(client):
    """The real singletons + a fresh-chaos-corpus guarantee."""
    db = SessionLocal()
    provider = get_payment_provider()
    purge_chaos_rows(db, provider)
    try:
        yield db, get_ml_service(), provider
    finally:
        purge_chaos_rows(db, provider)
        db.close()


def _run(db, ml, provider, scenario):
    return run_chaos_scenario(
        db, ml, provider, scenario, start=scenario_start(scenario)
    )


# ---------------------------------------------------------------- service


@pytest.mark.parametrize("scenario", RELEASE_SCENARIOS)
def test_release_scenarios_recover_exactly_once(chaos_env, scenario):
    db, ml, provider = chaos_env
    result = _run(db, ml, provider, scenario)
    assert result.verdict == "PASS", (
        f"{scenario}: {[ (i.name, i.detail) for i in result.invariants if not i.held ]}"
        + (f" note={result.note}" if result.note else "")
    )
    assert result.outcome is not None
    assert result.outcome.decision == "AUTO_RECOVERED"
    assert result.outcome.status == "VERIFIED"
    held = {i.name for i in result.invariants if i.held}
    if scenario != "CONCURRENT_RECOVERY":
        # the threaded scenario asserts its own (stronger) invariant names
        assert {
            "one_recovery_row", "provider_reference_present",
            "ledger_released_once", "tx_limit_released",
        } <= held


@pytest.mark.parametrize("scenario", BLOCKED_SCENARIOS)
def test_blocked_scenarios_never_release(chaos_env, scenario):
    db, ml, provider = chaos_env
    result = _run(db, ml, provider, scenario)
    assert result.verdict == "PASS", (
        f"{scenario}: {[ (i.name, i.detail) for i in result.invariants if not i.held ]}"
        + (f" note={result.note}" if result.note else "")
    )
    assert result.outcome is not None
    assert result.outcome.decision == "RECOVERY_BLOCKED"
    held = {i.name for i in result.invariants if i.held}
    assert {"tx_never_limit_released", "ledger_not_released"} <= held


def test_late_settlement_safety_gate(chaos_env):
    """The core Stage-8 safety property under chaos: a settlement that lands
    after the assessment must NEVER be released on top of."""
    db, ml, provider = chaos_env
    result = _run(db, ml, provider, "LATE_SETTLEMENT")
    assert result.verdict == "PASS"
    assert result.outcome.decision != "AUTO_RECOVERED"
    assert result.outcome.provider_reference is None
    assert "blocked_by_fresh_evidence_gate" in {
        i.name for i in result.invariants if i.held
    }


def test_db_failure_simulation_honest_skip(chaos_env):
    db, ml, provider = chaos_env
    result = _run(db, ml, provider, "DB_FAILURE_SIMULATION")
    assert result.verdict == "SKIP"
    assert result.outcome is None
    assert result.invariants == []
    assert result.note is not None and "unit tests" in result.note


def test_rerun_safety_same_scenario_twice(chaos_env):
    """Purge-first means a scenario can be rerun on its pinned id."""
    db, ml, provider = chaos_env
    first = _run(db, ml, provider, "MERCHANT_TIMEOUT")
    second = _run(db, ml, provider, "MERCHANT_TIMEOUT")
    assert first.verdict == "PASS"
    assert second.verdict == "PASS", (
        [(i.name, i.detail) for i in second.invariants if not i.held]
    )


def test_provider_failure_mode_cleared_after_run(chaos_env):
    """The one-shot provider injection must not leak into the next run."""
    db, ml, provider = chaos_env
    blocked = _run(db, ml, provider, "PROVIDER_TIMEOUT")
    assert blocked.verdict == "PASS"
    after = _run(db, ml, provider, "MERCHANT_TIMEOUT")
    assert after.verdict == "PASS"
    assert after.outcome.decision == "AUTO_RECOVERED"


def test_concurrent_recovery_exactly_one_effect(chaos_env):
    db, ml, provider = chaos_env
    result = _run(db, ml, provider, "CONCURRENT_RECOVERY")
    names = {
        i.name: (i.held, i.detail) for i in result.invariants
    }
    assert result.verdict == "PASS", (
        f"{[(n, d) for n, (h, d) in names.items() if not h]}"
        + (f" note={result.note}" if result.note else "")
    )
    assert names["exactly_one_provider_reference"][0]
    assert names["exactly_one_non_replay_decision"][0]
    assert names["released_exactly_once"][0]


def test_purge_cycle_restores_available_limit(chaos_env):
    db, ml, provider = chaos_env
    purge_chaos_rows(db, provider)  # deterministic baseline
    before = provider.available_limit
    # a full run cycle (releases + failed holds) then a full chaos purge
    for scenario in ("MERCHANT_TIMEOUT", "PROVIDER_TIMEOUT"):
        result = _run(db, ml, provider, scenario)
        assert result.verdict == "PASS"
    deleted = purge_chaos_rows(db, provider)
    assert deleted >= 1
    assert provider.available_limit == pytest.approx(before, abs=0.001)


# -------------------------------------------------------------------- API


def test_api_run_admin_gateway_timeout(client, chaos_env):
    db, ml, provider = chaos_env
    resp = client.post(
        RUN_URL, json={"scenario": "GATEWAY_TIMEOUT"}, headers=ADMIN_KEY
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "PASS"
    assert body["outcome"]["decision"] == "AUTO_RECOVERED"
    assert body["transaction_id"] == "CHAOS-GATEWAY_TIMEOUT-1"
    assert all(inv["held"] for inv in body["invariants"])


def test_api_run_admin_late_settlement_not_recovered(client, chaos_env):
    resp = client.post(
        RUN_URL, json={"scenario": "LATE_SETTLEMENT"}, headers=ADMIN_KEY
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "PASS"
    assert body["outcome"]["decision"] != "AUTO_RECOVERED"


def test_api_run_unknown_scenario_422(client):
    resp = client.post(
        RUN_URL, json={"scenario": "NOT_A_SCENARIO"}, headers=ADMIN_KEY
    )
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "CHAOS_UNKNOWN_SCENARIO"
    for scenario in SCENARIO_ORDER:
        assert scenario in error["message"]


def test_api_scenarios_catalog_staff_only(client):
    resp = client.get(SCENARIOS_URL, headers=ADMIN_KEY)
    assert resp.status_code == 200
    listed = {s["scenario"] for s in resp.json()["scenarios"]}
    assert listed == set(CATALOG)

    resp = client.get(SCENARIOS_URL, headers=SUPPORT_KEY)
    assert resp.status_code == 200


@pytest.mark.parametrize("headers", [SUPPORT_KEY, CUSTOMER_KEY])
def test_api_run_forbidden_roles(client, headers):
    resp = client.post(RUN_URL, json={"scenario": "MERCHANT_TIMEOUT"},
                       headers=headers)
    assert resp.status_code == 403


def test_api_run_writes_chaos_audit_row(client, chaos_env):
    db, _ml, _provider = chaos_env
    resp = client.post(
        RUN_URL, json={"scenario": "DUPLICATE_EVENT"}, headers=ADMIN_KEY
    )
    assert resp.status_code == 200
    rows = (
        db.query(SecurityAuditRecord)
        .filter(SecurityAuditRecord.action == AUDIT_CHAOS_TEST)
        .order_by(SecurityAuditRecord.id.desc())
        .all()
    )
    assert rows, "no CHAOS_TEST audit row written"
    meta = rows[0].audit_metadata or {}
    assert meta.get("scenario") == "DUPLICATE_EVENT"
    assert meta.get("verdict") == "PASS"
    assert meta.get("transaction_id") == "CHAOS-DUPLICATE_EVENT-1"


def test_provider_purge_transactions_signature_and_behavior():
    """Unit check of the one added provider method: restores the limit,
    drops entries, tolerates unknown ids."""
    provider = MockPaymentProvider()  # pure in-memory (store=None)
    provider.ensure_hold("CHAOS-PURGE-1", 300.0, "BDT")
    provider.ensure_hold("OTHER-1", 200.0, "BDT")
    assert provider.available_limit == MockPaymentProvider.INITIAL_LIMIT - 500.0

    removed = provider.purge_transactions(["CHAOS-PURGE-1", "UNKNOWN"])
    assert removed == 1
    assert provider.get_ledger_entry("CHAOS-PURGE-1") is None
    assert provider.get_ledger_entry("OTHER-1") is not None
    assert provider.available_limit == MockPaymentProvider.INITIAL_LIMIT - 200.0
    assert provider.purge_transactions(["CHAOS-PURGE-1"]) == 0

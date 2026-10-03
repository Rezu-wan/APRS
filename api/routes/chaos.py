"""
api/routes/chaos.py — Stage 11 Phase 11F chaos & safety endpoints (SANDBOX
ONLY).

POST /api/v1/chaos/run {scenario} — SYSTEM/ADMIN only. Runs ONE chaos
scenario through the REAL recovery pipeline (autonomous_recovery, never a
faked outcome) and returns the honest result: verdict PASS/FAIL/SKIP with
the per-invariant outcomes. A FAIL is still HTTP 200 — the result IS the
report; a 500 would hide the evidence.

GET /api/v1/chaos/scenarios — staff-only catalog listing so the UI can
render buttons without hardcoding scenario names.

Every run records a best-effort AUDIT_CHAOS_TEST security-audit row.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.core.security import AuthContext, require_roles
from api.db.database import get_db
from api.services.audit import AUDIT_CHAOS_TEST, record_security_event
from api.services.chaos import (
    CATALOG,
    SCENARIO_ORDER,
    ChaosInvariant,
    ChaosOutcome,
    ChaosRunResult,
    run_chaos_scenario,
    scenario_start,
)
from api.services.ml_service import get_ml_service
from api.services.payment_provider import get_payment_provider

logger = logging.getLogger("payment_recovery.chaos_api")

router = APIRouter(prefix="/api/v1/chaos", tags=["chaos"])


class ChaosRunRequest(BaseModel):
    scenario: str


class ChaosInvariantOut(BaseModel):
    name: str
    held: bool
    detail: str


class ChaosOutcomeOut(BaseModel):
    decision: str | None
    status: str | None
    recovery_id: str | None
    provider_reference: str | None
    blocked_reason: str | None


class ChaosRunResultOut(BaseModel):
    scenario: str
    transaction_id: str | None
    verdict: str  # PASS | FAIL | SKIP
    outcome: ChaosOutcomeOut | None
    invariants: list[ChaosInvariantOut]
    note: str | None


class ChaosScenarioOut(BaseModel):
    scenario: str
    description: str
    expected: dict


class ChaosScenarioListResponse(BaseModel):
    scenarios: list[ChaosScenarioOut]


def _result_out(result: ChaosRunResult) -> ChaosRunResultOut:
    outcome = (
        ChaosOutcomeOut(
            decision=result.outcome.decision,
            status=result.outcome.status,
            recovery_id=result.outcome.recovery_id,
            provider_reference=result.outcome.provider_reference,
            blocked_reason=result.outcome.blocked_reason,
        )
        if result.outcome is not None
        else None
    )
    return ChaosRunResultOut(
        scenario=result.scenario,
        transaction_id=result.transaction_id,
        verdict=result.verdict,
        outcome=outcome,
        invariants=[
            ChaosInvariantOut(name=i.name, held=i.held, detail=i.detail)
            for i in result.invariants
        ],
        note=result.note,
    )


@router.post("/run", response_model=ChaosRunResultOut, responses={
    422: {"description": "Unknown scenario (catalog listed in the message)"},
})
def run_chaos(
    body: ChaosRunRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN")),
) -> ChaosRunResultOut:
    """Run ONE chaos scenario (SANDBOX ONLY). The scenario drives the REAL
    recovery engine; the response is the honest PASS/FAIL/SKIP verdict with
    the named invariants — a FAIL never raises, it reports."""
    result = run_chaos_scenario(
        db,
        get_ml_service(),
        get_payment_provider(),
        body.scenario,
        start=scenario_start(body.scenario) if body.scenario in CATALOG
        else scenario_start(SCENARIO_ORDER[0]),
    )

    record_security_event(
        actor_type=auth.role,
        actor_id=auth.key_name,
        action=AUDIT_CHAOS_TEST,
        resource_type="chaos_scenario",
        resource_id=body.scenario,
        audit_metadata={
            "scenario": result.scenario,
            "verdict": result.verdict,
            "transaction_id": result.transaction_id,
        },
        db=db,
    )
    logger.info("chaos scenario %s run by %s (%s): verdict=%s",
                result.scenario, auth.key_name, auth.role, result.verdict)
    return _result_out(result)


@router.get("/scenarios", response_model=ChaosScenarioListResponse)
def list_chaos_scenarios(
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT")),
) -> ChaosScenarioListResponse:
    """The chaos catalog (staff-only): names, descriptions and the expected
    outcome so the UI can render the scenario buttons."""
    return ChaosScenarioListResponse(
        scenarios=[
            ChaosScenarioOut(
                scenario=scenario,
                description=CATALOG[scenario]["description"],
                expected=CATALOG[scenario]["expected"],
            )
            for scenario in SCENARIO_ORDER
        ]
    )

"""
api/routes/policy_simulator.py — Stage 11 Phase 11E Recovery Policy
Simulator endpoint (research tool, SANDBOX ONLY).

POST /api/v1/policy-simulator/run simulates versioned recovery-decision
policies over a transaction corpus on the SAME evidence and returns flat,
measurable per-policy differences. NO RANKING, no recommendation (spec
section 13) — policy selection stays a human decision.

PURITY: the run is read-only against the live system — it never touches
the active policy, the executor, the provider, the ledger, transaction
states, or the twin. The ONLY write is ONE best-effort POLICY_SIMULATION
security-audit row, written here AFTER the run (audit must never break,
and never influence, the simulation).

Authorization: SYSTEM/ADMIN only — the output exposes the internal policy
table and safety-gate behavior.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.core.exceptions import AppError
from api.core.security import AuthContext, require_roles
from api.db.database import get_db
from api.services.audit import AUDIT_POLICY_SIMULATION, record_security_event
from api.services.demo_scenarios import (
    SCENARIOS,
    scenario_transaction_id,
)
from api.services.policy_simulator import (
    SIMULATOR_NAME,
    SimulationRun,
    UnknownPolicyError,
    simulate_policies,
)

logger = logging.getLogger("payment_recovery.policy_simulator")

router = APIRouter(prefix="/api/v1/policy-simulator", tags=["policy-simulator"])


class UnknownPolicyErrorHttp(AppError):
    """Request named a policy that is not in the registry."""

    status_code = 422
    code = "INVALID_POLICY_NAME"


class SimulationSource(BaseModel):
    """Exactly one of `demo` or `transaction_ids` must be provided."""

    demo: bool = False
    transaction_ids: list[str] | None = None


class SimulatorRunRequest(BaseModel):
    policies: list[str] = Field(min_length=1)
    source: SimulationSource


@router.post(
    "/run",
    response_model=SimulationRun,
    responses={
        403: {"description": "SYSTEM/ADMIN only"},
        422: {"description": "Unknown policy name or bad source"},
    },
    summary="Simulate recovery policies over a corpus (read-only research)",
)
def run_policy_simulation(
    request: SimulatorRunRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN")),
):
    """Simulate the requested policies over the demo corpus or an explicit
    transaction-id list. Simulation NEVER affects the live policy."""
    if request.source.demo == (
        request.source.transaction_ids is not None
    ):
        raise UnknownPolicyErrorHttp(
            "provide exactly one of source.demo or source.transaction_ids"
        )

    try:
        from api.services.ml_service import get_ml_service

        if request.source.demo:
            tids = [
                scenario_transaction_id(key)
                for key in SCENARIOS
            ]
            run = simulate_policies(
                db,
                get_ml_service(),
                request.policies,
                tids,
                now=datetime.now(timezone.utc),
                ground_truth_available=True,
                source="demo",
            )
        else:
            run = simulate_policies(
                db,
                get_ml_service(),
                request.policies,
                request.source.transaction_ids or [],
                now=datetime.now(timezone.utc),
                ground_truth_available=False,
                source="transaction_ids",
            )
    except UnknownPolicyError as exc:
        raise UnknownPolicyErrorHttp(str(exc)) from exc

    record_security_event(
        actor_type=auth.role,
        actor_id=auth.key_name,
        action=AUDIT_POLICY_SIMULATION,
        resource_type=SIMULATOR_NAME,
        resource_id=run.run_id,
        audit_metadata={
            "policies": request.policies,
            "dataset_fingerprint": run.dataset.dataset_fingerprint,
            "simulated": True,
        },
        db=db,
    )

    return run

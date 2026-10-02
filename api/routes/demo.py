"""
api/routes/demo.py — Stage 10 demo-control API.

SANDBOX-ONLY, staff-gated controls for the six deterministic demo
transactions DEMO-S1..S6 (see api/services/demo_scenarios.py):

  GET  /api/v1/demo/scenarios                  catalog + live DB state
  GET  /api/v1/demo/status                     sandbox health snapshot
  POST /api/v1/demo/scenarios/{key}/prepare    seed one scenario (idempotent)
  POST /api/v1/demo/scenarios/{key}/inject-late-settlement   S5 race step
  POST /api/v1/demo/reset                      full deterministic reset

Nothing here ever implies real funds: every response is labeled
"simulated": true, prepare NEVER runs recovery/process (the recovery
decision must come from the real engine via the normal endpoint), and all
evidence is written through the real ingestion/assessment services.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.core.config import get_settings
from api.core.security import AuthContext, require_roles
from api.db.database import get_db
from api.db.models import RecoveryActionRecord, RiskAssessmentRecord, Transaction
from api.services.ai.prompts import PROMPT_VERSION
from api.services.audit import (
    AUDIT_DEMO_RESET,
    AUDIT_DEMO_SEED,
    current_request_id,
    record_security_event,
)
from api.services.demo_scenarios import (
    SCENARIOS,
    inject_late_settlement,
    prepare_scenario,
    reset_demo_corpus,
    scenario_transaction_id,
)
from api.services.ml_service import get_ml_service
from api.services.payment_provider import MockPaymentProvider, get_payment_provider

logger = logging.getLogger("payment_recovery.demo_api")

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])

SANDBOX_NOTE = "simulated sandbox — no real financial transaction"


def _scenario_status(db: Session, key: str) -> dict:
    """Catalog metadata + live DB state for one DEMO-S* transaction."""
    from api.services.demo_scenarios import SCENARIOS as _scen
    from api.services.payment_event_service import get_payment_events

    spec = _scen[key]
    tid = scenario_transaction_id(key)
    tx = (
        db.query(Transaction)
        .filter(Transaction.transaction_id == tid)
        .one_or_none()
    )
    exists = tx is not None
    events = get_payment_events(db, tid) if exists else []
    recovery_row = (
        db.query(RecoveryActionRecord)
        .filter(RecoveryActionRecord.transaction_id == tid)
        .order_by(
            RecoveryActionRecord.created_at.desc(), RecoveryActionRecord.id.desc()
        )
        .first()
    ) if exists else None
    risk_row = (
        db.query(RiskAssessmentRecord)
        .filter(RiskAssessmentRecord.transaction_id == tid)
        .order_by(
            RiskAssessmentRecord.created_at.desc(), RiskAssessmentRecord.id.desc()
        )
        .first()
    ) if exists else None

    recovery = None
    if recovery_row is not None:
        recovery = {
            "decision": recovery_row.action,
            "status": recovery_row.status,
            "recovery_id": recovery_row.recovery_id,
            "blocked_reason": recovery_row.blocked_reason,
            "provider_reference": recovery_row.provider_reference,
            "released_amount": (
                float(recovery_row.released_amount)
                if recovery_row.released_amount is not None
                else None
            ),
            "currency": recovery_row.currency,
        }
    risk = None
    if risk_row is not None:
        risk = {
            "anomaly_type": risk_row.anomaly_type,
            "risk_level": risk_row.risk_level,
        }

    return {
        "key": key,
        "transaction_id": tid,
        "title": spec["title"],
        "subtitle": spec["subtitle"],
        "story": spec["story"],
        "steps_key": spec["steps"],
        "expected": dict(spec["expected"]),
        "exists": exists,
        "current_state": tx.current_state if exists else None,
        "prepared": bool(events),
        "processed": recovery_row is not None,
        "recovery": recovery,
        "risk": risk,
    }


def _audit_seed(
    db: Session, auth: AuthContext, *, resource_id: str, actions: list[str]
) -> None:
    record_security_event(
        actor_type=auth.role,
        actor_id=auth.key_name,
        action=AUDIT_DEMO_SEED,
        resource_type="demo_scenario",
        resource_id=resource_id,
        result="ALLOWED",
        reason="demo scenario seed (sandbox, simulated)",
        audit_metadata={"actions": actions},
        db=db,
    )


@router.get("/scenarios")
def list_scenarios(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT")),
):
    """Demo scenario catalog with live DB state per scenario. Read-only;
    SUPPORT-visible so presenters can check status without write rights."""
    return {
        "simulated": True,
        "note": SANDBOX_NOTE,
        "scenarios": [_scenario_status(db, key) for key in SCENARIOS],
    }


@router.get("/status")
def demo_status(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT")),
):
    """SANDBOX health snapshot for the demo UI. No network calls — the GenAI
    status is derived from configuration only."""
    settings = get_settings()
    try:
        db.execute(text("SELECT 1"))
        database = "connected"
    except Exception:  # noqa: BLE001 — status must never leak driver errors
        database = "unavailable"

    ml_status = "loaded" if get_ml_service().loaded else "not_loaded"

    provider_name = (settings.ai_provider or "").strip().lower()
    if provider_name == "mock":
        genai_status = "available"
    elif provider_name == "openai":
        genai_status = "available" if settings.openai_api_key else "unavailable"
    else:
        genai_status = "unavailable"

    provider = get_payment_provider()
    entries = (
        provider.ledger_snapshot()
        if isinstance(provider, MockPaymentProvider)
        else []
    )
    return {
        "simulated": True,
        "note": SANDBOX_NOTE,
        "database": database,
        "ml_models": ml_status,
        "genai": {
            "provider": provider_name,
            "status": genai_status,
            "prompt_version": PROMPT_VERSION,
        },
        "sandbox_provider": {
            "provider": "mock",
            "initial_limit": 10000.0,
            "available_limit": provider.available_limit,
            "currency": "BDT",
            "held_entries": sum(1 for e in entries if e.get("status") == "HELD"),
            "released_entries": sum(
                1 for e in entries if e.get("status") == "RELEASED"
            ),
        },
    }


@router.post("/scenarios/{key}/prepare")
def prepare(
    key: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN")),
):
    """Idempotently prepare one demo scenario through the REAL ingestion and
    assessment services. NEVER processes recovery — the recovery decision is
    always the real engine's, reached via the normal endpoint."""
    actions = prepare_scenario(db, key)  # raises 404 on unknown key
    _audit_seed(db, auth, resource_id=f"DEMO-{key}", actions=actions)
    return {
        "prepared": True,
        "simulated": True,
        "note": SANDBOX_NOTE,
        "scenario": _scenario_status(db, key),
        "actions": actions,
    }


@router.post("/scenarios/{key}/inject-late-settlement")
def inject_late_settlement_route(
    key: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN")),
):
    """S5-only race step: ingest the late SETTLEMENT_CONFIRMED evidence via
    the real ingestion service (idempotent). Other scenarios -> 400."""
    from api.services.demo_scenarios import DemoInvalidStepError

    actions = inject_late_settlement(db, key)
    _audit_seed(db, auth, resource_id=f"DEMO-{key}", actions=actions)
    return {
        "prepared": True,
        "simulated": True,
        "note": SANDBOX_NOTE,
        "scenario": _scenario_status(db, key),
        "actions": actions,
    }


@router.post("/reset")
def reset(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN")),
):
    """Full deterministic reset: purge every DEMO-S* row (FK-safe order),
    wipe the SIMULATED sandbox ledger (in-memory + persisted), and re-prepare
    all six scenarios through the real services."""
    meta = reset_demo_corpus(db)
    record_security_event(
        actor_type=auth.role,
        actor_id=auth.key_name,
        action=AUDIT_DEMO_RESET,
        resource_type="demo_corpus",
        resource_id=None,
        result="ALLOWED",
        reason="demo corpus reset (sandbox, simulated)",
        audit_metadata=meta,
        db=db,
    )
    logger.info("demo reset by %s (%s): %s", auth.key_name, auth.role, meta)
    return {
        "reset": True,
        "simulated": True,
        "note": SANDBOX_NOTE,
        "request_id": current_request_id(),
        "scenarios": [_scenario_status(db, key) for key in SCENARIOS],
    }

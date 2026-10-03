"""
api/services/demo_scenarios.py — Stage 10 deterministic demo-scenario catalog.

Shared source of truth for the six fixed demo transactions DEMO-S1..S6
(spec §28, S5 semantics redefined in Stage 10). Both the /api/v1/demo
router and scripts/seed_demo.py (which drives the LIVE API over HTTP)
build on the constants and builders here — the demo service calls the REAL
service-layer functions (transaction record_event, payment-event
ingest_events, risk_engine assessment); it NEVER writes evidence rows
directly and NEVER calls recovery/process (the no-bypass invariant: the
recovery decision must always come from the real engine).

Scenario semantics (SANDBOX ONLY — nothing here moves real money):
  S1  merchant_timeout chain  -> GENUINE_FAILURE, prepare only; the
      presenter runs recovery/process -> AUTO_RECOVERED / VERIFIED
  S2  double_deduction chain  -> blocked (DOUBLE_DEDUCTION / CRITICAL)
  S3  success (happy path)    -> blocked (ALREADY_SUCCESS)
  S4  single debit only       -> blocked (INSUFFICIENT_EVIDENCE / INCOMPLETE)
  S5  merchant_timeout chain + risk assessment, THEN a LATE
      SETTLEMENT_CONFIRMED event (deterministic timestamp well after the
      timeout chain) -> the real recovery/process is blocked by the
      fresh-evidence safety gate (the settlement race)
  S6  merchant_timeout chain  -> duplicate-process demo (S1's twin: second
      process call returns ALREADY_RECOVERED with the same recovery_id)

Idempotency: transaction create is create-or-adopt, payment events are
idempotent per provider_event_id, and the risk assessment is
fingerprint-reused — so prepare can be replayed any number of times.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from api.core.exceptions import AppError
from api.core.payment_lifecycle import EVENT_TYPE_INFO, HAPPY_PATH_EVENTS

logger = logging.getLogger("payment_recovery.demo_scenarios")

# Fixed demo timeline — deterministic timestamps (spec §28): all events are
# derived from this base, S<n> events starting at base + (n-1) minutes.
DEMO_BASE = datetime(2026, 10, 3, 9, 0, 0, tzinfo=timezone.utc)

BASE_LATENCY_MS = 120
TERMINAL_EVENT_MS = 90
MERCHANT_TIMEOUT_GAP_MS = 3_000
GATEWAY_TIMEOUT_GAP_MS = 3_000
DOUBLE_DEDUCTION_GAP_MS = 200

# S5 late-settlement injection: a SETTLEMENT_CONFIRMED event whose domain
# timestamp lands WELL AFTER the merchant_timeout chain (the chain finishes
# within ~6 s of the scenario start) — late provider evidence, exactly the
# shape the fresh-evidence safety gate exists for.
LATE_SETTLEMENT_OFFSET_MS = 600_000  # scenario_start + 10 minutes
# the merchant_timeout chain consumes seq 000..004; 010 never collides and
# keeps the provider_event_id convention ({tid}-{type}-{seq:03d}) — idempotent
# per provider_event_id like every other event
LATE_SETTLEMENT_SEQ = 10

_STEPS: dict[str, list[tuple[str, int]]] = {
    "merchant_timeout": [
        ("CUSTOMER_DEBIT_CONFIRMED", 0),
        ("GATEWAY_REQUEST_SENT", 0),
        ("GATEWAY_RESPONSE_RECEIVED", 0),
        ("MERCHANT_CONFIRMATION_REQUESTED", 0),
        ("MERCHANT_CONFIRMATION_TIMEOUT", MERCHANT_TIMEOUT_GAP_MS),
    ],
    "double_deduction": [
        ("CUSTOMER_DEBIT_CONFIRMED", 0),
        ("CUSTOMER_DEBIT_CONFIRMED", DOUBLE_DEDUCTION_GAP_MS),
        ("GATEWAY_REQUEST_SENT", 0),
        ("GATEWAY_TIMEOUT", GATEWAY_TIMEOUT_GAP_MS),
    ],
    "success": [(event_type, 0) for event_type in HAPPY_PATH_EVENTS],
    "single_debit": [
        ("CUSTOMER_DEBIT_CONFIRMED", 0),
    ],
    "none": [],
}

_CLEAN_FIXTURE = {
    "user_id": "USER-DEMO",
    "merchant_id": "MERCHANT-DEMO",
    "amount": 1200.00,
    "currency": "BDT",
    "gateway_latency_ms": 2800,
    "retry_count": 2,
    "network_quality": "Good",
    "previous_failures": 1,
    "account_age_days": 450,
    "status": "FAILED",
    "failure_reason": "Timeout",
}

# The scenario catalog: catalog metadata + the REAL pinned outcomes from the
# Stage 8 E2E suite (tests/test_autonomous_api.py). "expected" describes what
# the presenter should see AFTER the demo steps (S6: after both processes);
# the decision/status columns are the recovery-engine outcomes, not values
# this module ever writes itself.
SCENARIOS: dict[str, dict] = {
    "S1": {
        "steps": "merchant_timeout", "process": True,
        "label": "genuine failure -> auto-recover",
        "title": "Genuine failure, auto-recovered",
        "subtitle": "merchant timeout on a real failure",
        "story": "A real failure times out at the merchant and the engine "
                 "safely auto-recovers the held amount in the sandbox.",
        "expected": {"anomaly": "GENUINE_FAILURE", "risk_level": "LOW",
                     "decision": "AUTO_RECOVERED", "status": "VERIFIED"},
    },
    "S2": {
        "steps": "double_deduction", "process": False,
        "label": "double deduction -> blocked",
        "title": "Double deduction, blocked",
        "subtitle": "two customer debits, one payment",
        "story": "The customer was debited twice, so autonomous recovery is "
                 "blocked for manual financial review.",
        "expected": {"anomaly": "DOUBLE_DEDUCTION", "risk_level": "CRITICAL",
                     "decision": "RECOVERY_BLOCKED", "status": "BLOCKED"},
    },
    "S3": {
        "steps": "success", "process": False,
        "label": "successful payment -> blocked",
        "title": "Successful payment, blocked",
        "subtitle": "the happy path needs no recovery",
        "story": "The payment actually succeeded, so any recovery would be a "
                 "double payout and is blocked.",
        "expected": {"anomaly": "NONE", "risk_level": "LOW",
                     "decision": "RECOVERY_BLOCKED", "status": "BLOCKED"},
    },
    "S4": {
        "steps": "single_debit", "process": False,
        "label": "insufficient evidence -> blocked",
        "title": "Insufficient evidence, blocked",
        "subtitle": "a single debit event is not a story",
        "story": "Only one debit event exists, so the engine refuses to act "
                 "on incomplete evidence.",
        "expected": {"anomaly": "INCOMPLETE", "risk_level": "UNKNOWN",
                     "decision": "RECOVERY_BLOCKED", "status": "BLOCKED"},
    },
    "S5": {
        "steps": "merchant_timeout", "process": False,
        "label": "settlement race -> blocked (late settlement)",
        "title": "Settlement race, blocked",
        "subtitle": "late settlement lands after assessment",
        "story": "A settlement confirmation arrives after the risk "
                 "assessment, and the fresh-evidence safety gate blocks the "
                 "recovery (Stage 10 redefinition of S5).",
        "expected": {"anomaly": "UNKNOWN", "risk_level": "UNKNOWN",
                     "decision": "RECOVERY_BLOCKED", "status": "BLOCKED"},
    },
    "S6": {
        "steps": "merchant_timeout", "process": False,
        "label": "duplicate-process demo (S1's twin)",
        "title": "Duplicate process, replayed",
        "subtitle": "process it twice, recover once",
        "story": "Running recovery twice on the same failure replays the "
                 "original result instead of double-recovering.",
        "expected": {"anomaly": "GENUINE_FAILURE", "risk_level": "LOW",
                     "decision": "ALREADY_RECOVERED", "status": "VERIFIED"},
    },
}

DEMO_TRANSACTION_PREFIX = "DEMO-S"

# Stage 11 polish: per-scenario identities so the relationship / behavioral
# panels tell a DISTINCT story per scenario — a single shared fixture made
# every DEMO transaction look like one user bursting at one merchant. S6
# keeps S1's identity ON PURPOSE: it is S1's twin (the same customer's
# duplicate-process story), so their signals SHOULD agree.
SCENARIO_IDENTITIES: dict[str, dict] = {
    "S1": {"user_id": "USER-DEMO-1", "merchant_id": "MERCHANT-DEMO-1"},
    "S2": {"user_id": "USER-DEMO-2", "merchant_id": "MERCHANT-DEMO-2"},
    "S3": {"user_id": "USER-DEMO-3", "merchant_id": "MERCHANT-DEMO-3"},
    "S4": {"user_id": "USER-DEMO-4", "merchant_id": "MERCHANT-DEMO-4"},
    "S5": {"user_id": "USER-DEMO-5", "merchant_id": "MERCHANT-DEMO-5"},
    "S6": {"user_id": "USER-DEMO-1", "merchant_id": "MERCHANT-DEMO-1"},
}


def scenario_fixture(key: str) -> dict:
    """The clean fixture with the scenario's identity applied. Shared by the
    demo router (prepare) and scripts/seed_demo.py so both paths seed the
    SAME data."""
    fixture = dict(_CLEAN_FIXTURE)
    fixture.update(SCENARIO_IDENTITIES.get(key, {}))
    return fixture


def scenario_transaction_id(key: str) -> str:
    return f"DEMO-{key}"


def scenario_start(key: str) -> datetime:
    """Deterministic domain-time base for one scenario's events."""
    return DEMO_BASE + timedelta(minutes=int(key[1:]) - 1)


def _fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def build_events(transaction_id: str, steps_key: str, start: datetime) -> list[dict]:
    """Deterministic event chain — no RNG at all: fixed latencies, fixed
    timestamps derived from `start`."""
    events: list[dict] = []
    elapsed_ms = 0.0
    for seq, (event_type, extra_delay_ms) in enumerate(_STEPS[steps_key]):
        info = EVENT_TYPE_INFO[event_type]
        elapsed_ms += extra_delay_ms
        latency_ms = (
            TERMINAL_EVENT_MS if info["outcome"] == "TIMEOUT"
            else BASE_LATENCY_MS + seq * 40
        )
        elapsed_ms += latency_ms
        events.append({
            "provider_event_id": f"{transaction_id}-{event_type}-{seq:03d}",
            "event_type": event_type,
            "source": info["source"],
            "status": info["outcome"],
            "event_timestamp": _fmt_ts(start + timedelta(milliseconds=elapsed_ms)),
            "reference_id": f"{transaction_id}-{info['stage'].lower()}-ref",
            "latency_ms": latency_ms,
        })
    return events


def build_late_settlement_event(transaction_id: str, start: datetime) -> dict:
    """The S5 race event: a SETTLEMENT_CONFIRMED whose domain timestamp is
    well AFTER the merchant_timeout chain — deterministic, and idempotent per
    provider_event_id (seq 010 is never used by the prepare chain)."""
    info = EVENT_TYPE_INFO["SETTLEMENT_CONFIRMED"]
    return {
        "provider_event_id":
            f"{transaction_id}-SETTLEMENT_CONFIRMED-{LATE_SETTLEMENT_SEQ:03d}",
        "event_type": "SETTLEMENT_CONFIRMED",
        "source": info["source"],
        "status": info["outcome"],
        "event_timestamp": _fmt_ts(
            start + timedelta(milliseconds=LATE_SETTLEMENT_OFFSET_MS)
        ),
        "reference_id": f"{transaction_id}-settlement-ref",
        "latency_ms": BASE_LATENCY_MS,
    }


class DemoInvalidStepError(AppError):
    """inject-late-settlement on a scenario that has no such step."""

    status_code = 400
    code = "DEMO_INVALID_STEP"


class DemoScenarioNotFoundError(AppError):
    """Unknown scenario key."""

    status_code = 404
    code = "DEMO_SCENARIO_NOT_FOUND"


def _require_scenario(key: str) -> dict:
    spec = SCENARIOS.get(key)
    if spec is None:
        raise DemoScenarioNotFoundError(f"unknown demo scenario: {key}")
    return spec


def prepare_scenario(db, key: str) -> list[str]:
    """Prepare one scenario through the REAL service layer only:
    (1) create the transaction if absent (create-or-adopt),
    (2) ingest the deterministic event chain via payment_event_service
        (idempotent per provider_event_id),
    (3) run the risk assessment via the risk engine (fingerprint reuse).
    NEVER runs recovery/process — the §30 no-bypass invariant. Commits like
    the production routes do. Returns a human-readable actions list."""
    from api.schemas.payment_events import PaymentEventIn
    from api.schemas.transaction import TransactionEventRequest
    from api.core.config import get_settings
    from api.services.ml_service import get_ml_service
    from api.services.payment_event_service import ingest_events
    from api.services.risk_engine import (
        persist_assessment,
        record_anomaly_classified,
        run_assessment,
    )
    from api.services.transaction_service import get_transaction, record_event

    spec = _require_scenario(key)
    tid = scenario_transaction_id(key)
    actions: list[str] = []

    # 1. transaction create-or-adopt (the fixture payload is idempotent)
    if get_transaction(db, tid) is None:
        payload = TransactionEventRequest(
            transaction_id=tid, **scenario_fixture(key)
        )
        record_event(db, payload, get_ml_service(), get_settings())
        actions.append("transaction_created")
    else:
        actions.append("transaction_exists")

    # 2. event evidence via the real ingestion service (duplicate-safe)
    events = build_events(tid, spec["steps"], scenario_start(key))
    if events:
        result = ingest_events(db, tid, [PaymentEventIn(**e) for e in events])
        actions.append(
            f"events_ingested(created={result['created']}, "
            f"duplicates={result['duplicates']})"
        )
    else:
        actions.append("events_skipped")

    # 3. risk assessment via the real engine (fingerprint-reuse = idempotent)
    tx = get_transaction(db, tid)
    assessment, fingerprint, reused = run_assessment(
        db, tx, get_ml_service(), customer_reported_failure=False
    )
    if not reused:
        persist_assessment(db, tx, assessment, fingerprint)
        record_anomaly_classified(db, tx, assessment, fingerprint)
        db.commit()
        actions.append("assessment_created")
    else:
        actions.append("assessment_reused")

    logger.info("demo scenario prepared: key=%s actions=%s", key, actions)
    return actions


def inject_late_settlement(db, key: str) -> list[str]:
    """Stage 10 S5 race step: ingest the late SETTLEMENT_CONFIRMED through
    the REAL ingestion service (idempotent per provider_event_id). Only S5
    has this step; anything else is a 400 DEMO_INVALID_STEP."""
    from api.schemas.payment_events import PaymentEventIn
    from api.services.payment_event_service import ingest_events

    _require_scenario(key)
    if key != "S5":
        raise DemoInvalidStepError(
            "inject-late-settlement applies only to scenario S5 "
            "(the settlement race)"
        )
    tid = scenario_transaction_id(key)
    event = build_late_settlement_event(tid, scenario_start(key))
    result = ingest_events(db, tid, [PaymentEventIn(**event)])
    actions = [
        "late_settlement_ingested(created={created}, duplicates={duplicates})".format(
            created=result["created"], duplicates=result["duplicates"]
        )
    ]
    logger.info("demo late settlement injected: key=%s actions=%s", key, actions)
    return actions


def purge_demo_rows(db) -> int:
    """Delete ALL rows for transaction_id LIKE 'DEMO-S%' across the child
    tables (FK-safe order: ai_explanations, risk_assessments,
    recovery_actions, recovery_decisions, digital_twin_events,
    payment_events, then transactions). Bulk deletes with
    synchronize_session=False. Returns the number of transactions removed."""
    from api.db.models import (
        AIExplanation,
        DigitalTwinEvent,
        PaymentEvent,
        RecoveryActionRecord,
        RecoveryDecision,
        RiskAssessmentRecord,
        Transaction,
    )

    deleted = 0
    for model in (
        AIExplanation,
        RiskAssessmentRecord,
        RecoveryActionRecord,
        RecoveryDecision,
        DigitalTwinEvent,
        PaymentEvent,
    ):
        deleted += (
            db.query(model)
            .filter(model.transaction_id.like(f"{DEMO_TRANSACTION_PREFIX}%"))
            .delete(synchronize_session=False)
        )
    transactions_deleted = (
        db.query(Transaction)
        .filter(Transaction.transaction_id.like(f"{DEMO_TRANSACTION_PREFIX}%"))
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info("demo purge: transactions=%d child_rows=%d",
                transactions_deleted, deleted)
    return transactions_deleted


def reset_demo_corpus(db) -> dict:
    """Full deterministic reset: (1) purge all DEMO-S* evidence/state rows,
    (2) reset the SIMULATED sandbox provider + its persisted ledger rows
    (mirroring /api/v1/sandbox/reset), (3) re-prepare all six scenarios.
    Returns {"transactions_deleted": n, "reseeded": 6}."""
    from api.db.models import SandboxLedgerEntry
    from api.services.payment_provider import get_payment_provider

    transactions_deleted = purge_demo_rows(db)

    # SIMULATED ledger only — in-memory provider + persisted rows, exactly
    # what the sandbox reset endpoint does
    get_payment_provider().reset()
    db.query(SandboxLedgerEntry).delete(synchronize_session=False)
    db.commit()

    for key in SCENARIOS:
        prepare_scenario(db, key)
    return {"transactions_deleted": transactions_deleted, "reseeded": len(SCENARIOS)}

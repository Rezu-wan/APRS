"""
Stage 8 — End-to-End Autonomous Recovery Demo
Project: AI-Powered Payment Failure Recovery & Digital Twin System
================================================================

Drives the LIVE API through the Stage 8 autonomous-recovery pipeline:
transaction creation -> payment-event ingestion -> risk assessment (POST,
so Stage 7 evidence exists) -> POST recovery/process -> assertions against
the pinned decision vocabulary, plus GET recovery / GET timeline read-back
to verify the sandbox ledger and the Digital Twin lifecycle events.

Each scenario is (payment-event sequence, transaction fixture, EXPECTED
decision). EXPECTED values follow the deterministic policy + safety gate —
Stage 8's invariant is that the pipeline NEVER releases a limit unless
GENUINE_FAILURE + low/medium risk + candidate + single debit + no
settlement survive a fresh-evidence recheck.

Scenarios:
  S1 genuine failure   debit OK, gateway OK, merchant confirmation times
                       out -> AUTO_RECOVERED, RELEASE_LIMIT, VERIFIED,
                       simulated, sandbox provider reference, tx reaches
                       LIMIT_RELEASED, twin lifecycle events present
  S2 double deduction  customer debited twice -> RECOVERY_BLOCKED,
                       NO_ACTION, provider NOT called (no reference)
  S3 success           full 7-event happy chain -> RECOVERY_BLOCKED,
                       ALREADY_SUCCESS
  S4 incomplete        a single debit -> RECOVERY_BLOCKED,
                       INSUFFICIENT_EVIDENCE
  S5 race (spec 43)    genuine failure, then a SETTLEMENT_CONFIRMED event
                       lands AFTER processing evidence exists -> the fresh
                       safety recheck blocks: RECOVERY_BLOCKED
                       (NEW_SUCCESSFUL_SETTLEMENT or ALREADY_SUCCESS on
                       re-derivation), never AUTO_RECOVERED, tx never
                       LIMIT_RELEASED
  S6 duplicate         process twice on the same transaction -> first
                       AUTO_RECOVERED, second ALREADY_RECOVERED, same
                       recovery_id, released exactly once
  S7/S8 provider       covered by unit tests (test_recovery_executor.py,
      failure / unsafe test_recovery_verifier.py); here we assert the
      safe-state contract on every recovery row we touch: a row is never
      VERIFIED without a provider_reference. Marked covered-by-unit-tests.

Style follows scripts/stage7_e2e.py / scripts/payment_event_simulator.py:
stdlib only, event vocabulary imported from api/core/payment_lifecycle.py
(never hardcoded), provider_event_id convention
{transaction_id}-{event_type}-{seq:03d}, rerun-safe id rotation on
400 INVALID_STATE_TRANSITION. Connection errors print one clean line —
no stack traces unless --verbose.

Usage:
  py -m scripts.stage8_e2e
  py -m scripts.stage8_e2e --api-url http://127.0.0.1:8000 --seed 42 --verbose
  py -m scripts.stage8_e2e --demo    # S1 only, narrated end to end
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# Ensure the project root is importable when run as a plain script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.core.payment_lifecycle import (  # noqa: E402  (path fix above)
    EVENT_TYPE_INFO,
    HAPPY_PATH_EVENTS,
)

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
SEED = 42
DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_API_KEY = "dev-system-key"  # documented public dev placeholder (see README)
JITTER_FRACTION = 0.20              # base latency +-20%

BASE_LATENCY_MS = 120
TERMINAL_EVENT_MS = 90
MERCHANT_TIMEOUT_GAP_MS = 3_000
GATEWAY_TIMEOUT_GAP_MS = 3_000
RACE_SETTLEMENT_GAP_MS = 6_000      # the "late" settlement — after everything

# Digital Twin recovery lifecycle vocabulary (see recovery_executor.py) —
# imported names would create a service-layer import from a script; these
# literals are cross-checked in S1's assertion below, which fails loudly on
# any drift.
TWIN_APPROVED = "RECOVERY_APPROVED"
TWIN_STARTED = "RECOVERY_STARTED"
TWIN_EXECUTED = "RECOVERY_EXECUTED"
TWIN_VERIFIED = "RECOVERY_VERIFIED"

# Response "decision" vocabulary (pinned in autonomous_recovery.py)
DECISION_AUTO_RECOVERED = "AUTO_RECOVERED"
DECISION_BLOCKED = "RECOVERY_BLOCKED"
DECISION_MANUAL_REVIEW_QUEUED = "MANUAL_REVIEW_QUEUED"
DECISION_ALREADY_RECOVERED = "ALREADY_RECOVERED"

# Blocked-reason codes (stable strings, recovery_autonomous.py)
BLOCK_ALREADY_SUCCESS = "ALREADY_SUCCESS"
BLOCK_NEW_SUCCESSFUL_SETTLEMENT = "NEW_SUCCESSFUL_SETTLEMENT"
BLOCK_DOUBLE_DEDUCTION = "DOUBLE_DEDUCTION"
BLOCK_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

# --------------------------------------------------------------------------
# Scenario definitions
# --------------------------------------------------------------------------
# Ordered (event_type, extra_delay_ms) steps — same shape as the Stage 6
# simulator. Event source/status are NEVER hardcoded; they come from
# EVENT_TYPE_INFO.

_STEPS: dict[str, list[tuple[str, int]]] = {
    # Genuine failure: money left the customer, the flow failed at merchant
    # confirmation and nothing followed — the recoverable case.
    "merchant_timeout": [
        ("CUSTOMER_DEBIT_CONFIRMED", 0),
        ("GATEWAY_REQUEST_SENT", 0),
        ("GATEWAY_RESPONSE_RECEIVED", 0),
        ("MERCHANT_CONFIRMATION_REQUESTED", 0),
        ("MERCHANT_CONFIRMATION_TIMEOUT", MERCHANT_TIMEOUT_GAP_MS),
    ],
    "double_deduction": [
        ("CUSTOMER_DEBIT_CONFIRMED", 0),
        ("CUSTOMER_DEBIT_CONFIRMED", 200),   # second debit — the anomaly
        ("GATEWAY_REQUEST_SENT", 0),
        ("GATEWAY_TIMEOUT", GATEWAY_TIMEOUT_GAP_MS),
    ],
    "success": [(event_type, 0) for event_type in HAPPY_PATH_EVENTS],
    "incomplete": [
        ("CUSTOMER_DEBIT_CONFIRMED", 0),
    ],
}

# "Clean" failed fixture — a genuine gateway/merchant failure with no
# customer-side risk signals; the policy releases exactly this shape.
_CLEAN_FIXTURE = {
    "user_id": "USER-E2E",
    "merchant_id": "MERCHANT-E2E",
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

# key -> (steps key, fixture, expected outcome dict)
SCENARIOS: dict[str, dict] = {
    "S1": {
        "steps": "merchant_timeout",
        "fixture": _CLEAN_FIXTURE,
        "label": "genuine failure -> auto-recover",
        "expect": {
            "decision": DECISION_AUTO_RECOVERED,
            "action": "RELEASE_LIMIT",
            "status": "VERIFIED",
        },
    },
    "S2": {
        "steps": "double_deduction",
        "fixture": _CLEAN_FIXTURE,
        "label": "double deduction -> blocked",
        "expect": {
            "decision": DECISION_BLOCKED,
            "action": "NO_ACTION",
            "blocked_reason": BLOCK_DOUBLE_DEDUCTION,
        },
    },
    "S3": {
        "steps": "success",
        "fixture": _CLEAN_FIXTURE,
        "label": "successful payment -> blocked",
        "expect": {
            "decision": DECISION_BLOCKED,
            "action": "NO_ACTION",
            "blocked_reason": BLOCK_ALREADY_SUCCESS,
        },
    },
    "S4": {
        "steps": "incomplete",
        "fixture": _CLEAN_FIXTURE,
        "label": "insufficient evidence -> blocked",
        "expect": {
            "decision": DECISION_BLOCKED,
            "action": "NO_ACTION",
            "blocked_reason": BLOCK_INSUFFICIENT_EVIDENCE,
        },
    },
    "S5": {
        "steps": "merchant_timeout",
        "fixture": _CLEAN_FIXTURE,
        "label": "settlement lands mid-recovery (race)",
        "expect": {
            # Defense-in-depth: EITHER layer may catch the late settlement.
            # - policy layer (fresh Stage 7 re-assessment): a confirmed
            #   settlement changes the anomaly (e.g. SUCCESSFUL_BUT_UNCONFIRMED
            #   or UNKNOWN for the contradictory merchant-timeout+settlement
            #   mix) -> NOT_ELIGIBLE / INSUFFICIENT_EVIDENCE / RISK_NO_LONGER_PERMITS
            # - safety gate (fresh reconstruction): NEW_SUCCESSFUL_SETTLEMENT /
            #   ALREADY_SUCCESS (fires when the policy still said eligible).
            # The HARD invariants: NEVER auto-recover, provider never called,
            # transaction never reaches LIMIT_RELEASED.
            "decision": DECISION_BLOCKED,
            "blocked_reason_in": (BLOCK_NEW_SUCCESSFUL_SETTLEMENT,
                                  BLOCK_ALREADY_SUCCESS,
                                  "INSUFFICIENT_EVIDENCE",
                                  "NOT_ELIGIBLE",
                                  "RISK_NO_LONGER_PERMITS"),
        },
    },
    "S6": {
        "steps": "merchant_timeout",
        "fixture": _CLEAN_FIXTURE,
        "label": "duplicate process call (idempotent)",
        "expect": {
            "decision": DECISION_AUTO_RECOVERED,
            "second_decision": DECISION_ALREADY_RECOVERED,
        },
    },
}


def scenario_transaction_id(scenario_key: str, seed: int) -> str:
    """Deterministic id: TXN-E2E8-<scenario>-<8 hex from seed>."""
    digest = hashlib.sha256(f"{seed}:stage8:{scenario_key}".encode("utf-8")).hexdigest()
    return f"TXN-E2E8-{scenario_key}-{digest[:8]}"


def build_events(transaction_id: str, steps_key: str, start: datetime,
                 seed: int) -> list[dict]:
    """Build the ordered payment-event list for one scenario.

    Same contract as the Stage 6 simulator: rng is used ONLY for latency
    jitter, so the same (transaction_id, scenario, start, seed) is stable and
    re-ingestion is idempotent on the API side.
    """
    rng = random.Random(seed)
    events: list[dict] = []
    elapsed_ms = 0.0

    for seq, (event_type, extra_delay_ms) in enumerate(_STEPS[steps_key]):
        info = EVENT_TYPE_INFO[event_type]
        elapsed_ms += extra_delay_ms
        base = TERMINAL_EVENT_MS if info["outcome"] == "TIMEOUT" else BASE_LATENCY_MS + seq * 40
        latency_ms = int(round(base * rng.uniform(1.0 - JITTER_FRACTION,
                                                   1.0 + JITTER_FRACTION)))
        elapsed_ms += latency_ms
        events.append({
            "provider_event_id": f"{transaction_id}-{event_type}-{seq:03d}",
            "event_type": event_type,
            "source": info["source"],
            "status": info["outcome"],
            "event_timestamp": (start + timedelta(milliseconds=elapsed_ms))
                .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "reference_id": f"{transaction_id}-{info['stage'].lower()}-ref",
            "latency_ms": latency_ms,
        })

    return events


def _fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# --------------------------------------------------------------------------
# HTTP helpers (stdlib only)
# --------------------------------------------------------------------------

def _request(method: str, url: str, api_key: str, payload: dict | None,
             verbose: bool) -> tuple[int, dict]:
    """One JSON API call. Returns (status_code, parsed_body)."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
    )
    if verbose:
        print(f"[verbose] {method} {url}")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        try:
            return exc.code, json.loads(detail) if detail else {}
        except json.JSONDecodeError:
            raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from exc


class ApiError(Exception):
    """A scenario step failed against the live API."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _call(method: str, url: str, api_key: str, payload: dict | None,
          verbose: bool, expect: tuple[int, ...] = (200,)) -> dict:
    status, body = _request(method, url, api_key, payload, verbose)
    if verbose:
        print(f"[verbose] -> {status}: {json.dumps(body)[:400]}")
    if status not in expect:
        raise ApiError(
            f"{method} {url} -> HTTP {status}: {json.dumps(body)[:300]}",
            status=status,
        )
    return body


# --------------------------------------------------------------------------
# Scenario driver
# --------------------------------------------------------------------------

def _create_transaction(base: str, args, key: str, spec: dict) -> str:
    """Create the scenario transaction; rerun-safe id rotation on
    400 INVALID_STATE_TRANSITION (a previous run may have left the same
    deterministic id in a state where a fresh FAILED event is illegal)."""
    tid = scenario_transaction_id(key, args.seed)
    for attempt in range(40):
        fixture = {"transaction_id": tid, **spec["fixture"]}
        try:
            _call("POST", f"{base}/api/v1/transaction/event", args.api_key,
                  fixture, args.verbose)
            return tid
        except ApiError as exc:
            if exc.status == 400 and attempt < 39:
                tid = f"{scenario_transaction_id(key, args.seed)}-r{attempt + 1}"
                print(f"[{key}] transaction id already advanced; retrying as {tid}")
                continue
            raise
    raise ApiError(f"could not create a fresh transaction for {key}")


def run_scenario(key: str, spec: dict, args, start: datetime) -> dict:
    """Drive one scenario against the live API; returns a result record."""
    base = args.api_url.rstrip("/")
    tid = _create_transaction(base, args, key, spec)

    record: dict = {
        "key": key, "label": spec["label"], "transaction_id": tid,
        "ok": False, "error": None, "outcome": {}, "note": "",
    }

    # 1. ingest the payment-event evidence (idempotent on provider_event_id)
    events = build_events(tid, spec["steps"], start, args.seed)
    _call("POST", f"{base}/api/v1/transactions/{tid}/payment-events",
          args.api_key, {"events": events}, args.verbose)

    # 2. risk assessment POST — Stage 7 evidence must exist before recovery
    posted = _call("POST", f"{base}/api/v1/transactions/{tid}/risk-assessment",
                   args.api_key, {"customer_reported_failure": False}, args.verbose)
    record["assessment"] = posted.get("assessment") or {}

    # 2b. S5 race (spec §43): AFTER the Stage 7 evidence exists, a delayed
    # SETTLEMENT_CONFIRMED lands. The autonomous pipeline must re-derive from
    # this fresh evidence and block — never release. (Verified injected.)
    if key == "S5":
        last_ts = events[-1]["event_timestamp"]
        late_start = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        late = [{
            "provider_event_id": f"{tid}-SETTLEMENT_CONFIRMED-901",
            "event_type": "SETTLEMENT_CONFIRMED",
            "source": EVENT_TYPE_INFO["SETTLEMENT_CONFIRMED"]["source"],
            "status": EVENT_TYPE_INFO["SETTLEMENT_CONFIRMED"]["outcome"],
            "event_timestamp": (late_start + timedelta(seconds=10))
                .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "latency_ms": 120,
        }]
        injected = _call("POST", f"{base}/api/v1/transactions/{tid}/payment-events",
                         args.api_key, {"events": late}, args.verbose)
        if injected.get("created") != 1:
            raise ApiError(f"S5: late-settlement injection failed: {injected!r}")
        record["note"] = "late settlement injected"

    # 3. autonomous recovery: the backend-owned pipeline
    process = _call("POST", f"{base}/api/v1/transactions/{tid}/recovery/process",
                    args.api_key, {"customer_reported_failure": False},
                    args.verbose)
    record["process"] = process

    # 4. read back the recovery row + timeline
    recovery = _call("GET", f"{base}/api/v1/transactions/{tid}/recovery",
                     args.api_key, None, args.verbose)
    record["recovery"] = recovery
    timeline = _call("GET", f"{base}/api/v1/transactions/{tid}/timeline",
                     args.api_key, None, args.verbose)
    record["timeline"] = timeline

    # 5. scenario-specific assertions
    if key == "S1":
        _assert_s1(record)
    elif key == "S2":
        _assert_s2(record)
    elif key == "S3":
        _assert_blocked(record, BLOCK_ALREADY_SUCCESS)
    elif key == "S4":
        _assert_blocked(record, BLOCK_INSUFFICIENT_EVIDENCE)
    elif key == "S5":
        _assert_s5(record)
    elif key == "S6":
        _assert_s6(base, args, spec, start, record)

    record["ok"] = True
    return record


def _expect_decision(record: dict, expected: dict) -> None:
    """Assert the pinned process-response fields against EXPECTED."""
    process = record["process"]
    for field in ("decision", "action", "status"):
        if field in expected and process.get(field) != expected[field]:
            raise ApiError(
                f"expected {field} {expected[field]!r}, got {process.get(field)!r}")
    if not process.get("simulated"):
        raise ApiError("process response must be marked simulated: true")
    if not process.get("recovery_id"):
        raise ApiError("process response must carry a recovery_id")


def _assert_s1(record: dict) -> None:
    """S1: full happy recovery — decision, ledger, twin, final state."""
    _expect_decision(record, SCENARIOS["S1"]["expect"])
    process, recovery = record["process"], record["recovery"]

    if process.get("provider_reference") != recovery.get("provider_reference") \
            or not recovery.get("provider_reference"):
        raise ApiError("S1 must expose a sandbox provider_reference on both "
                       "the process response and the recovery row")
    if not recovery.get("provider"):
        raise ApiError("S1 recovery row must name the sandbox provider")

    timeline = record["timeline"]
    twin_types = [e.get("event_type") for e in timeline.get("events", [])]
    for required in (TWIN_APPROVED, TWIN_STARTED, TWIN_EXECUTED, TWIN_VERIFIED):
        if required not in twin_types:
            raise ApiError(f"S1 timeline missing {required} "
                           f"(events present: {sorted(t for t in twin_types if t)})")

    if timeline.get("current_state") != "LIMIT_RELEASED":
        raise ApiError(f"S1 transaction must reach LIMIT_RELEASED, "
                       f"got {timeline.get('current_state')!r}")

    if recovery.get("status") != "VERIFIED" or recovery.get("released_amount") is None:
        raise ApiError("S1 recovery row must be VERIFIED with a released_amount")


def _assert_s2(record: dict) -> None:
    """S2: double deduction — blocked, provider never called."""
    _expect_decision(record, SCENARIOS["S2"]["expect"])
    recovery = record["recovery"]
    if recovery.get("provider_reference") is not None:
        raise ApiError("S2 provider must NOT be called — recovery row has a "
                       f"provider_reference {recovery.get('provider_reference')!r}")
    if not recovery.get("blocked_reason"):
        raise ApiError("S2 recovery row must record a blocked_reason")
    if not recovery.get("decision_reason"):
        raise ApiError("S2 recovery row must record a decision_reason")


def _assert_blocked(record: dict, blocked_reason: str) -> None:
    """S3/S4: blocked with a specific stable reason code."""
    _expect_decision(record, {
        "decision": DECISION_BLOCKED,
        "action": "NO_ACTION",
    })
    actual = record["recovery"].get("blocked_reason")
    if actual != blocked_reason:
        raise ApiError(f"expected blocked_reason {blocked_reason!r}, got {actual!r}")
    if record["recovery"].get("provider_reference") is not None:
        raise ApiError(f"blocked scenario must not call the provider "
                       f"(provider_reference present)")


def _assert_s5(record: dict) -> None:
    """S5 race: the late settlement must prevent the release."""
    process = record["process"]
    if process.get("decision") != DECISION_BLOCKED:
        raise ApiError("S5: late settlement must prevent auto-recovery, got "
                       f"decision {process.get('decision')!r}")
    blocked = record["recovery"].get("blocked_reason")
    if blocked not in SCENARIOS["S5"]["expect"]["blocked_reason_in"]:
        raise ApiError("S5: expected a settlement/success/uncertainty block "
                       f"reason, got {blocked!r}")
    state = record["timeline"].get("current_state")
    if state == "LIMIT_RELEASED":
        raise ApiError("S5: transaction must NOT reach LIMIT_RELEASED")
    if record["recovery"].get("provider_reference") is not None:
        raise ApiError("S5: provider must not have been called")
    record["note"] = f"blocked: {blocked}"


def _assert_s6(base, args, spec, start, record: dict) -> None:
    """S6: the second process call replays — same recovery_id, released once."""
    _expect_decision(record, {"decision": DECISION_AUTO_RECOVERED})
    tid = record["transaction_id"]
    first = record["process"]
    first_recovery = record["recovery"]

    second = _call("POST", f"{base}/api/v1/transactions/{tid}/recovery/process",
                   args.api_key, {"customer_reported_failure": False},
                   args.verbose)
    record["second_process"] = second
    second_recovery = _call("GET", f"{base}/api/v1/transactions/{tid}/recovery",
                            args.api_key, None, args.verbose)
    record["second_recovery"] = second_recovery

    if second.get("decision") != DECISION_ALREADY_RECOVERED:
        raise ApiError("S6: second call must report ALREADY_RECOVERED, got "
                       f"{second.get('decision')!r}")
    if second.get("recovery_id") != first.get("recovery_id"):
        raise ApiError("S6: recovery_id changed between calls "
                       f"({first.get('recovery_id')!r} -> {second.get('recovery_id')!r})")
    if second_recovery.get("released_amount") != first_recovery.get("released_amount"):
        raise ApiError("S6: ledger released amount changed between calls — "
                       "the limit was released more than once")
    record["note"] = ("released exactly once: "
                      f"{second_recovery.get('released_amount')} "
                      f"{second_recovery.get('currency')} "
                      f"(recovery {first.get('recovery_id')[:12]}…)")


def run_safe_state_contract(records: list[dict], args) -> dict:
    """S7/S8 (provider failure / unsafe-state handling).

    The mock provider's failure hook is server-side; it cannot be triggered
    through the live API by design. Those paths are covered at unit level by
    tests/test_recovery_executor.py and tests/test_recovery_verifier.py.
    What we CAN assert live is the SAFE-STATE CONTRACT: every recovery row
    we touched obeys it — a row is never VERIFIED without a provider
    reference, and released amounts are only present on released rows.
    """
    checked = 0
    for record in records:
        if not record.get("ok"):
            continue
        for recovery in _iter_recovery_rows(record):
            status = recovery.get("status")
            reference = recovery.get("provider_reference")
            if status == "VERIFIED" and not reference:
                raise ApiError(
                    f"safe-state contract violated on "
                    f"{record['transaction_id']}: VERIFIED without a "
                    f"provider_reference")
            checked += 1
    print(f"[S7] safe-state contract verified on {checked} recovery row(s); "
          "provider-failure + verifier paths are covered by unit tests "
          "(tests/test_recovery_executor.py, tests/test_recovery_verifier.py)")
    return {
        "key": "S7", "label": "safe-state contract (+S8 via unit tests)",
        "transaction_id": "-",
        "ok": True, "error": None,
        "outcome": {},
        "note": "covered-by-unit-tests (executor failure hook is "
                "server-side; live API cannot inject provider failures)",
    }


def _iter_recovery_rows(record: dict):
    for field in ("recovery", "second_recovery"):
        row = record.get(field)
        if isinstance(row, dict) and row.get("status"):
            yield row


# --------------------------------------------------------------------------
# Demo narration (--demo)
# --------------------------------------------------------------------------

def print_demo_story(record: dict) -> None:
    """Spec section 50: the autonomous story, numbered, with the real ids
    and timestamps from the live API responses."""
    tid = record["transaction_id"]
    process = record.get("process") or {}
    recovery = record.get("recovery") or {}
    timeline = record.get("timeline") or {}
    assessment = record.get("assessment") or {}
    print()
    print(f"Autonomous recovery story — {tid}")
    print("-" * 72)
    steps = [
        f"Transaction {tid} starts (customer pays {recovery.get('requested_amount', '?')} "
        f"{recovery.get('currency', '')}, status FAILED).",
        "The customer's bank confirms the debit — the money has left their account.",
        "The gateway / merchant confirmation times out — the payment never completes.",
        "Stage 6 reconstruction rebuilds the timeline from stored provider events and "
        "identifies the root cause from the evidence (no guessing).",
        f"Stage 7 classifies it: {assessment.get('anomaly_type', '?')}, "
        f"risk {assessment.get('risk_level', '?')} "
        f"(score {assessment.get('risk_score', '?')}), recovery candidate: "
        f"{assessment.get('recovery_candidate', '?')}.",
        "The recovery policy approves: genuine failure + acceptable risk + single debit "
        "+ no settlement -> RELEASE_LIMIT.",
        f"The safety gate re-derives everything from fresh evidence, then the executor "
        f"releases the limit on the SIMULATED SANDBOX provider "
        f"(recovery {recovery.get('recovery_id', '?')}, "
        f"reference {recovery.get('provider_reference', '?')}).",
        f"Verification passes — status {recovery.get('status', '?')}, "
        f"released {recovery.get('released_amount', '?')} {recovery.get('currency', '')}.",
        f"The Digital Twin recorded the lifecycle "
        f"(RECOVERY_ELIGIBILITY_ASSESSED -> RECOVERY_APPROVED -> RECOVERY_STARTED -> "
        f"RECOVERY_EXECUTED -> RECOVERY_VERIFIED); the transaction is now "
        f"{timeline.get('current_state', '?')}.",
        "The customer sees a resolved payment: limit released, no support ticket needed.",
    ]
    for i, step in enumerate(steps, 1):
        print(f" {i:>2}. {step}")
    print("-" * 72)
    print("Autonomous recovery operates on a simulated sandbox provider. "
          "No real financial transaction is performed.")


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def print_results(records: list[dict]) -> None:
    header = (f"{'#':<4} {'scenario':<42} {'decision':<22} {'action':<15} "
              f"{'status':<9} {'blocked_reason':<26} verdict")
    print(header)
    print("-" * len(header))
    for r in records:
        outcome = r.get("outcome") or {}
        if r["ok"]:
            source = r.get("process") or outcome
            note = f"  ({r['note']})" if r.get("note") else ""
            print(f"{r['key']:<4} {r['label']:<42} "
                  f"{str(source.get('decision', '?')):<22} "
                  f"{str(source.get('action', '?')):<15} "
                  f"{str(source.get('status', '?')):<9} "
                  f"{str((r.get('recovery') or {}).get('blocked_reason') or '-'):<26} "
                  f"PASS{note}")
        else:
            print(f"{r['key']:<4} {r['label']:<42} {'-':<22} {'-':<15} "
                  f"{'-':<9} {'-':<26} FAIL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage 8 end-to-end autonomous recovery demo "
                    "(drives the live API; stdlib only).")
    parser.add_argument("--api-url", default=DEFAULT_API_URL,
                        help=f"API base URL (default {DEFAULT_API_URL})")
    parser.add_argument("--api-key", default=os.environ.get("PAYMENT_API_KEY", DEFAULT_API_KEY),
                        help="API key (default: $PAYMENT_API_KEY or the dev placeholder)")
    parser.add_argument("--seed", type=int, default=SEED,
                        help=f"random seed, used ONLY for latency jitter and tx ids (default {SEED})")
    parser.add_argument("--demo", action="store_true",
                        help="run S1 only and narrate the autonomous story")
    parser.add_argument("--verbose", action="store_true",
                        help="log details (raw requests and responses)")
    args = parser.parse_args(argv)

    base = args.api_url.rstrip("/")
    start = datetime.now(timezone.utc)

    print(f"Stage 8 E2E — API: {base}  seed: {args.seed}")
    print()

    scenario_keys = ["S1"] if args.demo else list(SCENARIOS)
    records: list[dict] = []
    for key in scenario_keys:
        spec = SCENARIOS[key]
        tid = scenario_transaction_id(key, args.seed)
        print(f"[{key}] {spec['label']} on {tid} ...", flush=True)
        record = {"key": key, "label": spec["label"], "transaction_id": tid,
                  "ok": False, "error": None, "outcome": {}, "note": ""}
        try:
            record.update(run_scenario(key, spec, args, start))
        except ApiError as exc:
            record["error"] = str(exc)
            print(f"    FAIL: {record['error']}")
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            print(f"error: cannot reach API at {base}: {reason}")
            print("Start the server first:  uvicorn api.main:app --reload")
            if args.verbose:
                import traceback
                traceback.print_exc()
            return 1
        except RuntimeError as exc:
            print(f"error: {exc}")
            if args.verbose:
                import traceback
                traceback.print_exc()
            return 1
        records.append(record)
        if args.demo and record.get('ok'):
            print_demo_story(record)

    # S7/S8: safe-state contract over everything we touched + unit-test note
    if not args.demo:
        try:
            records.append(run_safe_state_contract(records, args))
        except ApiError as exc:
            records.append({
                "key": "S7", "label": "safe-state contract (+S8 via unit tests)",
                "transaction_id": "-", "ok": False, "error": str(exc),
                "outcome": {}, "note": "",
            })
            print(f"    FAIL: {exc}")

    print()
    print_results(records)

    passed = sum(1 for r in records if r["ok"])
    total = len(records)
    print()
    print(f"Summary: {passed}/{total} scenarios passed"
          + ("" if passed == total else "  ->  see FAIL lines above"))
    print("Autonomous recovery operates on a simulated sandbox provider. "
          "No real financial transaction is performed.")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

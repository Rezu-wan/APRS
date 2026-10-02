"""
Stage 7 — End-to-End Risk & Anomaly Classification Demo
Project: AI-Powered Payment Failure Recovery & Digital Twin System
================================================================

Drives the LIVE API through five scenarios and verifies the Stage 7 hybrid
risk engine end to end: transaction creation -> payment-event ingestion ->
risk assessment (POST) -> assessment read-back (GET) -> Digital Twin check
(ANOMALY_CLASSIFIED observation present in the timeline).

Each scenario is (payment-event sequence, transaction fixture, EXPECTED
anomaly_type). The EXPECTED category is determined by the deterministic
evidence rules — Stage 7's invariant is that the deterministic category
ALWAYS wins over the ML signal, so these verdicts are stable regardless of
what the anomaly model scores.

Scenarios:
  S1 gateway_timeout    debit OK, request sent, gateway times out
                        -> GENUINE_FAILURE, recovery candidate
  S2 double deduction   customer debited twice, one gateway attempt fails
                        -> DOUBLE_DEDUCTION, NOT a recovery candidate
  S3 success            all 7 happy-path events -> NONE
  S4 incomplete         a single debit confirmation, nothing else -> INCOMPLETE
  S5 suspicious-evidence gateway timeout on a high-risk transaction
                        (retries, prior failures, poor network)
                        -> GENUINE_FAILURE (evidence wins precedence over the
                        elevated ML score; the score is printed, not obeyed)

Style follows scripts/payment_event_simulator.py: stdlib only, event
vocabulary imported from api/core/payment_lifecycle.py (never hardcoded),
provider_event_id convention {transaction_id}-{event_type}-{seq:03d},
plausible ascending timestamps. The default --api-url points at the local
dev server; override with the flag. Clean connection-error handling — no
stack traces unless --verbose.

Usage:
  py -m scripts.stage7_e2e
  py -m scripts.stage7_e2e --api-url http://127.0.0.1:8000 --seed 42 --verbose
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
GATEWAY_TIMEOUT_GAP_MS = 3_000

# --------------------------------------------------------------------------
# Scenario definitions
# --------------------------------------------------------------------------
# Each scenario: ordered (event_type, extra_delay_ms) steps — same shape as
# the Stage 6 simulator — plus a transaction fixture and EXPECTED values.
# Event source/status are NEVER hardcoded; they come from EVENT_TYPE_INFO.

_SCENARIO_STEPS: dict[str, list[tuple[str, int]]] = {
    "gateway_timeout": [
        ("CUSTOMER_DEBIT_CONFIRMED", 0),
        ("GATEWAY_REQUEST_SENT", 0),
        ("GATEWAY_TIMEOUT", GATEWAY_TIMEOUT_GAP_MS),
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

# "Clean" failed fixture: attributes that neither trip the recovery caps nor
# suggest customer-side risk — a genuine gateway failure.
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

# High-risk fixture for S5: attributes an ML anomaly model plausibly scores
# high (frequent retries, history of failures, poor network, mid-size amount).
# EXPECTED anomaly_type is still GENUINE_FAILURE — deterministic gateway-timeout
# evidence outranks the ML signal by design.
_HIGH_RISK_FIXTURE = {
    "user_id": "USER-E2E",
    "merchant_id": "MERCHANT-E2E",
    "amount": 900.00,
    "currency": "BDT",
    "gateway_latency_ms": 4500,
    "retry_count": 5,
    "network_quality": "Poor",
    "previous_failures": 4,
    "account_age_days": 90,
    "status": "FAILED",
    "failure_reason": "Timeout",
}

# key -> (scenario steps key, fixture, expected anomaly_type, expected recovery_candidate)
SCENARIOS: dict[str, dict] = {
    "S1": {"steps": "gateway_timeout",  "fixture": _CLEAN_FIXTURE,     "expected_anomaly": "GENUINE_FAILURE",   "expected_candidate": True},
    "S2": {"steps": "double_deduction", "fixture": _CLEAN_FIXTURE,     "expected_anomaly": "DOUBLE_DEDUCTION",  "expected_candidate": False},
    "S3": {"steps": "success",          "fixture": _CLEAN_FIXTURE,     "expected_anomaly": "NONE",              "expected_candidate": False},
    "S4": {"steps": "incomplete",       "fixture": _CLEAN_FIXTURE,     "expected_anomaly": "INCOMPLETE",        "expected_candidate": False},
    "S5": {"steps": "gateway_timeout",  "fixture": _HIGH_RISK_FIXTURE, "expected_anomaly": "GENUINE_FAILURE",   "expected_candidate": None},  # print only
}


def scenario_transaction_id(scenario_key: str, seed: int) -> str:
    """Deterministic id: TXN-E2E-<scenario>-<8 hex from seed>."""
    digest = hashlib.sha256(f"{seed}:{scenario_key}".encode("utf-8")).hexdigest()
    return f"TXN-E2E-{scenario_key}-{digest[:8]}"


def build_events(transaction_id: str, scenario_key: str, start: datetime,
                 seed: int) -> list[dict]:
    """Build the ordered payment-event list for one E2E scenario.

    Same contract as the Stage 6 simulator: rng is used ONLY for latency
    jitter, so the same (transaction_id, scenario, start, seed) is stable and
    re-ingestion is idempotent on the API side.
    """
    rng = random.Random(seed)
    steps = _SCENARIO_STEPS[SCENARIOS[scenario_key]["steps"]]
    events: list[dict] = []
    elapsed_ms = 0.0

    for seq, (event_type, extra_delay_ms) in enumerate(steps):
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

def run_scenario(key: str, spec: dict, args, start: datetime) -> dict:
    """Drive one scenario against the live API; returns a result record."""
    base = args.api_url.rstrip("/")

    # Deterministic ids are nice-to-have, not load-bearing: if a previous run
    # left the same transaction id in a non-recreatable state (e.g. sitting in
    # RECOVERY_PENDING, where a fresh FAILED event is an illegal transition),
    # rotate a fresh suffix and retry. Payment-event ingestion stays idempotent
    # per generated id, so each rotation is a clean scenario run.
    tid = scenario_transaction_id(key, args.seed)
    for attempt in range(5):
        fixture = {"transaction_id": tid, **spec["fixture"]}
        try:
            # 1. create the transaction (idempotent replays are tolerated)
            _call("POST", f"{base}/api/v1/transaction/event", args.api_key,
                  fixture, args.verbose)
            break
        except ApiError as exc:
            if exc.status == 400 and attempt < 4:
                tid = f"{scenario_transaction_id(key, args.seed)}-r{attempt + 1}"
                print(f"[{key}] transaction id already advanced; retrying as {tid}")
                continue
            raise

    fixture = {"transaction_id": tid, **spec["fixture"]}
    events = build_events(tid, key, start, args.seed)
    record: dict = {"key": key, "transaction_id": tid, "ok": False,
                    "error": None, "assessment": None, "note": ""}

    # 2. ingest the payment-event evidence (idempotent on provider_event_id)
    _call("POST", f"{base}/api/v1/transactions/{tid}/payment-events",
          args.api_key, {"events": events}, args.verbose)

    # 3. risk assessment (POST) and read-back (GET)
    posted = _call("POST", f"{base}/api/v1/transactions/{tid}/risk-assessment",
                   args.api_key, {"customer_reported_failure": False}, args.verbose)
    fetched = _call("GET", f"{base}/api/v1/transactions/{tid}/risk-assessment",
                    args.api_key, None, args.verbose)
    assessment = posted.get("assessment") or fetched.get("assessment")
    if not assessment:
        raise ApiError("risk-assessment response contained no 'assessment' object")
    record["assessment"] = assessment
    record["reused"] = bool(posted.get("reused", False))
    record["twin_recorded"] = bool(posted.get("digital_twin_event_recorded", False))

    # POST and GET must agree on the category
    if fetched.get("assessment", {}).get("anomaly_type") != assessment.get("anomaly_type"):
        raise ApiError("GET risk-assessment disagrees with POST "
                       f"(POST={assessment.get('anomaly_type')!r}, "
                       f"GET={fetched.get('assessment', {}).get('anomaly_type')!r})")

    # 4. Digital Twin: the timeline must contain an ANOMALY_CLASSIFIED event
    timeline = _call("GET", f"{base}/api/v1/transactions/{tid}/timeline",
                     args.api_key, None, args.verbose)
    twin_types = {e.get("event_type") for e in timeline.get("events", [])}
    if "ANOMALY_CLASSIFIED" not in twin_types:
        raise ApiError("timeline has no ANOMALY_CLASSIFIED event "
                       f"(events present: {sorted(t for t in twin_types if t)})")

    # 5. verdict against EXPECTED
    actual = assessment.get("anomaly_type")
    if actual != spec["expected_anomaly"]:
        raise ApiError(f"expected anomaly_type {spec['expected_anomaly']}, got {actual!r}")
    if spec["expected_candidate"] is not None \
            and bool(assessment.get("recovery_candidate")) != spec["expected_candidate"]:
        raise ApiError(f"expected recovery_candidate {spec['expected_candidate']}, "
                       f"got {assessment.get('recovery_candidate')!r}")
    record["ok"] = True
    return record


def print_results(records: list[dict]) -> None:
    header = (f"{'#':<4} {'scenario':<18} {'anomaly_type':<18} {'risk_level':<12} "
              f"{'risk_score':>10} {'ml_score':>9} {'cand':>5}  verdict")
    print(header)
    print("-" * len(header))
    for r in records:
        a = r.get("assessment") or {}
        if r["ok"]:
            note = ""
            if r["key"] == "S5":
                score = a.get("ml_anomaly_score")
                note = " (ml score high — evidence still wins)" \
                    if isinstance(score, (int, float)) and score >= 0.7 \
                    else (f" (ml score {score})" if score is not None else "")
            print(f"{r['key']:<4} {r['key_scenario']:<18} "
                  f"{a.get('anomaly_type', '?'):<18} "
                  f"{str(a.get('risk_level', '?')):<12} "
                  f"{str(a.get('risk_score', '?')):>10} "
                  f"{str(a.get('ml_anomaly_score'))[:8]:>9} "
                  f"{str(a.get('recovery_candidate'))[:5]:>5}  PASS{note}")
        else:
            print(f"{r['key']:<4} {'-':<18} {'-':<18} {'-':<12} {'-':>10} {'-':>9} {'-':>5}  FAIL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage 7 end-to-end risk & anomaly classification demo "
                    "(drives the live API; stdlib only).")
    parser.add_argument("--api-url", default=DEFAULT_API_URL,
                        help=f"API base URL (default {DEFAULT_API_URL})")
    parser.add_argument("--api-key", default=os.environ.get("PAYMENT_API_KEY", DEFAULT_API_KEY),
                        help="API key (default: $PAYMENT_API_KEY or the dev placeholder)")
    parser.add_argument("--seed", type=int, default=SEED,
                        help=f"random seed, used ONLY for latency jitter and tx ids (default {SEED})")
    parser.add_argument("--verbose", action="store_true",
                        help="log details (stack traces, raw responses)")
    args = parser.parse_args(argv)

    base = args.api_url.rstrip("/")
    start = datetime.now(timezone.utc)

    print(f"Stage 7 E2E — API: {base}  seed: {args.seed}")
    print()

    records: list[dict] = []
    for key, spec in SCENARIOS.items():
        tid = scenario_transaction_id(key, args.seed)
        print(f"[{key}] {spec['steps']} on {tid} ...", flush=True)
        record = {"key": key, "key_scenario": spec["steps"], "transaction_id": tid,
                  "ok": False, "error": None, "assessment": None}
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

    print()
    print_results(records)

    passed = sum(1 for r in records if r["ok"])
    total = len(records)
    print()
    print(f"Summary: {passed}/{total} scenarios passed"
          + ("" if passed == total else "  ->  see FAIL lines above"))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

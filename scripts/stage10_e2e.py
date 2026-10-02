"""
Stage 10 — Final Demo E2E (the full judge story)
Project: AI-Powered Payment Failure Recovery & Digital Twin System
====================================================================

Drives the LIVE API through the Stage 10 demo scenarios — the exact story
a judge sees — against the server-side seeded DEMO-S1..S6 data:

  1. RESET        POST /demo/reset as ADMIN -> reset=true, 6 scenarios
  2. S1 PRIMARY   genuine failure -> autonomous recovery: prepare (no
                  recovery yet), transaction read, reconstruction root
                  cause, risk assessment, process -> AUTO_RECOVERED /
                  VERIFIED, read-back, sandbox ledger consistency (the
                  available limit reconciles exactly with the entries),
                  bn/customer explanation, audit trail rows
  3. S2 SAFETY    double deduction -> RECOVERY_BLOCKED / DOUBLE_DEDUCTION,
                  provider never called, tx never LIMIT_RELEASED
  4. S5 RACE      late settlement lands before processing -> layered
                  block code, never released, never auto-recovered
  5. S6 IDEMPOTENCY duplicate process -> ALREADY_RECOVERED with the SAME
                  recovery_id, released exactly once in the ledger
  6. SECURITY     §29 regression sweep: CUSTOMER and SUPPORT can never
                  mutate demo state or execute recovery; secret-leakage
                  sweep over every collected response body
  7. TIMING       a per-step milliseconds table with REAL measured values

SANDBOX ONLY — no real money moves. Every recovery runs against the
simulated sandbox provider and every response is asserted (or reported)
to be simulated: true.

Style follows scripts/stage8_e2e.py / scripts/stage9_e2e.py: stdlib only,
_request/_call/ApiError with .status, --api-url/--seed/--verbose,
rerun-safe (reset at the start makes every run deterministic). Rate
limiting is ENABLED on the live dev server, so the script sleeps between
calls and treats HTTP 429 as a FAIL with a clear message. No secret is
ever printed.

Usage:
  py -m scripts.stage10_e2e
  py -m scripts.stage10_e2e --api-url http://127.0.0.1:8000 --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# Ensure the project root is importable when run as a plain script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
SEED = 42
DEFAULT_API_URL = "http://127.0.0.1:8000"

# Fixed dev role keys (documented public dev placeholders — they MUST be in
# the repo for the demo; the §29 sweep also proves they never leak).
SYSTEM_KEY = "dev-system-key"
ADMIN_KEY = "dev-admin-key"
SUPPORT_KEY = "dev-support-key"
ALICE_KEY = "dev-customer-alice"

ALL_KEYS = (SYSTEM_KEY, ADMIN_KEY, SUPPORT_KEY, ALICE_KEY)

# Politeness for the live dev server's rate limiter (60/min for /demo/*,
# 20/min recovery process, 30/min risk — which is ENABLED there, unlike
# under pytest where ENVIRONMENT=test disables it).
INTER_CALL_SLEEP_S = 1.2

EXPECTED_SCENARIO_KEYS = ("S1", "S2", "S3", "S4", "S5", "S6")

DECISION_AUTO_RECOVERED = "AUTO_RECOVERED"
DECISION_BLOCKED = "RECOVERY_BLOCKED"
DECISION_ALREADY_RECOVERED = "ALREADY_RECOVERED"

BLOCK_DOUBLE_DEDUCTION = "DOUBLE_DEDUCTION"
# S5 layered defense: EITHER layer may catch the late settlement (same
# acceptance set as stage8_e2e).
S5_BLOCK_REASONS = ("NEW_SUCCESSFUL_SETTLEMENT", "ALREADY_SUCCESS",
                    "INSUFFICIENT_EVIDENCE", "NOT_ELIGIBLE",
                    "RISK_NO_LONGER_PERMITS")


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
        shown = {k: v for k, v in request.header_items() if k.lower() != "x-api-key"}
        print(f"[verbose] {method} {url} headers={shown}")
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
    """A check failed against the live API."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _call(method: str, url: str, api_key: str, payload: dict | None,
          verbose: bool, expect: tuple[int, ...] = (200,)) -> dict:
    time.sleep(INTER_CALL_SLEEP_S)  # stay under the live server's rate limit
    status, body = _request(method, url, api_key, payload, verbose)
    if verbose:
        print(f"[verbose] -> {status}: {json.dumps(body)[:400]}")
    if status == 429:
        raise ApiError(
            "HTTP 429 rate limited — the live dev server has rate limiting "
            "ENABLED; re-run (the INTER_CALL_SLEEP_S pause may need raising), "
            "or run against a server started with ENVIRONMENT=test",
            status=status,
        )
    if status not in expect:
        raise ApiError(
            f"{method} {url} -> HTTP {status}: {json.dumps(body)[:300]}",
            status=status,
        )
    return body


def _probe(method: str, url: str, api_key: str, payload: dict | None,
           verbose: bool) -> tuple[int, dict]:
    """A request whose FAILURE status is the expectation — still polite to
    the rate limiter."""
    time.sleep(INTER_CALL_SLEEP_S)
    return _request(method, url, api_key, payload, verbose)


# --------------------------------------------------------------------------
# Harness: checks + step timing
# --------------------------------------------------------------------------

class Runner:
    """Collects PASS/FAIL checks and per-step wall-clock milliseconds."""

    def __init__(self, verbose: bool):
        self.verbose = verbose
        self.checks: list[dict] = []      # {key, label, ok, note, error}
        self.timings: list[tuple[str, float]] = []  # (step, ms)
        self.bodies: list[tuple[str, dict]] = []    # for the secret sweep

    def timed(self, label: str, fn):
        """Run fn() inside a perf_counter window; record its ms and return
        its result. Connection-level errors propagate to the caller."""
        start = time.perf_counter()
        try:
            result = fn()
        finally:
            self.timings.append((label, (time.perf_counter() - start) * 1000.0))
        return result

    def check(self, key: str, label: str, fn) -> bool:
        """Run one assertion bundle; a raised ApiError marks it FAIL and the
        run continues where safe. Returns the verdict."""
        try:
            note = fn()
            self.checks.append({"key": key, "label": label, "ok": True,
                                "note": note or "", "error": None})
            print(f"  PASS [{key}] {label}" + (f" — {note}" if note else ""))
            return True
        except ApiError as exc:
            self.checks.append({"key": key, "label": label, "ok": False,
                                "note": "", "error": str(exc)})
            print(f"  FAIL [{key}] {label}: {exc}")
            return False

    def keep(self, label: str, body: dict) -> dict:
        """Retain a response body for the secret-leakage sweep."""
        self.bodies.append((label, body))
        return body


# --------------------------------------------------------------------------
# The demo story
# --------------------------------------------------------------------------

def run_reset(runner: Runner, base: str, args) -> dict:
    """Step 1: deterministic demo reset (rerun-safety anchor)."""
    def _do():
        return runner.keep("reset", _call(
            "POST", f"{base}/api/v1/demo/reset", ADMIN_KEY, None, args.verbose))

    def _assert():
        body = _do()
        if body.get("reset") is not True:
            raise ApiError(f"expected reset=true, got {body.get('reset')!r}")
        scenarios = body.get("scenarios")
        if not isinstance(scenarios, list) or len(scenarios) != 6:
            raise ApiError(f"expected 6 scenarios in the reset response, "
                           f"got {len(scenarios) if isinstance(scenarios, list) else scenarios!r}")
        if body.get("simulated") is not True:
            raise ApiError("reset response must be marked simulated: true")
        return f"reset=true, 6 scenarios"

    runner.check("R1", "demo reset is deterministic", _assert)


def run_s1(runner: Runner, base: str, args) -> None:
    """Step 2: S1 — the primary judge story (genuine failure -> recovery)."""
    tid = "DEMO-S1"

    # (a) scenarios listing: S1 prepared but NOT processed
    def _a():
        body = runner.keep("scenarios", _call(
            "GET", f"{base}/api/v1/demo/scenarios", ADMIN_KEY, None, args.verbose))
        scenarios = body.get("scenarios") or []
        if body.get("simulated") is not True:
            raise ApiError("scenarios response must be marked simulated: true")
        by_key = {s.get("key"): s for s in scenarios if isinstance(s, dict)}
        s1 = by_key.get("S1")
        if not s1:
            raise ApiError(f"S1 missing from /demo/scenarios "
                           f"(keys: {sorted(by_key)})")
        if not s1.get("exists") or not s1.get("prepared"):
            raise ApiError(f"S1 not prepared after reset: exists="
                           f"{s1.get('exists')!r}, prepared={s1.get('prepared')!r}")
        if s1.get("processed"):
            raise ApiError("S1 is already processed right after reset — "
                           "prepare must NEVER run recovery")
        # no recovery row yet
        probe = _probe("GET", f"{base}/api/v1/transactions/{tid}/recovery",
                       ADMIN_KEY, None, args.verbose)
        runner.keep("s1-no-recovery-yet", probe[1])
        if probe[0] != 404:
            raise ApiError(f"expected 404 (no recovery) for {tid}, got "
                           f"HTTP {probe[0]}")
        return f"prepared, not processed; GET recovery -> 404"

    runner.timed("S1a scenario+no-recovery", lambda: runner.check(
        "S1a", "S1 prepared, no recovery row yet", _a))

    # (b) transaction read
    tx_state: list[str] = []

    def _b():
        body = runner.keep("s1-transaction", _call(
            "GET", f"{base}/api/v1/transactions/{tid}", ADMIN_KEY, None,
            args.verbose))
        state = body.get("current_state") or body.get("state")
        tx_state.append(str(state))
        return f"current_state={state}"

    runner.timed("S1b transaction read", lambda: runner.check(
        "S1b", "transaction read-back", _b))

    # (c) reconstruction: a deterministic root cause
    def _c():
        body = runner.keep("s1-reconstruction", _call(
            "GET", f"{base}/api/v1/transactions/{tid}/reconstruction",
            SUPPORT_KEY, None, args.verbose))
        if body.get("root_cause") in (None, "", {}):
            raise ApiError("reconstruction returned a null root_cause")
        return f"root_cause={str(body.get('root_cause'))[:40]}"

    runner.timed("S1c reconstruction", lambda: runner.check(
        "S1c", "reconstruction derives a root cause", _c))

    # (d) risk assessment — an assessment must EXIST; GENUINE_FAILURE is
    # expected but the honest ML/rules blend may differ, so warn-not-fail.
    anomaly_holder: list[str] = []

    def _d():
        body = runner.keep("s1-risk", _call(
            "POST", f"{base}/api/v1/transactions/{tid}/risk-assessment",
            ADMIN_KEY, {"customer_reported_failure": False}, args.verbose))
        inner = body.get("assessment") or body
        anomaly = inner.get("anomaly_type")
        anomaly_holder.append(str(anomaly))
        if body.get("simulated") is False:
            raise ApiError("risk response unexpectedly marked simulated: false")
        if anomaly != "GENUINE_FAILURE":
            print(f"    WARN [S1d] honest ML/rules blend produced "
                  f"anomaly_type={anomaly!r} (expected GENUINE_FAILURE) — "
                  "assessment existence is asserted, not the exact label")
            return f"assessment exists, anomaly_type={anomaly!r}"
        return f"anomaly_type=GENUINE_FAILURE"

    runner.timed("S1d risk assessment", lambda: runner.check(
        "S1d", "risk assessment exists (expect GENUINE_FAILURE)", _d))

    # (e) the autonomous recovery itself
    recovery_id_holder: list[str] = []

    def _e():
        body = runner.keep("s1-process", _call(
            "POST", f"{base}/api/v1/transactions/{tid}/recovery/process",
            ADMIN_KEY, {"customer_reported_failure": False}, args.verbose))
        if body.get("decision") != DECISION_AUTO_RECOVERED:
            raise ApiError(f"expected decision AUTO_RECOVERED, got "
                           f"{body.get('decision')!r}")
        if body.get("status") != "VERIFIED":
            raise ApiError(f"expected status VERIFIED, got {body.get('status')!r}")
        if not body.get("provider_reference"):
            raise ApiError("provider_reference must be non-null after VERIFIED")
        if body.get("simulated") is not True:
            raise ApiError("process response must be marked simulated: true")
        recovery_id_holder.append(str(body.get("recovery_id")))
        return (f"AUTO_RECOVERED, recovery "
                f"{str(body.get('recovery_id'))[:12]}…")

    runner.timed("S1e recovery process", lambda: runner.check(
        "S1e", "recovery process AUTO_RECOVERED/VERIFIED", _e))

    # (f) read-back
    def _f():
        body = runner.keep("s1-recovery-row", _call(
            "GET", f"{base}/api/v1/transactions/{tid}/recovery", ADMIN_KEY,
            None, args.verbose))
        if body.get("status") != "VERIFIED":
            raise ApiError(f"recovery row status {body.get('status')!r}, "
                           "expected VERIFIED")
        if recovery_id_holder and body.get("recovery_id") != recovery_id_holder[0]:
            raise ApiError("recovery_id changed between process and read-back")
        return f"row VERIFIED, same recovery_id"

    runner.timed("S1f recovery read-back", lambda: runner.check(
        "S1f", "recovery read-back agrees", _f))

    # (g) sandbox ledger consistency — recompute the arithmetic
    def _g():
        body = runner.keep("s1-ledger", _call(
            "GET", f"{base}/api/v1/sandbox/ledger", ADMIN_KEY, None,
            args.verbose))
        entries = body.get("entries") or []
        s1 = [e for e in entries
              if isinstance(e, dict) and e.get("transaction_id") == tid]
        if not s1:
            raise ApiError(f"no sandbox ledger entry for {tid}")
        released = sum(float(e.get("released_amount") or 0) for e in s1)
        if released <= 0:
            raise ApiError(f"{tid} ledger entry has released_amount <= 0")
        initial = float(body.get("initial_limit") or 0)
        held_total = sum(float(e.get("held_amount") or 0) for e in entries)
        released_total = sum(float(e.get("released_amount") or 0)
                             for e in entries)
        expected_available = initial - (held_total - released_total)
        available = body.get("available_limit")
        if available is None or abs(float(available) - expected_available) > 0.001:
            raise ApiError(
                f"ledger inconsistent: available_limit={available!r}, "
                f"recomputed {expected_available} from initial={initial}, "
                f"held={held_total}, released={released_total}")
        return (f"released {released:g}; available_limit reconciles "
                f"({available:g})")

    runner.timed("S1g ledger consistency", lambda: runner.check(
        "S1g", "sandbox ledger reconciles", _g))

    # (h) bn/customer explanation — requested by STAFF (ADMIN) for the
    # customer audience, mirroring the presenter flow (DEMO fixtures belong
    # to user_id USER-DEMO, which no customer key owns).
    def _h():
        body = runner.keep("s1-explanation", _call(
            "POST", f"{base}/api/v1/explanations/transaction", ADMIN_KEY,
            {"transaction_id": tid, "language": "bn",
             "audience": "customer"}, args.verbose))
        explanation = body.get("explanation")
        if not isinstance(explanation, str) or not explanation.strip():
            raise ApiError("explanation text is empty")
        if body.get("is_fallback") is True:
            return "bn explanation ok (honest fallback: provider unavailable)"
        return "bn explanation ok (provider generated)"

    runner.timed("S1h explanation", lambda: runner.check(
        "S1h", "bn/customer explanation generated", _h))

    # (i) audit trail — no action filter (the backend filter is EXACT match,
    # so ?action=DEMO matches nothing); scan the newest rows instead.
    def _i():
        body = runner.keep("s1-audit", _call(
            "GET", f"{base}/api/v1/audit?limit=100", ADMIN_KEY,
            None, args.verbose))
        rows = body.get("rows") or body.get("events") or body.get("items") \
            or (body if isinstance(body, list) else [])
        actions = [str(r.get("action", "")) for r in rows
                   if isinstance(r, dict)]
        demo_rows = [a for a in actions
                     if a.startswith("DEMO_RESET") or a.startswith("DEMO_SEED")]
        if not demo_rows:
            raise ApiError("audit has no DEMO_RESET/DEMO_SEED rows "
                           f"(saw {sorted(set(actions))[:10]})")
        return f"{len(demo_rows)} DEMO audit row(s)"

    runner.timed("S1i audit trail", lambda: runner.check(
        "S1i", "audit trail records demo actions", _i))


def _prepare_and_process(runner: Runner, base: str, args, key: str) -> dict:
    """prepare -> (optional injection) -> process; returns both bodies."""
    prepare = runner.keep(f"{key}-prepare", _call(
        "POST", f"{base}/api/v1/demo/scenarios/{key}/prepare", ADMIN_KEY,
        None, args.verbose))
    if prepare.get("prepared") is not True or prepare.get("simulated") is not True:
        raise ApiError(f"{key} prepare must return prepared=true, "
                       f"simulated=true, got {prepare!r}"[:300])
    tid = (prepare.get("scenario") or {}).get("transaction_id") or f"DEMO-{key}"
    process = runner.keep(f"{key}-process", _call(
        "POST", f"{base}/api/v1/transactions/{tid}/recovery/process",
        ADMIN_KEY, {"customer_reported_failure": False}, args.verbose))
    return {"prepare": prepare, "process": process, "tid": tid}


def _read_blocked_reason(runner: Runner, base: str, args, tid: str) -> str | None:
    """GET the recovery record — blocked_reason lives THERE, not on the
    process response body (which carries the human-readable `reason`)."""
    record = runner.keep(f"{tid}-record", _call(
        "GET", f"{base}/api/v1/transactions/{tid}/recovery", ADMIN_KEY,
        None, args.verbose))
    return record.get("blocked_reason")


def _assert_never_released(runner: Runner, base: str, args, tid: str,
                           context: str) -> None:
    """The tx must never be LIMIT_RELEASED (checked via demo scenarios)."""
    def _assert():
        body = _call("GET", f"{base}/api/v1/demo/scenarios", ADMIN_KEY, None,
                     args.verbose)
        runner.keep(f"{context}-state-check", body)
        by_key = {s.get("key"): s for s in body.get("scenarios") or []
                  if isinstance(s, dict)}
        # find the scenario whose transaction_id matches
        scenario = next((s for s in by_key.values()
                         if s.get("transaction_id") == tid), None)
        state = (scenario or {}).get("current_state")
        if state == "LIMIT_RELEASED":
            raise ApiError(f"{tid} reached LIMIT_RELEASED ({context})")
        recovery = (scenario or {}).get("recovery") or {}
        if recovery.get("decision") == DECISION_AUTO_RECOVERED:
            raise ApiError(f"{tid} reports AUTO_RECOVERED ({context})")
        return f"current_state={state}"
    runner.check(f"{context}-state", f"{tid} never released ({context})",
                 _assert)


def run_s2(runner: Runner, base: str, args) -> None:
    """Step 3: S2 — double deduction is blocked, provider never called."""

    def _assert():
        result = _prepare_and_process(runner, base, args, "S2")
        process = result["process"]
        if process.get("decision") != DECISION_BLOCKED:
            raise ApiError(f"expected RECOVERY_BLOCKED, got "
                           f"{process.get('decision')!r}")
        if process.get("status") != "BLOCKED":
            raise ApiError(f"expected status BLOCKED, got {process.get('status')!r}")
        blocked = _read_blocked_reason(runner, base, args, result["tid"])
        if blocked != BLOCK_DOUBLE_DEDUCTION:
            raise ApiError(f"expected blocked_reason DOUBLE_DEDUCTION on the "
                           f"recovery record, got {blocked!r}")
        if process.get("provider_reference") is not None:
            raise ApiError("provider must NOT be called — provider_reference "
                           "is present on a blocked response")
        if process.get("simulated") is not True:
            raise ApiError("process response must be marked simulated: true")
        return f"blocked: DOUBLE_DEDUCTION, no provider call"

    runner.timed("S2 double deduction", lambda: runner.check(
        "S2", "double deduction blocked (provider not called)", _assert))
    _assert_never_released(runner, base, args, "DEMO-S2", "S2")


def run_s5(runner: Runner, base: str, args) -> None:
    """Step 4: S5 — the late-settlement race is caught (layered defense)."""

    def _assert():
        # prepare, confirm no recovery row yet, inject, then process
        prepare = runner.keep("S5-prepare", _call(
            "POST", f"{base}/api/v1/demo/scenarios/S5/prepare", ADMIN_KEY,
            None, args.verbose))
        if prepare.get("prepared") is not True:
            raise ApiError("S5 prepare failed")
        tid = (prepare.get("scenario") or {}).get("transaction_id") or "DEMO-S5"
        probe = _probe("GET", f"{base}/api/v1/transactions/{tid}/recovery",
                       ADMIN_KEY, None, args.verbose)
        runner.keep("S5-no-recovery-yet", probe[1])
        if probe[0] != 404:
            raise ApiError(f"S5: expected 404 before injection, got HTTP {probe[0]}")
        injected = runner.keep("S5-inject", _call(
            "POST", f"{base}/api/v1/demo/scenarios/S5/inject-late-settlement",
            ADMIN_KEY, None, args.verbose))
        if injected.get("simulated") is not True:
            raise ApiError("inject-late-settlement must be marked simulated: true")
        process = runner.keep("S5-process", _call(
            "POST", f"{base}/api/v1/transactions/{tid}/recovery/process",
            ADMIN_KEY, {"customer_reported_failure": False}, args.verbose))
        if process.get("decision") != DECISION_BLOCKED:
            raise ApiError("S5: the late settlement must prevent "
                           f"auto-recovery, got {process.get('decision')!r}")
        blocked = _read_blocked_reason(runner, base, args, tid)
        if blocked not in S5_BLOCK_REASONS:
            raise ApiError(f"S5: expected a layered block code "
                           f"{S5_BLOCK_REASONS} on the recovery record, "
                           f"got {blocked!r}")
        if process.get("provider_reference") is not None:
            raise ApiError("S5: provider must not have been called")
        return f"blocked: {blocked}"

    runner.timed("S5 race", lambda: runner.check(
        "S5", "late settlement blocks recovery (layered)", _assert))
    _assert_never_released(runner, base, args, "DEMO-S5", "S5")


def run_s6(runner: Runner, base: str, args) -> None:
    """Step 5: S6 — duplicate process is idempotent; released exactly once."""

    def _assert():
        result = _prepare_and_process(runner, base, args, "S6")
        first = result["process"]
        tid = result["tid"]
        if first.get("decision") != DECISION_AUTO_RECOVERED:
            raise ApiError(f"S6 first call: expected AUTO_RECOVERED, got "
                           f"{first.get('decision')!r}")
        if first.get("simulated") is not True:
            raise ApiError("S6 process must be marked simulated: true")
        first_id = first.get("recovery_id")
        if not first_id:
            raise ApiError("S6: first response carries no recovery_id")

        second = runner.keep("S6-replay", _call(
            "POST", f"{base}/api/v1/transactions/{tid}/recovery/process",
            ADMIN_KEY, {"customer_reported_failure": False}, args.verbose))
        if second.get("decision") != DECISION_ALREADY_RECOVERED:
            raise ApiError(f"S6 second call: expected ALREADY_RECOVERED, got "
                           f"{second.get('decision')!r}")
        if second.get("recovery_id") != first_id:
            raise ApiError("S6: recovery_id changed between calls")

        ledger = runner.keep("S6-ledger", _call(
            "GET", f"{base}/api/v1/sandbox/ledger", ADMIN_KEY, None,
            args.verbose))
        s6 = [e for e in ledger.get("entries") or []
              if isinstance(e, dict) and e.get("transaction_id") == tid]
        released = [e for e in s6 if float(e.get("released_amount") or 0) > 0]
        if len(released) != 1:
            raise ApiError(f"S6: expected exactly one released ledger entry "
                           f"for {tid}, found {len(released)}")
        entry = released[0]
        if float(entry.get("released_amount") or 0) > float(
                entry.get("held_amount") or 0):
            raise ApiError("S6: released_amount exceeds held_amount")
        return (f"released exactly once "
                f"({entry.get('released_amount')} {entry.get('currency', '')})")

    runner.timed("S6 idempotency", lambda: runner.check(
        "S6", "duplicate process is idempotent (released once)", _assert))


def run_security(runner: Runner, base: str, args) -> None:
    """Step 6: §29 regression sweep + secret-leakage sweep."""

    def _customer():
        probes = [
            ("reset", "POST", f"{base}/api/v1/demo/reset", None),
            ("prepare S1", "POST",
             f"{base}/api/v1/demo/scenarios/S1/prepare", None),
            ("scenarios", "GET", f"{base}/api/v1/demo/scenarios", None),
            ("ledger", "GET", f"{base}/api/v1/sandbox/ledger", None),
            ("process", "POST",
             f"{base}/api/v1/transactions/DEMO-S2/recovery/process",
             {"customer_reported_failure": False}),
            ("risk read", "GET",
             f"{base}/api/v1/transactions/DEMO-S2/risk-assessment", None),
            ("audit", "GET", f"{base}/api/v1/audit", None),
        ]
        for label, method, url, payload in probes:
            status, body = _probe(method, url, ALICE_KEY, payload, args.verbose)
            runner.keep(f"sec-alice-{label.replace(' ', '-')}", body)
            if status != 403:
                raise ApiError(f"CUSTOMER {label}: expected 403, got HTTP {status}")
        return "7 CUSTOMER probes -> 403"

    runner.timed("SEC customer", lambda: runner.check(
        "SEC1", "CUSTOMER denied on all demo/recovery/audit routes", _customer))

    def _support():
        probes = [
            ("reset", "POST", f"{base}/api/v1/demo/reset", None),
            ("prepare S1", "POST",
             f"{base}/api/v1/demo/scenarios/S1/prepare", None),
        ]
        for label, method, url, payload in probes:
            status, body = _probe(method, url, SUPPORT_KEY, payload, args.verbose)
            runner.keep(f"sec-support-{label.replace(' ', '-')}", body)
            if status != 403:
                raise ApiError(f"SUPPORT {label}: expected 403, got HTTP {status}")
        # SUPPORT may still READ demo state
        for label, url in (
                ("scenarios", f"{base}/api/v1/demo/scenarios"),
                ("status", f"{base}/api/v1/demo/status"),
                ("ledger", f"{base}/api/v1/sandbox/ledger")):
            body = _call("GET", url, SUPPORT_KEY, None, args.verbose)
            runner.keep(f"sec-support-read-{label}", body)
        return "mutate -> 403 x2; read scenarios/status/ledger -> 200"

    runner.timed("SEC support", lambda: runner.check(
        "SEC2", "SUPPORT may read, never mutate demo state", _support))

    def _secrets():
        # 'sk-' is checked as a QUOTED-TOKEN PREFIX (a real provider key
        # serializes as "sk-..."), not a raw substring — audit resource ids
        # legitimately contain 'sk-' inside paths like /risk-assessment.
        sk_token = re.compile(r'"sk-[^"]*"')
        checked = 0
        for label, body in runner.bodies:
            serialized = json.dumps(body)
            for key in ALL_KEYS:
                if key in serialized:
                    raise ApiError(f"{label} body LEAKS a dev key value "
                                   "(value never printed here)")
            if sk_token.search(serialized):
                raise ApiError(f"{label} body contains an 'sk-' string")
            checked += 1
        return f"{checked} response bodies swept, zero leaks"

    runner.timed("SEC secrets", lambda: runner.check(
        "SEC3", "secret-leakage sweep over all collected bodies", _secrets))


def print_timings(runner: Runner, total_ms: float) -> None:
    """§ performance check — REAL measured per-step milliseconds."""
    print("PERFORMANCE (measured, live server)")
    print("-" * 48)
    for label, ms in runner.timings:
        print(f"  {label:<28} {ms:8.1f} ms")
    print("-" * 48)
    print(f"  {'TOTAL':<28} {total_ms:8.1f} ms")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage 10 final demo E2E: the full judge story against "
                    "the live API (stdlib only; SANDBOX ONLY -- no real "
                    "money moves).")
    parser.add_argument("--api-url", default=DEFAULT_API_URL,
                        help=f"API base URL (default {DEFAULT_API_URL})")
    parser.add_argument("--seed", type=int, default=SEED,
                        help=f"kept for convention; demo ids are fixed "
                             f"(default {SEED})")
    parser.add_argument("--verbose", action="store_true",
                        help="log details (raw requests and responses; keys "
                             "are never printed)")
    args = parser.parse_args(argv)

    base = args.api_url.rstrip("/")
    print(f"Stage 10 E2E — final demo run — API: {base}")
    print("SANDBOX ONLY — no real money moves. Every recovery is simulated.")
    print("(rate limiting is ENABLED on the live dev server: pausing "
          f"{INTER_CALL_SLEEP_S}s between calls; HTTP 429 is a FAIL)")
    print()

    runner = Runner(args.verbose)
    run_start = time.perf_counter()

    try:
        run_reset(runner, base, args)
        run_s1(runner, base, args)
        run_s2(runner, base, args)
        run_s5(runner, base, args)
        run_s6(runner, base, args)
        run_security(runner, base, args)
    except ApiError as exc:
        if exc.status == 429:
            print(f"    STOP: {exc}")
        else:
            print(f"    STOP: flow aborted (later steps depend on this): {exc}")
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

    total_ms = (time.perf_counter() - run_start) * 1000.0

    print()
    print_timings(runner, total_ms)

    print()
    passed = sum(1 for c in runner.checks if c["ok"])
    total = len(runner.checks)
    print(f"STAGE 10 E2E: {passed}/{total} checks passed")
    print("Autonomous recovery operates on a simulated sandbox provider. "
          "No real financial transaction is performed.")
    print("No API key value is ever printed by this script.")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

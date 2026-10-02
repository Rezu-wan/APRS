"""
Stage 9 — End-to-End VERIFICATION: Security Flow (§36) + Failure Flow (§37)
Project: AI-Powered Payment Failure Recovery & Digital Twin System
==========================================================================

Drives the LIVE dev API with the four fixed dev roles (SYSTEM, ADMIN,
SUPPORT, and two CUSTOMER identities) and asserts the Stage 9 hardening
contract end to end.

SECURITY FLOW (§36)
  T1  create a transaction for user_id "alice" as SYSTEM            -> 200
  T2  alice reads OWN transaction -> 200; bob reads alice's -> 403;
      an unbound customer key is refused (403/401) with a
      non-enumerating body (the message never contains the tx id)
  T3  alice posts an explanation for her tx -> 200;
      bob posts for alice's -> 403
  T4  CUSTOMER attempts recovery process -> 403
  T5  inject the merchant-timeout payment-event chain as SYSTEM     -> 200
  T6  reconstruction as SUPPORT -> 200; risk assessment as ADMIN
      -> 200 GENUINE_FAILURE
  T7  recovery process as ADMIN -> AUTO_RECOVERED, VERIFIED, simulated
  T8  replay process -> ALREADY_RECOVERED, SAME recovery_id
  T9  SUPPORT attempts process -> 403 (may evaluate, never execute)
  T10 GET /api/v1/audit as ADMIN -> 200 with AUTH_FAILURE and/or
      FORBIDDEN rows; as CUSTOMER -> 403
  T11 SECRET-LEAKAGE sweep: NO response body from T1-T10 contains any dev
      API key value or any "sk-" prefixed string

FAILURE FLOW (§37, honest)
  T12 the provider-failure recovery hook is SERVER-SIDE and cannot be
      injected through the live API; the safety of that path is asserted by
      running tests/test_recovery_executor.py + tests/test_recovery_verifier.py
      as a subprocess (reported as unit-covered, never faked)
  T13 every recovery row reachable from the transactions created here
      obeys the safe-state contract: never VERIFIED with a null
      provider_reference

Style follows scripts/stage7_e2e.py / scripts/stage8_e2e.py: stdlib only,
_call/ApiError with .status, rerun-safe id rotation on 400
INVALID_STATE_TRANSITION, --api-url/--seed/--verbose. Rate limiting is
ENABLED on the live dev server, so the script sleeps between calls and
treats HTTP 429 as a FAIL with a clear message. No secret is ever printed.

Usage:
  py -m scripts.stage9_e2e
  py -m scripts.stage9_e2e --api-url http://127.0.0.1:8000 --verbose
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# Ensure the project root is importable when run as a plain script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.core.payment_lifecycle import (  # noqa: E402  (path fix above)
    EVENT_TYPE_INFO,
)

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
SEED = 42
DEFAULT_API_URL = "http://127.0.0.1:8000"

# Fixed dev role keys (documented public dev placeholders — they MUST be in
# the repo for the demo; this script also proves they never leak into
# responses, which is T11's whole point).
SYSTEM_KEY = "dev-system-key"
ADMIN_KEY = "dev-admin-key"
SUPPORT_KEY = "dev-support-key"
ALICE_KEY = "dev-customer-alice"
BOB_KEY = "dev-customer-bob"
UNBOUND_KEY = "dev-customer-key"  # owns nothing
ALL_KEYS = (SYSTEM_KEY, ADMIN_KEY, SUPPORT_KEY, ALICE_KEY, BOB_KEY)

# Politeness for the live dev server's rate limiter (which is ENABLED there,
# unlike under pytest where ENVIRONMENT=test disables it).
INTER_CALL_SLEEP_S = 0.15

ALICE_USER_ID = "alice"

DECISION_AUTO_RECOVERED = "AUTO_RECOVERED"
DECISION_ALREADY_RECOVERED = "ALREADY_RECOVERED"

_MERCHANT_TIMEOUT_EVENTS = [
    "CUSTOMER_DEBIT_CONFIRMED",
    "GATEWAY_REQUEST_SENT",
    "GATEWAY_RESPONSE_RECEIVED",
    "MERCHANT_CONFIRMATION_REQUESTED",
    "MERCHANT_CONFIRMATION_TIMEOUT",
]

_TX_FIXTURE = {
    "user_id": ALICE_USER_ID,
    "merchant_id": "MERCHANT-E2E9",
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


def stage9_transaction_id(seed: int) -> str:
    digest = hashlib.sha256(f"{seed}:stage9:security".encode("utf-8")).hexdigest()
    return f"TXN-E2E9-SEC-{digest[:8]}"


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
    """A test step failed against the live API."""

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


def _expect_status(label: str, status: int, expected: int) -> None:
    if status != expected:
        raise ApiError(f"{label}: expected HTTP {expected}, got HTTP {status}")


def _probe(method: str, url: str, api_key: str, payload: dict | None,
           verbose: bool) -> tuple[int, dict]:
    """A request whose FAILURE status is the expectation — still polite to
    the rate limiter."""
    time.sleep(INTER_CALL_SLEEP_S)
    return _request(method, url, api_key, payload, verbose)


def _message(body: dict) -> str:
    detail = body.get("detail", body)
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    return str(detail)


# --------------------------------------------------------------------------
# The security flow (§36)
# --------------------------------------------------------------------------

def run_security_flow(args) -> list[dict]:
    """Run T1-T11 in order. Each entry is appended to `records` as soon as it
    finishes, so a failure mid-flow still reports the earlier PASSes."""
    base = args.api_url.rstrip("/")
    records: list[dict] = []
    bodies: list[tuple[str, dict]] = []  # (label, body) for the T11 sweep

    def record(tid: str, ok: bool, note: str, error: str | None = None) -> None:
        number = f"T{len(records) + 1}"
        records.append({
            "key": number, "transaction_id": tid or "-",
            "ok": ok, "note": note, "error": error,
        })

    def step(label: str, fn):
        """Run one test step, translate failures into a FAIL record, and
        re-raise only for connection-level errors."""
        try:
            note = fn()
            record(args.tid, True, note)
            print(f"    PASS {label}" + (f" ({note})" if note else ""))
        except ApiError as exc:
            record(args.tid, False, "", str(exc))
            print(f"    FAIL {label}: {exc}")
            raise

    # ---- T1: create alice's transaction (rerun-safe id rotation) --------
    def _t1() -> str:
        tid = stage9_transaction_id(args.seed)
        for attempt in range(40):
            try:
                body = _call("POST", f"{base}/api/v1/transaction/event",
                             SYSTEM_KEY, {"transaction_id": tid, **_TX_FIXTURE},
                             args.verbose)
                bodies.append(("T1-create", body))
                return tid
            except ApiError as exc:
                if exc.status == 400 and attempt < 39:
                    tid = f"{stage9_transaction_id(args.seed)}-r{attempt + 1}"
                    print(f"    [rotate] transaction id already advanced; "
                          f"retrying as {tid}")
                    continue
                raise
        raise ApiError("could not create a fresh transaction")

    args.tid = None
    tid_holder: list[str] = []

    def _t1_step():
        tid_holder.append(_t1())
        args.tid = tid_holder[0]
        return f"transaction {args.tid}"

    step("T1 create transaction for alice as SYSTEM", _t1_step)
    tid = args.tid

    # ---- T2: ownership on transaction reads ------------------------------
    def _t2() -> str:
        own = _call("GET", f"{base}/api/v1/transactions/{tid}", ALICE_KEY,
                    None, args.verbose)
        bodies.append(("T2-alice-own", own))
        if own.get("user_id") != ALICE_USER_ID:
            raise ApiError(f"T2: alice's read returned user_id "
                           f"{own.get('user_id')!r}, expected 'alice'")
        bob_denied = _probe("GET", f"{base}/api/v1/transactions/{tid}",
                            BOB_KEY, None, args.verbose)
        bodies.append(("T2-bob-denied", bob_denied[1]))
        _expect_status("T2 bob reads alice's tx", bob_denied[0], 403)
        unbound = _probe("GET", f"{base}/api/v1/transactions/{tid}",
                         UNBOUND_KEY, None, args.verbose)
        bodies.append(("T2-unbound-denied", unbound[1]))
        # an unbound customer key must have NO access; the exact refusal code
        # depends on whether the server registers the key at all
        if unbound[0] not in (401, 403):
            raise ApiError(f"T2: unbound customer key got HTTP {unbound[0]}, "
                           "expected 401/403")
        # non-enumerating: the 403 body must not confirm the tx id exists
        if tid in _message(unbound[1]):
            raise ApiError("T2: denial body contains the transaction id "
                           "(enumeration leak)")
        return "own=200, other-customer=403, unbound=401/403, non-enumerating"

    step("T2 alice reads own / bob denied / unbound denied", _t2)

    # ---- T3: explanations ownership --------------------------------------
    def _t3() -> str:
        payload = {"transaction_id": tid, "language": "en",
                   "audience": "support"}
        own = _call("POST", f"{base}/api/v1/explanations/transaction",
                    ALICE_KEY, payload, args.verbose)
        bodies.append(("T3-alice-explanation", own))
        bob_denied = _probe("POST", f"{base}/api/v1/explanations/transaction",
                            BOB_KEY, payload, args.verbose)
        bodies.append(("T3-bob-denied", bob_denied[1]))
        _expect_status("T3 bob explains alice's tx", bob_denied[0], 403)
        return "owner explanation=200, other customer=403"

    step("T3 alice explains own tx / bob denied", _t3)

    # ---- T4: CUSTOMER cannot execute recovery ----------------------------
    def _t4() -> str:
        resp = _probe("POST", f"{base}/api/v1/transactions/{tid}/recovery/process",
                      ALICE_KEY, {"customer_reported_failure": False},
                      args.verbose)
        bodies.append(("T4-alice-process-denied", resp[1]))
        _expect_status("T4 alice recovery process", resp[0], 403)
        return "CUSTOMER process -> 403"

    step("T4 alice attempts recovery process", _t4)

    # ---- T5: inject the merchant-timeout evidence chain -------------------
    def _t5() -> str:
        events = build_events(tid, datetime.now(timezone.utc), args.seed)
        body = _call("POST", f"{base}/api/v1/transactions/{tid}/payment-events",
                     SYSTEM_KEY, {"events": events}, args.verbose)
        bodies.append(("T5-events", body))
        created = body.get("created")
        if created != len(events):
            raise ApiError(f"T5: expected {len(events)} events created, "
                           f"got {created!r}")
        return f"{created} events ingested"

    step("T5 inject merchant-timeout events as SYSTEM", _t5)

    # ---- T6: reconstruction (SUPPORT) + risk assessment (ADMIN) ----------
    def _t6() -> str:
        recon = _call("GET", f"{base}/api/v1/transactions/{tid}/reconstruction",
                      SUPPORT_KEY, None, args.verbose)
        bodies.append(("T6-reconstruction", recon))
        assessment = _call("POST",
                           f"{base}/api/v1/transactions/{tid}/risk-assessment",
                           ADMIN_KEY, {"customer_reported_failure": False},
                           args.verbose)
        bodies.append(("T6-assessment", assessment))
        inner = assessment.get("assessment") or assessment
        anomaly = inner.get("anomaly_type")
        if anomaly != "GENUINE_FAILURE":
            raise ApiError(f"T6: expected GENUINE_FAILURE, got {anomaly!r}")
        return f"reconstruction=200, anomaly=GENUINE_FAILURE"

    step("T6 reconstruction as SUPPORT / assessment as ADMIN", _t6)

    # ---- T7: autonomous recovery as ADMIN --------------------------------
    def _t7() -> str:
        process = _call("POST", f"{base}/api/v1/transactions/{tid}/recovery/process",
                        ADMIN_KEY, {"customer_reported_failure": False},
                        args.verbose)
        bodies.append(("T7-process", process))
        if process.get("decision") != DECISION_AUTO_RECOVERED:
            raise ApiError(f"T7: expected AUTO_RECOVERED, got "
                           f"{process.get('decision')!r}")
        if process.get("status") != "VERIFIED":
            raise ApiError(f"T7: expected VERIFIED, got "
                           f"{process.get('status')!r}")
        if process.get("simulated") is not True:
            raise ApiError("T7: response must be marked simulated: true")
        return f"AUTO_RECOVERED, recovery {str(process.get('recovery_id'))[:12]}..."

    step("T7 recovery process as ADMIN", _t7)

    # ---- T8: idempotent replay --------------------------------------------
    def _t8() -> str:
        first = _call("GET", f"{base}/api/v1/transactions/{tid}/recovery",
                      ADMIN_KEY, None, args.verbose)
        bodies.append(("T8-recovery-read", first))
        replay = _call("POST",
                       f"{base}/api/v1/transactions/{tid}/recovery/process",
                       ADMIN_KEY, {"customer_reported_failure": False},
                       args.verbose)
        bodies.append(("T8-replay", replay))
        if replay.get("decision") != DECISION_ALREADY_RECOVERED:
            raise ApiError(f"T8: expected ALREADY_RECOVERED, got "
                           f"{replay.get('decision')!r}")
        if replay.get("recovery_id") != first.get("recovery_id"):
            raise ApiError("T8: recovery_id changed between calls")
        return f"same recovery_id {str(first.get('recovery_id'))[:12]}..."

    step("T8 replay process is idempotent", _t8)

    # ---- T9: SUPPORT may evaluate, never execute --------------------------
    def _t9() -> str:
        resp = _probe("POST",
                      f"{base}/api/v1/transactions/{tid}/recovery/process",
                      SUPPORT_KEY, {"customer_reported_failure": False},
                      args.verbose)
        bodies.append(("T9-support-denied", resp[1]))
        _expect_status("T9 support process", resp[0], 403)
        return "SUPPORT process -> 403"

    step("T9 support attempts recovery process", _t9)

    # ---- T10: audit trail --------------------------------------------------
    def _t10() -> str:
        audit = _call("GET", f"{base}/api/v1/audit", ADMIN_KEY, None,
                      args.verbose)
        bodies.append(("T10-audit-admin", audit))
        rows = audit.get("events") or audit.get("items") or audit.get("rows") \
            or (audit if isinstance(audit, list) else [])
        actions = [str(r.get("action", "")) for r in rows
                   if isinstance(r, dict)]
        hardening = [a for a in actions
                     if "AUTH_FAILURE" in a or "FORBIDDEN" in a]
        if not hardening:
            raise ApiError("T10: audit has no AUTH_FAILURE/FORBIDDEN rows "
                           f"(saw {sorted(set(actions))[:10]})")
        denied = _probe("GET", f"{base}/api/v1/audit", ALICE_KEY, None,
                        args.verbose)
        bodies.append(("T10-audit-customer-denied", denied[1]))
        _expect_status("T10 alice reads audit", denied[0], 403)
        return (f"{len(rows)} rows, {len(hardening)} hardening row(s); "
                "CUSTOMER audit -> 403")

    step("T10 audit as ADMIN / denied for CUSTOMER", _t10)

    # ---- T11: secret-leakage sweep -----------------------------------------
    # 'sk-' is checked as a QUOTED-TOKEN PREFIX (a real provider key
    # serializes as "sk-..."), not a raw substring: audit resource ids
    # legitimately contain 'sk-' inside paths like /risk-assessment
    # (Stage 10 demo traffic writes FORBIDDEN rows with such resource ids).
    def _t11() -> str:
        sk_token = re.compile(r'"sk-[^"]*"')
        checked = 0
        for label, body in bodies:
            serialized = json.dumps(body)
            for key in ALL_KEYS:
                if key in serialized:
                    raise ApiError(f"T11: {label} body LEAKS the key value "
                                   f"(never printed here)")
            if sk_token.search(serialized):
                raise ApiError(f"T11: {label} body contains an 'sk-' string")
            checked += 1
        return f"{checked} response bodies swept, zero leaks"

    step("T11 secret-leakage sweep over all bodies", _t11)

    args.created_tid = tid
    return records


def build_events(transaction_id: str, start: datetime, seed: int) -> list[dict]:
    """The merchant-timeout evidence chain, Stage 6 simulator contract."""
    import random

    rng = random.Random(seed)
    events: list[dict] = []
    elapsed_ms = 0.0
    for seq, event_type in enumerate(_MERCHANT_TIMEOUT_EVENTS):
        info = EVENT_TYPE_INFO[event_type]
        base_ms = 90 if info["outcome"] == "TIMEOUT" else 120 + seq * 40
        latency_ms = int(round(base_ms * rng.uniform(0.8, 1.2)))
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
# The failure flow (§37)
# --------------------------------------------------------------------------

UNIT_TEST_FILES = [
    "tests/test_recovery_executor.py",
    "tests/test_recovery_verifier.py",
]


def run_failure_flow(args) -> list[dict]:
    base = args.api_url.rstrip("/")
    records: list[dict] = []
    tid = getattr(args, "created_tid", None)

    # ---- T12: provider-failure path is UNIT-covered (honest) --------------
    def _t12() -> str:
        print("    [T12] provider-failure recovery hook is server-side and "
              "cannot be injected via the live API — running the unit suite")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *UNIT_TEST_FILES, "-q", "--no-header"],
            capture_output=True, text=True, timeout=600,
        )
        tail = (proc.stdout or "").strip().splitlines()[-1:] or ["<no output>"]
        if proc.returncode != 0:
            raise ApiError(f"T12: unit suite not green ({tail[0]})")
        return f"unit-covered: pytest green ({tail[0]})"

    try:
        note = _t12()
        records.append({"key": "T12", "transaction_id": "-", "ok": True,
                        "note": note, "error": None})
        print(f"    PASS T12 provider-failure path unit-covered")
    except ApiError as exc:
        records.append({"key": "T12", "transaction_id": "-", "ok": False,
                        "note": "", "error": str(exc)})
        print(f"    FAIL T12: {exc}")
    except (OSError, subprocess.SubprocessError) as exc:
        records.append({"key": "T12", "transaction_id": "-", "ok": False,
                        "note": "", "error": f"pytest failed to run: {exc}"})
        print(f"    FAIL T12: pytest failed to run: {exc}")

    # ---- T13: safe-state contract over every recovery row we created -----
    def _t13() -> str:
        if not tid:
            raise ApiError("T13: no transaction was created (T1 failed)")
        recovery = _call("GET", f"{base}/api/v1/transactions/{tid}/recovery",
                         ADMIN_KEY, None, args.verbose)
        status = recovery.get("status")
        reference = recovery.get("provider_reference")
        if status == "VERIFIED" and not reference:
            raise ApiError(
                f"T13: safe-state contract violated — {tid} is VERIFIED "
                "with a null provider_reference")
        return (f"recovery {str(recovery.get('recovery_id'))[:12]}... "
                f"status={status}, "
                f"provider_reference={'present' if reference else 'none'}")

    try:
        note = _t13()
        records.append({"key": "T13", "transaction_id": tid or "-",
                        "ok": True, "note": note, "error": None})
        print(f"    PASS T13 safe-state contract ({note})")
    except ApiError as exc:
        records.append({"key": "T13", "transaction_id": tid or "-",
                        "ok": False, "note": "", "error": str(exc)})
        print(f"    FAIL T13: {exc}")

    return records


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def print_results(records: list[dict]) -> None:
    header = f"{'#':<5} {'transaction':<28} verdict  note"
    print(header)
    print("-" * len(header))
    for r in records:
        if r["ok"]:
            print(f"{r['key']:<5} {str(r['transaction_id'])[:27]:<28} PASS"
                  f"  {r['note']}")
        else:
            print(f"{r['key']:<5} {str(r['transaction_id'])[:27]:<28} FAIL"
                  f"  {(r['error'] or '')[:120]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage 9 E2E verification: security flow (§36) + failure "
                    "flow (§37) against the live API (stdlib only).")
    parser.add_argument("--api-url", default=DEFAULT_API_URL,
                        help=f"API base URL (default {DEFAULT_API_URL})")
    parser.add_argument("--seed", type=int, default=SEED,
                        help=f"seed for deterministic transaction ids "
                             f"(default {SEED})")
    parser.add_argument("--verbose", action="store_true",
                        help="log details (raw requests and responses; keys "
                             "are never printed)")
    args = parser.parse_args(argv)

    base = args.api_url.rstrip("/")
    print(f"Stage 9 E2E verification — API: {base}  seed: {args.seed}")
    print("(rate limiting is ENABLED on the live dev server: pausing "
          f"{INTER_CALL_SLEEP_S}s between calls; HTTP 429 is a FAIL)")
    print()
    print("SECURITY FLOW (§36)")

    records: list[dict] = []
    try:
        records.extend(run_security_flow(args))
    except ApiError as exc:
        if exc.status == 429:
            print(f"    STOP: {exc}")
        else:
            print(f"    STOP: security flow aborted: {exc}")
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

    print()
    print("FAILURE FLOW (§37)")
    records.extend(run_failure_flow(args))

    print()
    print_results(records)
    passed = sum(1 for r in records if r["ok"])
    total = len(records)
    print()
    print(f"Summary: {passed}/{total} tests passed"
          + ("" if passed == total else "  ->  see FAIL lines above"))
    print("No API key value is ever printed by this script.")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

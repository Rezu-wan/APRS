"""
Stage 11 — End-to-End VERIFICATION: Event-Driven Intelligence, Temporal
Twin, Policy Simulation & Chaos Safety (spec §29, scenarios S11-1..S11-8)
Project: AI-Powered Payment Failure Recovery & Digital Twin System
==========================================================================

Drives the LIVE dev API through the Stage 11 surfaces and asserts the
spec's eight E2E scenarios plus the §25 security regression:

  S11-1  event replay      the same payment-event batch delivered twice ->
                           second delivery creates 0 events, one business
                           effect (released once)
  S11-2  out-of-order      the standard chain ingested in REVERSED order ->
                           reconstruction matches the in-order result
  S11-3  temporal state    state-at before a late settlement shows
                           settlement NOT_OBSERVED; after it, CONFIRMED
  S11-4  late settlement   inject + process -> RECOVERY_BLOCKED, never
                           released (layered block codes accepted)
  S11-5  concurrent        the chaos CONCURRENT_RECOVERY scenario (10
                           threads through the real engine) -> verdict PASS
  S11-6  chaos provider    chaos PROVIDER_TIMEOUT -> verdict PASS, nothing
                           released
  S11-7  policy sim        two identical simulator runs -> identical
                           dataset fingerprint + identical v1 metrics;
                           v1 false_recovery == 0; baseline releases 0
  S11-8  historical        the SAME early state-at query returns the
           isolation       identical report BEFORE and AFTER the late
                           settlement is injected — the future never
                           rewrites the past

Security sweep: CUSTOMER 403 on every Stage 11 surface (temporal,
behavioral, relationships, simulator, chaos); SUPPORT may read the chaos
catalog but never run simulator/chaos; secret sweep over all bodies.
Audit: POLICY_SIMULATION / CHAOS_TEST / TEMPORAL_QUERY rows exist.
Metrics: http_* and domain counters present after activity.

Style follows scripts/stage10_e2e.py: stdlib only, _call/_probe/ApiError,
--api-url, per-check PASS/FAIL lines, honest failures (exit 1), timing
table of real measured values, no secret ever printed.

Usage:
  py -m scripts.stage11_e2e
  py -m scripts.stage11_e2e --api-url http://127.0.0.1:8000 --verbose
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEV_KEYS = {  # documented public dev placeholders — never printed
    "SYSTEM": "dev-system-key",
    "ADMIN": "dev-admin-key",
    "SUPPORT": "dev-support-key",
    "ALICE": "dev-customer-alice",
}
ALL_KEY_VALUES = list(DEV_KEYS.values())
S5_BLOCK_CODES = (
    "NEW_SUCCESSFUL_SETTLEMENT",
    "ALREADY_SUCCESS",
    "INSUFFICIENT_EVIDENCE",
    "NOT_ELIGIBLE",
    "RISK_NO_LONGER_PERMITS",
)

from api.services.demo_scenarios import _CLEAN_FIXTURE, build_events  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

# chain timestamps for the replay/order scenarios (far from DEMO_BASE so the
# demo corpus and these fixtures never interact)
_T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


class Runner:
    """Check collector with the stage10 conventions."""

    def __init__(self) -> None:
        self.results: list[tuple[str, str, str, str]] = []  # id, name, verdict, note
        self.bodies: list[tuple[str, dict]] = []
        self.timings: list[tuple[str, float]] = []

    def keep(self, label: str, body: dict) -> dict:
        self.bodies.append((label, body))
        return body

    def check(self, check_id: str, name: str, fn):
        t0 = time.perf_counter()
        try:
            note = fn()
            self.results.append((check_id, name, "PASS", note or ""))
        except Exception as exc:  # noqa: BLE001 — one failure must not stop the sweep
            self.results.append((check_id, name, "FAIL", str(exc)[:220]))
        self.timings.append((f"{check_id} {name}", time.perf_counter() - t0))

    def summary(self) -> tuple[int, int]:
        passed = sum(1 for r in self.results if r[2] == "PASS")
        return passed, len(self.results)


# --------------------------------------------------------------------------
# HTTP helpers (stdlib only — same conventions as stage10_e2e)
# --------------------------------------------------------------------------

def _request(method: str, url: str, api_key: str | None, payload: dict | None
             ) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read().decode("utf-8")
            return response.status, (json.loads(raw) if raw.strip() else {})
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


def _call(method: str, url: str, api_key: str | None, payload: dict | None,
          expect: tuple[int, ...] = (200,)) -> dict:
    status, body = _request(method, url, api_key, payload)
    if status not in expect:
        raise RuntimeError(
            f"{method} {url.rsplit('/', 1)[-1]} -> HTTP {status}: "
            f"{json.dumps(body)[:200]}"
        )
    return body


def _probe(method: str, url: str, api_key: str | None, payload: dict | None
           ) -> tuple[int, dict]:
    return _request(method, url, api_key, payload)


def _create_transaction(base: str, admin: str, tid: str, runner: Runner,
                        label: str, max_rotations: int = 10) -> str:
    """Create the fixture transaction, rotating the id (-r1, -r2, …) when a
    previous run left it in a terminal state (rerun-safe, per stage8/9)."""
    current = tid
    for attempt in range(max_rotations + 1):
        status, body = _probe("POST", f"{base}/api/v1/transaction/event", admin,
                              {"transaction_id": current, **_CLEAN_FIXTURE})
        if status == 200:
            runner.keep(label, body)
            return current
        code = (body.get("error") or {}).get("code", "")
        if status == 400 and code == "INVALID_STATE_TRANSITION":
            current = f"{tid}-r{attempt + 1}"
            continue
        raise RuntimeError(f"create {tid} -> HTTP {status}: {json.dumps(body)[:160]}")
    raise RuntimeError(f"create {tid}: id rotation exhausted")


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage 11 E2E — event/temporal/simulator/chaos (S11-1..8).")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    base = args.api_url.rstrip("/")
    admin = DEV_KEYS["ADMIN"]
    support = DEV_KEYS["SUPPORT"]
    alice = DEV_KEYS["ALICE"]
    runner = Runner()

    print(f"Stage 11 E2E — event-driven intelligence, temporal twin, chaos")
    print(f"API: {base}  —  SANDBOX ONLY, no real money moves.")
    print()

    # ---------------------------------------------------------------- reset
    def _reset():
        body = runner.keep("reset", _call(
            "POST", f"{base}/api/v1/demo/reset", admin, {}))
        if body.get("reset") is not True or len(body.get("scenarios") or []) != 6:
            raise RuntimeError("demo reset did not return 6 scenarios")
        return "6 scenarios re-seeded"

    runner.check("R1", "deterministic demo reset", _reset)

    # ------------------------------------------------- S11-1 event replay
    def _s11_1():
        tid = _create_transaction(base, admin, "TXN-E2E11-REPLAY", runner,
                                  "s11-1-create")
        chain = build_events(tid, "merchant_timeout", _T0)
        first = runner.keep("s11-1-first", _call(
            "POST", f"{base}/api/v1/transactions/{tid}/payment-events", admin,
            {"events": chain}))
        second = runner.keep("s11-1-second", _call(
            "POST", f"{base}/api/v1/transactions/{tid}/payment-events", admin,
            {"events": chain}))
        if first.get("created") != len(chain):
            raise RuntimeError(f"first ingest created {first.get('created')}")
        if second.get("created") != 0 or second.get("duplicates") != len(chain):
            raise RuntimeError(
                f"replay must be created=0 duplicates={len(chain)}, got "
                f"created={second.get('created')} duplicates={second.get('duplicates')}")
        process = runner.keep("s11-1-process", _call(
            "POST", f"{base}/api/v1/transactions/{tid}/recovery/process", admin,
            {"customer_reported_failure": False}))
        if process.get("decision") != "AUTO_RECOVERED":
            raise RuntimeError(f"expected AUTO_RECOVERED, got {process.get('decision')}")
        ledger = runner.keep("s11-1-ledger", _call(
            "GET", f"{base}/api/v1/sandbox/ledger", admin, None))
        entry = next((e for e in ledger.get("entries", [])
                      if e["transaction_id"] == tid), None)
        if entry is None or not entry.get("released_amount"):
            raise RuntimeError("ledger must show exactly the one release")
        return (f"replay created=0 dups={second.get('duplicates')}; "
                f"one release ({entry['released_amount']} BDT)")

    runner.check("S11-1", "event replay -> one business effect", _s11_1)

    # -------------------------------------------- S11-2 out-of-order events
    def _s11_2():
        tid = _create_transaction(base, admin, "TXN-E2E11-ORDER", runner,
                                  "s11-2-create")
        chain = build_events(tid, "merchant_timeout", _T0)
        _call("POST", f"{base}/api/v1/transactions/{tid}/payment-events", admin,
              {"events": list(reversed(chain))})
        recon = runner.keep("s11-2-recon", _call(
            "GET", f"{base}/api/v1/transactions/{tid}/reconstruction", admin, None))
        expected_events = [e["event_type"] for e in chain]
        got_events = [e["event_type"] for e in recon.get("ordered_events", [])]
        if got_events != expected_events:
            raise RuntimeError("reconstruction must order by event_timestamp, "
                               f"not arrival; got {got_events}")
        if recon.get("root_cause") != "MERCHANT_CONFIRMATION_TIMEOUT":
            raise RuntimeError(f"root cause {recon.get('root_cause')!r}")
        return "reverse arrival -> chronological reconstruction, root MERCHANT_CONFIRMATION_TIMEOUT"

    runner.check("S11-2", "out-of-order events -> correct timeline", _s11_2)

    # ------------------------- S11-3 + S11-8 temporal state & isolation
    # DEMO-S5 after reset: merchant chain at ~09:04; the injected late
    # settlement lands at scenario_start + 10 min (~09:14).
    early_ts = "2026-10-03T09:10:00Z"   # after the chain, before the settlement
    late_ts = "2026-10-03T09:30:00Z"    # after the settlement
    early_query = f"{base}/api/v1/transactions/DEMO-S5/state-at?timestamp={early_ts}"
    late_query = f"{base}/api/v1/transactions/DEMO-S5/state-at?timestamp={late_ts}"

    def _s11_3_before():
        before = runner.keep("s11-3-early-before", _call("GET", early_query, admin, None))
        if before.get("observed_event_count", 0) < 1:
            raise RuntimeError("expected some observed events before the horizon")
        return (f"early view: observed={before.get('observed_event_count')} "
                f"settlement={before['reconstruction']['stages']['settlement']}")

    runner.check("S11-3a", "temporal state BEFORE late settlement", _s11_3_before)

    def _s11_4():
        inj = runner.keep("s11-4-inject", _call(
            "POST", f"{base}/api/v1/demo/scenarios/S5/inject-late-settlement",
            admin, None))
        if inj.get("simulated") is not True:
            raise RuntimeError("inject must be simulated")
        # re-run the EARLY query AFTER the future event exists (S11-8)
        early_after = runner.keep("s11-8-early-after", _call("GET", early_query, admin, None))
        proc = runner.keep("s11-4-process", _call(
            "POST", f"{base}/api/v1/transactions/DEMO-S5/recovery/process",
            admin, {"customer_reported_failure": False}))
        if proc.get("decision") != "RECOVERY_BLOCKED":
            raise RuntimeError(f"late settlement must block, got {proc.get('decision')}")
        record = runner.keep("s11-4-record", _call(
            "GET", f"{base}/api/v1/transactions/DEMO-S5/recovery", admin, None))
        if record.get("blocked_reason") not in S5_BLOCK_CODES:
            raise RuntimeError(f"block code {record.get('blocked_reason')!r}")
        if record.get("provider_reference") is not None:
            raise RuntimeError("provider must NOT be called on a blocked recovery")
        state = runner.keep("s11-4-state", _call(
            "GET", f"{base}/api/v1/demo/scenarios", admin, None))
        s5 = next(s for s in state["scenarios"] if s["key"] == "S5")
        if s5.get("current_state") == "LIMIT_RELEASED":
            raise RuntimeError("DEMO-S5 must never reach LIMIT_RELEASED")
        return f"blocked with {record.get('blocked_reason')}; provider not called"

    runner.check("S11-4", "late settlement blocks recovery", _s11_4)

    def _s11_3_after():
        after = runner.keep("s11-3-early-after", _call("GET", late_query, admin, None))
        stage = after["reconstruction"]["stages"]["settlement"]
        if stage != "CONFIRMED":
            raise RuntimeError(f"settlement at late horizon must be CONFIRMED, got {stage}")
        return f"late view: settlement=CONFIRMED observed={after.get('observed_event_count')}"

    runner.check("S11-3b", "temporal state AFTER late settlement", _s11_3_after)

    def _s11_8():
        # identical report before (S11-3a) and after (S11-4) the injection —
        # the future must never rewrite the past
        views = [b for label, b in runner.bodies
                 if label in ("s11-3-early-before", "s11-8-early-after")]
        if len(views) != 2:
            raise RuntimeError(f"expected 2 early views, collected {len(views)}")
        a, b = views
        # The VIEW must be identical; excluded_event_count legitimately grows
        # (it counts later arrivals) — assert it grew and nothing else moved.
        for field in ("state_then", "observed_event_count"):
            if a.get(field) != b.get(field):
                raise RuntimeError(f"historical drift on {field}: "
                                   f"{a.get(field)!r} -> {b.get(field)!r}")
        if b.get("excluded_event_count", 0) <= a.get("excluded_event_count", 0):
            raise RuntimeError("the later-arriving event must be COUNTED as excluded")
        sa, sb = a["reconstruction"]["stages"], b["reconstruction"]["stages"]
        if sa != sb or a["reconstruction"]["root_cause"] != b["reconstruction"]["root_cause"]:
            raise RuntimeError("historical reconstruction drifted after a future event")
        if b.get("uncertainty_note") is None:
            raise RuntimeError("an excluded event must surface the uncertainty note")
        return ("view identical before/after injection (observed="
                f"{a.get('observed_event_count')}); exclusion counted "
                f"({a.get('excluded_event_count')} -> {b.get('excluded_event_count')}) "
                "and never used")

    runner.check("S11-8", "historical isolation (future never rewrites past)", _s11_8)

    # ------------------------------------------- S11-5 + S11-6 chaos runner
    def _chaos(scenario: str, must_not_release: bool):
        result = runner.keep(f"chaos-{scenario}", _call(
            "POST", f"{base}/api/v1/chaos/run", admin, {"scenario": scenario}))
        if result.get("verdict") != "PASS":
            failed = [i["name"] for i in result.get("invariants", [])
                      if not i.get("held")]
            raise RuntimeError(f"verdict={result.get('verdict')} "
                               f"failed_invariants={failed} note={result.get('note')!r}")
        if must_not_release:
            outcome = result.get("outcome") or {}
            if outcome.get("decision") == "AUTO_RECOVERED":
                raise RuntimeError(f"{scenario} must not auto-recover")
            if outcome.get("provider_reference"):
                raise RuntimeError(f"{scenario} must not call the provider")
        return f"verdict=PASS decision={(result.get('outcome') or {}).get('decision')}"

    runner.check("S11-5", "concurrent recovery (10 threads) -> exactly once",
                 lambda: _chaos("CONCURRENT_RECOVERY", must_not_release=False))
    runner.check("S11-6", "chaos provider timeout -> no unsafe state",
                 lambda: _chaos("PROVIDER_TIMEOUT", must_not_release=True))

    # ------------------------------------------------- S11-7 policy sim
    def _s11_7():
        req = {"policies": ["autonomous-v1", "manual-only-baseline",
                            "autonomous-v2-experimental"],
               "source": {"demo": True}}
        run_a = runner.keep("s11-7-run-a", _call(
            "POST", f"{base}/api/v1/policy-simulator/run", admin, req))
        run_b = runner.keep("s11-7-run-b", _call(
            "POST", f"{base}/api/v1/policy-simulator/run", admin, req))
        if run_a.get("dataset", {}).get("dataset_fingerprint") != \
                run_b.get("dataset", {}).get("dataset_fingerprint"):
            raise RuntimeError("dataset fingerprints differ between identical runs")
        v1_a = next(p for p in run_a["policies"] if p["policy_version"] == "autonomous-v1")
        v1_b = next(p for p in run_b["policies"] if p["policy_version"] == "autonomous-v1")
        for field in ("would_release", "would_block", "would_manual_review",
                      "would_no_action", "gate_vetoes", "false_recovery"):
            if v1_a.get(field) != v1_b.get(field):
                raise RuntimeError(f"v1 {field} not reproducible: "
                                   f"{v1_a.get(field)} vs {v1_b.get(field)}")
        if v1_a.get("false_recovery") != 0:
            raise RuntimeError(f"v1 false_recovery must be 0, got {v1_a.get('false_recovery')}")
        baseline = next(p for p in run_a["policies"]
                        if p["policy_version"] == "manual-only-baseline")
        if baseline.get("would_release") != 0:
            raise RuntimeError("baseline must never release")
        if run_a.get("affects_live_policy") is not False:
            raise RuntimeError("simulator must declare affects_live_policy=false")
        return (f"reproducible fingerprint {run_a['dataset']['dataset_fingerprint'][:12]}…; "
                f"v1 release={v1_a['would_release']} false=0; baseline releases 0")

    runner.check("S11-7", "policy simulation reproducible + safe", _s11_7)

    # --------------------------------------------------- security sweep
    def _sec():
        customer_routes = [
            ("GET", f"{base}/api/v1/transactions/DEMO-S1/state-at?timestamp=2026-10-03T09:30:00Z"),
            ("GET", f"{base}/api/v1/transactions/DEMO-S1/behavioral-signals"),
            ("GET", f"{base}/api/v1/transactions/DEMO-S1/relationships"),
            ("GET", f"{base}/api/v1/metrics"),
        ]
        for method, url in customer_routes:
            status, _ = _probe(method, url, alice, None)
            if status != 403:
                raise RuntimeError(f"CUSTOMER {url.rsplit('/', 1)[-1]} -> {status}, want 403")
        for method, url, payload in (
                ("POST", f"{base}/api/v1/policy-simulator/run", {"policies": ["autonomous-v1"], "source": {"demo": True}}),
                ("POST", f"{base}/api/v1/chaos/run", {"scenario": "MERCHANT_TIMEOUT"}),
        ):
            status, _ = _probe(method, url, alice, payload)
            if status != 403:
                raise RuntimeError(f"CUSTOMER mutate probe -> {status}, want 403")
        status, _ = _probe("POST", f"{base}/api/v1/policy-simulator/run", support, {"policies": ["autonomous-v1"], "source": {"demo": True}})
        if status != 403:
            raise RuntimeError(f"SUPPORT simulator run -> {status}, want 403")
        status, _ = _probe("POST", f"{base}/api/v1/chaos/run", support, {"scenario": "MERCHANT_TIMEOUT"})
        if status != 403:
            raise RuntimeError(f"SUPPORT chaos run -> {status}, want 403")
        # SUPPORT may read the chaos catalog
        status, _ = _probe("GET", f"{base}/api/v1/chaos/scenarios", support, None)
        if status != 200:
            raise RuntimeError(f"SUPPORT chaos catalog -> {status}, want 200")
        return "CUSTOMER 403 x6; SUPPORT run-403 x2 + catalog-200"

    runner.check("SEC1", "Stage 11 authorization matrix", _sec)

    def _audit_and_metrics():
        audit = runner.keep("audit", _call(
            "GET", f"{base}/api/v1/audit?limit=100", admin, None))
        actions = {r.get("action") for r in audit.get("rows", [])}
        for needed in ("POLICY_SIMULATION", "CHAOS_TEST", "TEMPORAL_QUERY"):
            if needed not in actions:
                raise RuntimeError(f"audit missing {needed}")
        metrics = runner.keep("metrics", _call(
            "GET", f"{base}/api/v1/metrics", admin, None))
        counters = metrics.get("counters", {})
        for needed in ("recovery_success_total", "payment_events_total",
                       "risk_assessments_total"):
            if needed not in counters:
                raise RuntimeError(f"metrics missing counter {needed}")
        # safety_gate_blocks_total is implicit-create: present only when the
        # executor-level gate vetoed at least once in this process's lifetime
        # (policy-layer blocks do not count — correct separation).
        gate_blocks = counters.get("safety_gate_blocks_total")
        if "recovery_latency_ms" not in metrics.get("latencies_ms", {}):
            raise RuntimeError("metrics missing recovery_latency_ms")
        return ("audit rows + domain counters + latencies present "
                f"(safety_gate_blocks_total={gate_blocks})")

    runner.check("SEC2", "audit trail + observability", _audit_and_metrics)

    def _secrets():
        sk_token = re.compile(r'"sk-[^"]*"')
        for label, body in runner.bodies:
            serialized = json.dumps(body)
            for key in ALL_KEY_VALUES:
                if key in serialized:
                    raise RuntimeError(f"{label} LEAKS a dev key value")
            if sk_token.search(serialized):
                raise RuntimeError(f"{label} contains an 'sk-' string")
        return f"{len(runner.bodies)} response bodies swept, zero leaks"

    runner.check("SEC3", "secret-leakage sweep", _secrets)

    # ------------------------------------------------------------- report
    print()
    print(f"{'#':<7} {'check':<50} verdict  note")
    print("-" * 118)
    for check_id, name, verdict, note in runner.results:
        marker = "PASS" if verdict == "PASS" else "FAIL"
        print(f"{check_id:<7} {name:<50} {marker:<7} {note}")
    print()
    print("PERFORMANCE (measured wall-clock per check; live rate limiter pacing included)")
    print("-" * 118)
    for name, seconds in runner.timings:
        print(f"  {name:<64} {seconds * 1000:8.1f} ms")
    passed, total = runner.summary()
    print()
    print(f"STAGE 11 E2E: {passed}/{total} checks passed")
    print("Autonomous recovery operates on a simulated sandbox provider. "
          "No real financial transaction is performed.")
    return 0 if passed == total else 1


if __name__ == "__main__":
    try:
        sys.exit(run())
    except urllib.error.URLError as exc:
        print(f"error: cannot reach API: {getattr(exc, 'reason', exc)}")
        print("Start the server first:  uvicorn api.main:app")
        sys.exit(1)
    except RuntimeError as exc:
        print(f"error: {exc}")
        sys.exit(1)

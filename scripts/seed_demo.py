"""
scripts/seed_demo.py — Stage 9 deterministic demo seed (spec §28).
================================================================

Drives the LIVE API to create SIX fixed demo transactions (DEMO-S1..S6)
with fixed timestamps, mirroring the Stage 8 E2E builders:

  S1  merchant_timeout chain  -> the recoverable case; additionally runs
      recovery/process (AUTO_RECOVERED or ALREADY_RECOVERED both fine)
  S2  double_deduction chain  -> blocked (DOUBLE_DEDUCTION)
  S3  success (happy path)    -> blocked (ALREADY_SUCCESS)
  S4  single debit only       -> blocked (INSUFFICIENT_EVIDENCE)
  S5  merchant_timeout chain + risk assessment -> Stage 10 race scenario:
      the presenter then injects a LATE settlement via
      POST /api/v1/demo/scenarios/S5/inject-late-settlement and the real
      recovery/process is blocked by the fresh-evidence safety gate
  S6  merchant_timeout chain  -> duplicate-process demo (S1's twin)

Idempotent by design:
  * transaction create: a 400 INVALID_STATE_TRANSITION means the fixed id
    already exists from a previous run -> note "already exists", continue
  * payment events: idempotent per provider_event_id (replays count as
    duplicates)
  * risk assessment: fingerprint-reuse on the API side
  * recovery process: provider-level idempotency

The deterministic fixture, timeline constants, scenario catalog, and the
build_events() builder live in api/services/demo_scenarios.py (the shared
source of truth with the Stage 10 demo API); this script imports them and
keeps only the HTTP plumbing.

Stdlib only. Style follows scripts/stage8_e2e.py.

Usage:
  py -m scripts.seed_demo
  py -m scripts.seed_demo --api-url http://127.0.0.1:8000 --api-key dev-admin-key
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.demo_scenarios import (  # noqa: E402  (path fix above)
    DEMO_BASE,
    SCENARIOS,
    _CLEAN_FIXTURE,
    build_events,
)

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_API_KEY = "dev-admin-key"  # documented public dev placeholder


# --------------------------------------------------------------------------
# HTTP helpers (stdlib only)
# --------------------------------------------------------------------------

def _request(method: str, url: str, api_key: str, payload: dict | None
             ) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
    )
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
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _call(method: str, url: str, api_key: str, payload: dict | None,
          expect: tuple[int, ...] = (200,)) -> dict:
    status, body = _request(method, url, api_key, payload)
    if status not in expect:
        raise ApiError(
            f"{method} {url} -> HTTP {status}: {json.dumps(body)[:300]}",
            status=status,
        )
    return body


def seed_scenario(key: str, spec: dict, base_url: str, api_key: str) -> dict:
    """Seed one scenario; never raises for already-seeded state."""
    tid = f"DEMO-{key}"
    scenario_start = DEMO_BASE + timedelta(minutes=int(key[1:]) - 1)
    record: dict = {
        "key": key, "label": spec["label"], "transaction_id": tid,
        "ok": True, "error": None, "anomaly": "-", "decision": "-", "note": "",
    }

    # 1. create the transaction — 400 INVALID_STATE_TRANSITION on rerun
    status, body = _request(
        "POST", f"{base_url}/api/v1/transaction/event", api_key,
        {"transaction_id": tid, **_CLEAN_FIXTURE},
    )
    if status == 200:
        record["note"] = "created"
    elif status == 400:
        record["note"] = "already exists"
    else:
        raise ApiError(f"create {tid} -> HTTP {status}: {json.dumps(body)[:200]}",
                       status=status)

    # 2. ingest the event evidence (idempotent per provider_event_id)
    events = build_events(tid, spec["steps"], scenario_start)
    if events:
        _call("POST", f"{base_url}/api/v1/transactions/{tid}/payment-events",
              api_key, {"events": events})

    # 3. risk assessment POST — fingerprint reuse on the API side
    try:
        assessment = _call(
            "POST", f"{base_url}/api/v1/transactions/{tid}/risk-assessment",
            api_key, {"customer_reported_failure": False},
        )
        a = assessment.get("assessment") or {}
        record["anomaly"] = str(a.get("anomaly_type", "-"))
    except ApiError as exc:
        record["note"] = (record["note"] + "; assessment failed").strip("; ")
        record["error"] = str(exc)
        return record

    # 4. S1 additionally runs the autonomous recovery
    if spec["process"]:
        process = _call(
            "POST", f"{base_url}/api/v1/transactions/{tid}/recovery/process",
            api_key, {"customer_reported_failure": False},
        )
        decision = str(process.get("decision", "-"))
        if decision == "AUTO_RECOVERED":
            record["note"] = (record["note"] + "; released").strip("; ")
        elif decision == "ALREADY_RECOVERED":
            record["note"] = (record["note"] + "; already recovered").strip("; ")
        record["decision"] = decision
    else:
        # read-only: report the existing decision, if any
        status, recovery = _request(
            "GET", f"{base_url}/api/v1/transactions/{tid}/recovery", api_key, None,
        )
        if status == 200:
            action = recovery.get("action")
            record["decision"] = ("RELEASED" if action == "RELEASE_LIMIT"
                                  else str(action or "-"))
        elif status == 404:
            record["decision"] = "not processed"
        else:
            raise ApiError(f"GET recovery {tid} -> HTTP {status}", status=status)

    return record


def print_results(records: list[dict]) -> None:
    header = (f"{'#':<4} {'transaction':<12} {'scenario':<38} "
              f"{'anomaly':<28} {'decision':<16} note")
    print(header)
    print("-" * len(header))
    for r in records:
        print(f"{r['key']:<4} {r['transaction_id']:<12} {r['label']:<38} "
              f"{r['anomaly']:<28} {r['decision']:<16} {r['note']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed the deterministic demo corpus DEMO-S1..S6 "
                    "(spec §28; drives the live API, stdlib only).")
    parser.add_argument("--api-url", default=DEFAULT_API_URL,
                        help=f"API base URL (default {DEFAULT_API_URL})")
    parser.add_argument("--api-key",
                        default=os.environ.get("PAYMENT_API_KEY", DEFAULT_API_KEY),
                        help="API key (default: $PAYMENT_API_KEY or the dev "
                             "admin placeholder)")
    args = parser.parse_args(argv)

    base_url = args.api_url.rstrip("/")
    print(f"Demo seed — API: {base_url}  transactions: DEMO-S1..DEMO-S6")
    print()

    records: list[dict] = []
    for key, spec in SCENARIOS.items():
        print(f"[{key}] {spec['label']} ...", flush=True)
        try:
            records.append(seed_scenario(key, spec, base_url, args.api_key))
        except ApiError as exc:
            print(f"    FAIL: {exc}")
            records.append({
                "key": key, "label": spec["label"],
                "transaction_id": f"DEMO-{key}", "ok": False,
                "error": str(exc), "anomaly": "-", "decision": "-", "note": "",
            })
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            print(f"error: cannot reach API at {base_url}: {reason}")
            print("Start the server first:  uvicorn api.main:app --reload")
            return 1
        except RuntimeError as exc:
            print(f"error: {exc}")
            return 1

    print()
    print_results(records)
    failed = [r for r in records if not r["ok"]]
    print()
    print(f"Summary: {len(records) - len(failed)}/{len(records)} scenarios seeded"
          + ("" if not failed else "  ->  see FAIL lines above"))
    print("All sandbox operations are simulated. No real financial "
          "transaction is performed.")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

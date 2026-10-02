"""
Stage 10 — Demo Health Check
Project: AI-Powered Payment Failure Recovery & Digital Twin System
====================================================================

Pre-flight check for the live demo: is the API up, is the database
connected, are the ML models loaded, is the sandbox provider ready, is
GenAI available (fallback is acceptable and reported honestly), and are
all six DEMO-S1..S6 scenarios seeded. Optionally probes the frontend dev
server — unreachable is a WARN, not a FAIL, unless --require-frontend.

The script NEVER claims readiness while anything failed. Exit 0 only
when every required check passes. Stdlib only; no secret is ever printed.

Usage:
  py -m scripts.stage10_demo_check
  py -m scripts.stage10_demo_check --api-url http://127.0.0.1:8000 \
      --frontend-url http://localhost:5173 --require-frontend --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# Ensure the project root is importable when run as a plain script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_FRONTEND_URL = "http://localhost:5173"
DEFAULT_API_KEY = "dev-admin-key"  # documented public dev placeholder (README)

EXPECTED_SCENARIO_KEYS = ["S1", "S2", "S3", "S4", "S5", "S6"]

# --------------------------------------------------------------------------
# HTTP helpers (stdlib only)
# --------------------------------------------------------------------------

def _request(method: str, url: str, api_key: str | None, payload: dict | None,
             verbose: bool) -> tuple[int, dict]:
    """One JSON API call. Returns (status_code, parsed_body)."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(url, data=data, method=method,
                                     headers=headers)
    if verbose:
        print(f"[verbose] {method} {url}")
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read().decode("utf-8")
        try:
            return response.status, json.loads(raw)
        except json.JSONDecodeError:
            # Non-JSON body (e.g. the frontend dev server's HTML index) —
            # still a valid response for a reachability probe.
            return response.status, {"_raw": raw[:200]}
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


def _call(method: str, url: str, api_key: str | None, payload: dict | None,
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
# Checks
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage 10 demo pre-flight check: API, database, ML, "
                    "sandbox, GenAI, demo data, frontend (stdlib only).")
    parser.add_argument("--api-url", default=DEFAULT_API_URL,
                        help=f"API base URL (default {DEFAULT_API_URL})")
    parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL,
                        help=f"frontend dev server URL "
                             f"(default {DEFAULT_FRONTEND_URL})")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY,
                        help="API key for demo endpoints "
                             "(default: the documented dev ADMIN placeholder)")
    parser.add_argument("--require-frontend", action="store_true",
                        help="treat an unreachable frontend as FAIL instead "
                             "of WARN")
    parser.add_argument("--verbose", action="store_true",
                        help="log raw requests and responses")
    args = parser.parse_args(argv)

    base = args.api_url.rstrip("/")
    results: list[tuple[str, str, str]] = []  # (name, verdict, note)

    # ---- 1. API reachable ------------------------------------------------
    api_up = False
    health: dict = {}
    try:
        health = _call("GET", f"{base}/health", None, None, args.verbose)
        results.append(("API", "PASS", f"status={health.get('status', '?')}"))
        api_up = True
    except (ApiError, RuntimeError, urllib.error.URLError) as exc:
        results.append(("API", "FAIL", _short(exc)))

    # ---- 2. DATABASE ------------------------------------------------------
    if api_up:
        if health.get("database") == "connected":
            results.append(("DATABASE", "PASS", "connected"))
        else:
            results.append(("DATABASE", "FAIL",
                            f"health.database={health.get('database')!r}"))
    else:
        results.append(("DATABASE", "SKIP", "API unreachable"))

    # ---- 3. ML ------------------------------------------------------------
    if api_up:
        if health.get("ml_models") == "loaded":
            results.append(("ML", "PASS", "models loaded"))
        else:
            results.append(("ML", "FAIL",
                            f"health.ml_models={health.get('ml_models')!r}"))
    else:
        results.append(("ML", "SKIP", "API unreachable"))

    # ---- 4. SANDBOX (demo status) -----------------------------------------
    status_body: dict | None = None
    if api_up:
        try:
            status_body = _call("GET", f"{base}/api/v1/demo/status",
                                args.api_key, None, args.verbose)
        except ApiError as exc:
            if exc.status in (401, 404):
                results.append(("SANDBOX", "FAIL",
                                "demo endpoints not deployed "
                                f"(HTTP {exc.status})"))
            else:
                results.append(("SANDBOX", "FAIL", _short(exc)))
        except RuntimeError as exc:
            results.append(("SANDBOX", "FAIL", _short(exc)))
        if status_body is not None:
            sandbox = status_body.get("sandbox_provider")
            if not isinstance(sandbox, dict):
                results.append(("SANDBOX", "FAIL",
                                "demo/status has no sandbox_provider object"))
            else:
                available = sandbox.get("available_limit")
                if isinstance(available, (int, float)) and available >= 0:
                    results.append((
                        "SANDBOX", "PASS",
                        f"provider={sandbox.get('provider', '?')}, "
                        f"available_limit={available} "
                        f"{sandbox.get('currency', '')}"))
                else:
                    results.append(("SANDBOX", "FAIL",
                                    f"available_limit={available!r}"))
    else:
        results.append(("SANDBOX", "SKIP", "API unreachable"))

    # ---- 5. GENAI ----------------------------------------------------------
    if status_body is not None:
        genai = status_body.get("genai") or {}
        genai_status = genai.get("status")
        if genai_status == "available":
            results.append(("GENAI", "PASS",
                            f"provider={genai.get('provider', '?')}"))
        elif genai_status == "fallback":
            results.append(("GENAI", "PASS",
                            "fallback (degraded but demo-safe; "
                            "reported honestly)"))
        else:
            results.append(("GENAI", "FAIL", f"status={genai_status!r}"))
    else:
        results.append(("GENAI", "SKIP", "demo status unavailable"))

    # ---- 6. DEMO DATA -------------------------------------------------------
    if api_up:
        try:
            scenarios_body = _call("GET", f"{base}/api/v1/demo/scenarios",
                                   args.api_key, None, args.verbose)
            scenarios = scenarios_body.get("scenarios") or []
            present = [str(s.get("key")) for s in scenarios
                       if isinstance(s, dict) and s.get("key")]
            missing = [k for k in EXPECTED_SCENARIO_KEYS if k not in present]
            extra = [k for k in present
                     if k not in EXPECTED_SCENARIO_KEYS]
            if missing:
                results.append(("DEMO DATA", "FAIL",
                                f"missing scenarios: {', '.join(missing)} "
                                f"(present: {', '.join(present) or 'none'})"))
            else:
                note = f"all {len(EXPECTED_SCENARIO_KEYS)} seeded"
                if extra:
                    note += f", extra: {', '.join(extra)}"
                results.append(("DEMO DATA", "PASS", note))
        except ApiError as exc:
            if exc.status in (401, 404):
                results.append(("DEMO DATA", "FAIL",
                                "demo endpoints not deployed "
                                f"(HTTP {exc.status})"))
            else:
                results.append(("DEMO DATA", "FAIL", _short(exc)))
        except RuntimeError as exc:
            results.append(("DEMO DATA", "FAIL", _short(exc)))
    else:
        results.append(("DEMO DATA", "SKIP", "API unreachable"))

    # ---- 7. FRONTEND (optional) ---------------------------------------------
    try:
        frontend_status, _body = _request("GET", args.frontend_url, None,
                                          None, args.verbose)
        if frontend_status == 200:
            results.append(("FRONTEND", "PASS", args.frontend_url))
        else:
            results.append(("FRONTEND", "WARN",
                            f"HTTP {frontend_status} (unexpected)"))
    except (RuntimeError, urllib.error.URLError, OSError) as exc:
        if args.require_frontend:
            results.append(("FRONTEND", "FAIL", _short(exc)))
        else:
            results.append(("FRONTEND", "WARN",
                            "not running (backend-only check)"))

    # ---- Summary -------------------------------------------------------------
    print()
    print("STAGE 10 DEMO CHECK")
    failed = 0
    for name, verdict, note in results:
        print(f"{name:<16} {verdict:<5}"
              + (f"  {note}" if note else ""))
        if verdict == "FAIL":
            failed += 1
    print()
    if failed:
        print(f"NOT READY -- {failed} check(s) failed")
        return 1
    print("READY FOR DEMO")
    return 0


def _short(exc: Exception) -> str:
    text = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
    return text[:120]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        print(f"error: cannot reach API: {reason}")
        print("Start the server first:  uvicorn api.main:app --reload")
        sys.exit(1)
    except RuntimeError as exc:
        print(f"error: {exc}")
        sys.exit(1)

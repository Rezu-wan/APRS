"""
Stage 6 — Payment-Domain Event Sequence Simulator
Project: AI-Powered Payment Failure Recovery & Digital Twin System
================================================================

Generates realistic payment-DOMAIN event sequences (customer bank debit ->
gateway -> merchant confirmation -> settlement) for testing and demoing the
reconstruction engine. This is DISTINCT from the Stage 1 transaction
generator (scripts/generate_dataset.py), which makes transactions; this
script makes the fine-grained provider evidence stream a payment emits.

The event vocabulary (event types, sources, stages, outcomes, happy path) is
IMPORTED from api/core/payment_lifecycle.py — the shared, pinned vocabulary —
so the simulator can never drift from what ingestion and the reconstruction
engine reason over.

Scenarios (each an ordered list of payment events):
  success                  all 7 happy-path events, CONFIRMED outcomes
  gateway_timeout          debit OK, request sent, gateway times out (~3s gap)
  gateway_error            debit OK, request sent, gateway errors (~200ms)
  merchant_timeout         through merchant request, merchant times out (~10s gap)
  merchant_error           through merchant request, merchant errors
  settlement_failure       through settlement request, settlement fails
  settlement_not_confirmed through settlement request, settlement not confirmed
  debit_failure            customer bank debit fails immediately

Determinism: the same --seed + --scenario + --start always produce
byte-identical output. The seed feeds random.Random(seed) ONLY for latency
jitter (base latency +-20%). provider_event_ids are derived from the
transaction id + event type + sequence number, so they are stable across
runs and re-ingestion is idempotent on the API side.

This is a developer/demo CLI: the default --api-url points at the local dev
server (http://127.0.0.1:8000) — the "no hardcoded localhost" rule applies
to frontend components, not this CLI, and the default is overridable via the
flag. Uses stdlib only (no added dependencies).

Usage:
  py -m scripts.payment_event_simulator --scenario merchant_timeout
  py -m scripts.payment_event_simulator --transaction-id TXN-SIM-DRY \
      --scenario gateway_timeout --seed 42 \
      --start "2026-10-02T12:30:01" --ingest --api-url http://127.0.0.1:8000
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

# Base per-event latencies (ms) and terminal-failure gaps (ms). The gap for a
# timeout scenario is expressed as a LATER event whose latency_ms carries the
# wait — e.g. GATEWAY_TIMEOUT arrives ~3s after GATEWAY_REQUEST_SENT.
BASE_LATENCY_MS = 120        # progress / confirmation hops
TERMINAL_EVENT_MS = 90       # terminal failure events themselves
GATEWAY_TIMEOUT_GAP_MS = 3_000
MERCHANT_TIMEOUT_GAP_MS = 10_000

# scenario -> ordered steps. Each step is (event_type, extra_delay_ms):
#   extra_delay_ms  is the wall-clock wait BEFORE this event fires (cumulative
#                   with prior base latencies); 0 for normal chained hops.
#   source and status are NEVER hardcoded here — they are derived from
#   EVENT_TYPE_INFO, the pinned vocabulary.
_SCENARIO_STEPS: dict[str, list[tuple[str, int]]] = {
    "success": [(event_type, 0) for event_type in HAPPY_PATH_EVENTS],
    "gateway_timeout": [
        ("CUSTOMER_DEBIT_CONFIRMED", 0),
        ("GATEWAY_REQUEST_SENT", 0),
        ("GATEWAY_TIMEOUT", GATEWAY_TIMEOUT_GAP_MS),
    ],
    "gateway_error": [
        ("CUSTOMER_DEBIT_CONFIRMED", 0),
        ("GATEWAY_REQUEST_SENT", 0),
        ("GATEWAY_ERROR", 200),
    ],
    "merchant_timeout": [
        ("CUSTOMER_DEBIT_CONFIRMED", 0),
        ("GATEWAY_REQUEST_SENT", 0),
        ("GATEWAY_RESPONSE_RECEIVED", 0),
        ("MERCHANT_CONFIRMATION_REQUESTED", 0),
        ("MERCHANT_CONFIRMATION_TIMEOUT", MERCHANT_TIMEOUT_GAP_MS),
    ],
    "merchant_error": [
        ("CUSTOMER_DEBIT_CONFIRMED", 0),
        ("GATEWAY_REQUEST_SENT", 0),
        ("GATEWAY_RESPONSE_RECEIVED", 0),
        ("MERCHANT_CONFIRMATION_REQUESTED", 0),
        ("MERCHANT_ERROR", 500),
    ],
    "settlement_failure": [
        ("CUSTOMER_DEBIT_CONFIRMED", 0),
        ("GATEWAY_REQUEST_SENT", 0),
        ("GATEWAY_RESPONSE_RECEIVED", 0),
        ("MERCHANT_CONFIRMATION_REQUESTED", 0),
        ("MERCHANT_CONFIRMATION_RECEIVED", 0),
        ("SETTLEMENT_REQUESTED", 0),
        ("SETTLEMENT_FAILED", 800),
    ],
    "settlement_not_confirmed": [
        ("CUSTOMER_DEBIT_CONFIRMED", 0),
        ("GATEWAY_REQUEST_SENT", 0),
        ("GATEWAY_RESPONSE_RECEIVED", 0),
        ("MERCHANT_CONFIRMATION_REQUESTED", 0),
        ("MERCHANT_CONFIRMATION_RECEIVED", 0),
        ("SETTLEMENT_REQUESTED", 0),
        ("SETTLEMENT_NOT_CONFIRMED", 800),
    ],
    "debit_failure": [
        ("CUSTOMER_DEBIT_FAILED", 0),
    ],
}

SCENARIOS = tuple(_SCENARIO_STEPS)


def _base_latency_ms(event_type: str, seq: int) -> int:
    """Base latency for an event; timeout terminal events carry the gap."""
    info = EVENT_TYPE_INFO[event_type]
    if info["outcome"] == "TIMEOUT":
        # Gap accumulated by the preceding extra_delay step; the terminal
        # event's own processing is still small.
        return TERMINAL_EVENT_MS
    if info["outcome"] == "OBSERVED" or info["outcome"] == "CONFIRMED":
        # Later stages settle slower (settlement hops are heavier).
        return BASE_LATENCY_MS + seq * 40
    return TERMINAL_EVENT_MS


def default_transaction_id(scenario: str, seed: int) -> str:
    """Deterministic fallback id: TXN-SIM-<8 hex from seed+scenario>."""
    digest = hashlib.sha256(f"{seed}:{scenario}".encode("utf-8")).hexdigest()
    return f"TXN-SIM-{digest[:8]}"


def build_events(
    transaction_id: str,
    scenario: str,
    start: datetime,
    seed: int,
) -> list[dict]:
    """Build the ordered payment-event list for one scenario.

    Deterministic: rng is used ONLY for latency jitter (+-20%), so identical
    (transaction_id, scenario, start, seed) produce identical output.
    """
    rng = random.Random(seed)
    steps = _SCENARIO_STEPS[scenario]
    events: list[dict] = []
    elapsed_ms = 0.0

    for seq, (event_type, extra_delay_ms) in enumerate(steps):
        info = EVENT_TYPE_INFO[event_type]
        elapsed_ms += extra_delay_ms

        base = _base_latency_ms(event_type, seq)
        latency_ms = int(round(base * rng.uniform(1.0 - JITTER_FRACTION,
                                                   1.0 + JITTER_FRACTION)))
        elapsed_ms += latency_ms

        events.append({
            "provider_event_id": f"{transaction_id}-{event_type}-{seq:03d}",
            "event_type": event_type,
            "source": info["source"],        # from the pinned vocabulary
            "status": info["outcome"],       # from the pinned vocabulary
            "event_timestamp": (start + timedelta(milliseconds=elapsed_ms))
                .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "reference_id": f"{transaction_id}-{info['stage'].lower()}-ref",
            "latency_ms": latency_ms,
        })

    return events


def print_events(events: list[dict]) -> None:
    """Human-readable table + a JSON block matching the ingestion contract."""
    header = f"{'event_timestamp':<24} {'event_type':<32} {'source':<10} {'status':<14} {'latency_ms':>10}"
    print(header)
    print("-" * len(header))
    for e in events:
        print(f"{e['event_timestamp']:<24} {e['event_type']:<32} "
              f"{e['source']:<10} {e['status']:<14} {e['latency_ms']:>10}")

    payload = {
        "events": [
            {key: e[key] for key in (
                "provider_event_id", "event_type", "source", "status",
                "event_timestamp", "reference_id", "latency_ms")}
            for e in events
        ]
    }
    print("\nIngestion payload:")
    print(json.dumps(payload, indent=2))


def ingest_events(api_url: str, api_key: str, transaction_id: str,
                  events: list[dict], verbose: bool) -> int:
    """POST the events; returns a process exit code."""
    url = f"{api_url.rstrip('/')}/api/v1/transactions/{transaction_id}/payment-events"
    payload = json.dumps({
        "events": [
            {key: e[key] for key in (
                "provider_event_id", "event_type", "source", "status",
                "event_timestamp", "reference_id", "latency_ms")}
            for e in events
        ]
    }).encode("utf-8")

    request = urllib.request.Request(
        url, data=payload, method="POST",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        },
    )
    if verbose:
        print(f"[verbose] POST {url}")

    try:
        with urllib.request.urlopen(request) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        if exc.code == 404:
            print(f"error: transaction '{transaction_id}' not found (HTTP 404). "
                  f"Create it first, e.g.:\n"
                  f"  curl -X POST {api_url.rstrip('/')}/api/v1/transaction/event "
                  f"-H \"Content-Type: application/json\" -H \"X-API-Key: {api_key}\" "
                  f"-d '{{\"event_type\": \"TRANSACTION_CREATED\", "
                  f"\"transaction_id\": \"{transaction_id}\", "
                  f"\"payload\": {{\"amount\": 100.00}}}}'")
        else:
            print(f"error: API returned HTTP {exc.code}: {detail or exc.reason}")
        if verbose:
            import traceback
            traceback.print_exc()
        return 1
    except urllib.error.URLError as exc:
        print(f"error: cannot reach API at {api_url}: {exc.reason}")
        if verbose:
            import traceback
            traceback.print_exc()
        return 1

    created = body.get("created", body.get("created_count", "?"))
    duplicates = body.get("duplicates", body.get("duplicate_count", "?"))
    print(f"Ingested into {transaction_id}: created={created}, duplicates={duplicates}")
    if verbose:
        print(f"[verbose] response: {json.dumps(body)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage 6 payment-domain event sequence simulator "
                    "(deterministic; stdlib only).")
    parser.add_argument("--transaction-id", default=None,
                        help="transaction id (default: TXN-SIM-<8 hex from seed+scenario>)")
    parser.add_argument("--scenario", required=True, choices=SCENARIOS,
                        help="payment failure/success scenario to simulate")
    parser.add_argument("--seed", type=int, default=SEED,
                        help=f"random seed, used ONLY for latency jitter (default {SEED})")
    parser.add_argument("--start", default=None,
                        help='first event window start, ISO-8601 '
                             '(e.g. "2026-10-02T12:30:01"; default: now UTC)')
    parser.add_argument("--ingest", action="store_true",
                        help="POST the events to the API instead of printing")
    parser.add_argument("--api-url", default=DEFAULT_API_URL,
                        help=f"API base URL (default {DEFAULT_API_URL})")
    parser.add_argument("--api-key", default=os.environ.get("PAYMENT_API_KEY", DEFAULT_API_KEY),
                        help="API key (default: $PAYMENT_API_KEY or the dev placeholder)")
    parser.add_argument("--verbose", action="store_true",
                        help="log details (stack traces, raw responses)")
    args = parser.parse_args(argv)

    if args.start is not None:
        raw = args.start.strip().replace(" ", "T")
        try:
            start = datetime.fromisoformat(raw)
        except ValueError:
            parser.error(f"--start is not valid ISO-8601: {args.start!r}")
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        start = start.astimezone(timezone.utc)
    else:
        start = datetime.now(timezone.utc)

    transaction_id = args.transaction_id or default_transaction_id(args.scenario, args.seed)
    events = build_events(transaction_id, args.scenario, start, args.seed)

    if args.ingest:
        return ingest_events(args.api_url, args.api_key, transaction_id,
                             events, args.verbose)

    print(f"Scenario: {args.scenario}  transaction: {transaction_id}  "
          f"seed: {args.seed}  start: {start.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print()
    print_events(events)
    return 0


if __name__ == "__main__":
    sys.exit(main())

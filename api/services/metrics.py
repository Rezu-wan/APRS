"""api/services/metrics.py — Stage 11G in-process metrics registry.

A deliberately minimal, dependency-free observability registry:

  * counters   — monotonic floats (request counts, domain event counts)
  * gauges     — last-value floats (queue depth, cache size, ...)
  * latencies  — per-name {count, sum_ms, min_ms, max_ms}; average is
    computed on read, so no histogram buckets and no extra dependencies

SANDBOX LIMITATION (same class as the Stage 9 in-process rate limiter,
documented deliberately): the registry is in-memory and per-process. It
resets on restart and is NOT aggregated across workers/replicas. A
production deployment would swap this for Prometheus/OTel; the snapshot()
shape and the metric-name constants below are the seam.

CARDINALITY RULE (critical): metric names are code constants or the bounded
path classes from path_class(). Metrics carry NO transaction ids, NO user
ids, NO amounts — never interpolate identifiers into a metric name. Unknown
names are created implicitly on first record (no registration ceremony);
that is safe only as long as callers obey the cardinality rule above.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from time import monotonic as _monotonic

# ---------------------------------------------------------------------------
# Canonical metric-name constants.
#
# The observability integrator wires these into the domain services AFTER the
# Stage 11 wave (to avoid file conflicts). Import from here — never inline
# the string literals at call sites.
# ---------------------------------------------------------------------------

METRICS_TRANSACTIONS_TOTAL = "transactions_total"
METRICS_PAYMENT_EVENTS_TOTAL = "payment_events_total"
METRICS_RECONSTRUCTION_TOTAL = "reconstruction_total"
METRICS_RISK_ASSESSMENTS_TOTAL = "risk_assessments_total"
METRICS_RECOVERY_ATTEMPTS_TOTAL = "recovery_attempts_total"
METRICS_RECOVERY_SUCCESS_TOTAL = "recovery_success_total"
METRICS_RECOVERY_BLOCKED_TOTAL = "recovery_blocked_total"
METRICS_RECOVERY_FAILED_TOTAL = "recovery_failed_total"
METRICS_MANUAL_REVIEW_TOTAL = "manual_review_total"
METRICS_PROVIDER_CALLS_TOTAL = "provider_calls_total"
METRICS_PROVIDER_ERRORS_TOTAL = "provider_errors_total"
METRICS_SAFETY_GATE_BLOCKS_TOTAL = "safety_gate_blocks_total"
METRICS_RECONSTRUCTION_LATENCY = "reconstruction_latency_ms"
METRICS_RISK_LATENCY = "risk_latency_ms"
METRICS_RECOVERY_LATENCY = "recovery_latency_ms"
METRICS_VERIFICATION_LATENCY = "verification_latency_ms"

# Process start for the /metrics uptime readout. Module import time is close
# enough to process start for a single-process SANDBOX.
_STARTED_MONOTONIC = _monotonic()


def uptime_s() -> float:
    """Seconds since this module was imported (process-uptime proxy)."""
    return max(0.0, _monotonic() - _STARTED_MONOTONIC)


def path_class(path: str) -> str:
    """Map a request path to a BOUNDED metric class name.

    Deterministic pure function; the result set is finite and never contains
    client-controlled segments (ids are swallowed), so it is safe to use as a
    metric-name suffix. Used by api.middleware for HTTP latency/counting.
    """
    p = (path or "").rstrip("/")
    if not p:
        return "other"
    if p == "/health":
        return "health"
    if p == "/api/v1/auth" or p.startswith("/api/v1/auth/"):
        return "auth"
    if p == "/api/v1/transactions":
        return "transactions_list"
    if p == "/api/v1/transaction/event":
        return "transaction_create"
    if p.startswith("/api/v1/transactions/"):
        rest = p[len("/api/v1/transactions/"):].strip("/")
        if not rest:
            return "transactions_list"
        parts = rest.split("/")
        if len(parts) == 1:
            return "transaction_detail"  # /transactions/{id}
        # /transactions/{id}/<rest> — {id} swallowed, rest joined with "_"
        # (hyphens normalized too: "payment-events" -> "payment_events")
        return "transaction_" + "_".join(parts[1:]).replace("-", "_")
    if p == "/api/v1/demo" or p.startswith("/api/v1/demo/"):
        rest = p[len("/api/v1/demo"):].strip("/")
        return "demo_" + rest.replace("/", "_") if rest else "demo"
    if p == "/api/v1/stats/summary":
        return "stats_summary"
    if p == "/api/v1/metrics":
        return "metrics"
    return "other"


class MetricsRegistry:
    """Thread-safe in-process registry (one lock, plain dicts)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        # name -> [count, sum_ms, min_ms, max_ms]
        self._latencies: dict[str, list[float]] = {}

    # -- writers ----------------------------------------------------------

    def record_counter(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0.0) + value

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def record_latency(self, name: str, duration_ms: float) -> None:
        with self._lock:
            entry = self._latencies.get(name)
            if entry is None:
                self._latencies[name] = [1, float(duration_ms), float(duration_ms), float(duration_ms)]
            else:
                entry[0] += 1
                entry[1] += duration_ms
                if duration_ms < entry[2]:
                    entry[2] = duration_ms
                if duration_ms > entry[3]:
                    entry[3] = duration_ms

    # -- readers ----------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            latencies = {
                name: {
                    "count": int(entry[0]),
                    "sum_ms": entry[1],
                    # count >= 1 whenever an entry exists, so no div-by-zero
                    "avg_ms": entry[1] / entry[0],
                    "min_ms": entry[2],
                    "max_ms": entry[3],
                }
                for name, entry in self._latencies.items()
            }
        return {
            "counters": counters,
            "gauges": gauges,
            "latencies_ms": latencies,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def reset(self) -> None:
        """Test hook: wipe all recorded series."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._latencies.clear()


# Module-level singleton — import get_metrics() / reset_metrics(), never the
# private instance, at call sites.
_registry = MetricsRegistry()


def get_metrics() -> MetricsRegistry:
    return _registry


def reset_metrics() -> None:
    _registry.reset()


# -- module-level convenience wrappers (Stage 11G integration contract) ----
# Call sites import these so instrumentation code does not reach into the
# private instance, e.g. record_counter(METRICS_PAYMENT_EVENTS_TOTAL, 3.0).

def record_counter(name: str, value: float = 1.0) -> None:
    _registry.record_counter(name, value)


def set_gauge(name: str, value: float) -> None:
    _registry.set_gauge(name, value)


def record_latency(name: str, duration_ms: float) -> None:
    _registry.record_latency(name, duration_ms)

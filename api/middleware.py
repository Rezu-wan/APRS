"""api/middleware.py — Stage 9 cross-cutting HTTP middleware.

One ASGI middleware function (registered in api/main.py) that provides:

  1. Request-id propagation: honors a well-formed incoming ``X-Request-ID``
     (else generates one), exposes it via api.core.request_context for error
     bodies and logs, and echoes it on every response.
  2. Security headers on every response (nosniff, frame denial, strict
     referrer policy, locked-down permissions policy).
  3. Per-key rate limiting: a sliding-window (deque) limiter keyed on the
     ROLE KEY NAME — never the raw secret key. Limits are per minute.

SANDBOX LIMITATION (documented deliberately): the limiter is in-memory and
per-process. It resets on restart and is NOT distributed — behind multiple
workers/replicas each process counts independently. A production deployment
would swap this for Redis or a gateway-level limiter; the bucket/limit
contract below is the seam.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from api.core.config import get_settings
from api.core.request_context import new_request_id, set_request_id
from api.services.metrics import get_metrics, path_class


def _record_http_metrics(path_class_name: str, duration_ms: float | None) -> None:
    """Stage 11G: best-effort metric recording — must NEVER break the request
    path, so any registry failure is logged and swallowed. Names are bounded
    path classes (no ids/user data — see metrics.path_class)."""
    try:
        registry = get_metrics()
        registry.record_counter(f"http_{path_class_name}_requests_total")
        if duration_ms is not None:
            registry.record_latency(f"http_{path_class_name}", duration_ms)
    except Exception:  # noqa: BLE001 — observability must not break serving
        logger.exception("metrics recording failed for class %s", path_class_name)

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Security headers applied to every response.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

# Per-minute limits by bucket. Order matters: first matching fragment wins.
RATE_LIMITS: list[tuple[str, tuple[str, ...], int]] = [
    ("explanations", ("/explanations/",), 30),
    ("recovery", ("/recovery/process", "/recovery/evaluate", "/recovery/release-limit"), 20),
    ("risk", ("/risk-assessment",), 30),
    ("payment-events", ("/payment-events",), 60),
    ("auth", ("/auth/",), 60),
    ("demo", ("/demo/",), 60),
    ("customer-reports", ("/customer-report",), 20),
    # support workspace: one agent session fires queue+search+profile reads
    # plus an SSE ticket; generous but still per-key and bounded
    ("support", ("/support/",), 240),
]
DEFAULT_LIMIT = 120


def bucket_for_path(path: str) -> tuple[str, int]:
    for name, fragments, limit in RATE_LIMITS:
        if any(f in path for f in fragments):
            return name, limit
    return "default", DEFAULT_LIMIT


def bucket_key(key_name: str, bucket: str) -> str:
    """Rate buckets are keyed on the ROLE KEY NAME (a config label), never
    the raw secret — raw keys must never appear in logs or in-memory maps
    keyed by attacker-observable behavior."""
    return f"{key_name}:{bucket}"


class SlidingWindowLimiter:
    """In-memory sliding-window counter (deque of timestamps). Thread-safe;
    per-process by design (see module docstring)."""

    def __init__(self, window_seconds: float = 60.0):
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, now: float | None = None) -> bool:
        """Record one hit for `key`; return True if allowed, False if the
        per-window limit is exceeded (the hit is still recorded)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            cutoff = now - self.window_seconds
            while hits and hits[0] <= cutoff:
                hits.popleft()
            hits.append(now)
            return len(hits) <= limit


limiter = SlidingWindowLimiter()

logger = logging.getLogger("payment_recovery.middleware")


def resolve_key_name(x_api_key: str | None) -> str:
    """Map the incoming key to its non-secret config label for rate
    bucketing. Invalid or missing keys share one "anonymous" bucket so
    unauthenticated floods still get limited."""
    if x_api_key:
        from api.core.security import _key_map

        for key, ctx in _key_map(get_settings()).items():
            if x_api_key == key:
                return ctx.key_name
    return "anonymous"


async def request_middleware(request: Request, call_next) -> Response:
    settings = get_settings()

    # 0. Stage 11G: bounded path class for metrics — computed up front so the
    # 429 rate-limit early-return path below is counted too.
    klass = path_class(request.url.path)

    # 1. request id: honor well-formed client ids, else generate
    incoming = request.headers.get(REQUEST_ID_HEADER)
    request_id = incoming if incoming and _REQUEST_ID_PATTERN.match(incoming) else new_request_id()
    set_request_id(request_id)

    # 2. rate limiting (never in tests; opt-out via RATE_LIMIT_ENABLED=false)
    if settings.rate_limit_enabled and settings.environment != "test":
        bucket, limit = bucket_for_path(request.url.path)
        identity = resolve_key_name(request.headers.get("X-API-Key"))
        if not limiter.check(bucket_key(identity, bucket), limit):
            _record_http_metrics(klass, None)
            return JSONResponse(
                status_code=429,
                headers={
                    "Retry-After": "60",
                    REQUEST_ID_HEADER: request_id,
                    **SECURITY_HEADERS,
                },
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Too many requests — slow down.",
                        "request_id": request_id,
                    }
                },
            )

    start = time.perf_counter()
    response = await call_next(request)
    _record_http_metrics(klass, (time.perf_counter() - start) * 1000.0)

    # 3. echo request id + security headers on every response
    response.headers[REQUEST_ID_HEADER] = request_id
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response

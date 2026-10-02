"""api/core/request_context.py — per-request context via contextvars.

Holds the request id for the current async/task context so that error
handlers and log records can correlate to a single request without threading
the value through every signature. Set by api/middleware.py, consumed by the
error handlers in api/main.py and the request-id logging filter.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    """The request id for the current context; "-" when unset."""
    return request_id_var.get()


def set_request_id(value: str) -> None:
    request_id_var.set(value)


def new_request_id() -> str:
    """Short uuid4 hex id (16 chars) — unique enough per process, log-friendly."""
    return uuid.uuid4().hex[:16]


class RequestIdLogFilter(logging.Filter):
    """Adds record.request_id to every log record ("-"/unset safe), so the
    root handler's format can include request correlation idempotently."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True

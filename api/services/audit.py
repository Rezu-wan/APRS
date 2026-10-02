"""
api/services/audit.py — Stage 9 security audit service.

The security audit trail answers WHO did WHAT to WHICH resource for the
actions that matter to the safety model — auth failures, rate limiting,
forbidden attempts, demo resets, non-SYSTEM-triggered recoveries. It is
deliberately distinct from the Digital Twin (the payment narrative): this is
the security/ops record.

Rules:
  * NEVER logs secrets — actor_id is the key NAME ("api_key_admin"), which
    is how security.py identifies keys, never the key value itself.
  * Best-effort: a failed audit write must never break (or roll back) the
    caller's operation — record_security_event swallows and logs everything.
  * Request correlation is read from the request-id contextvar lazily (the
    middleware owns it); when unavailable the row simply has request_id
    NULL — an audit row without correlation beats no audit row.
"""

from __future__ import annotations

import contextvars
import importlib
import logging

from sqlalchemy.orm import Session

from api.db.models import SecurityAuditRecord

logger = logging.getLogger("payment_recovery.audit")

# The audit action vocabulary (Stage 9, pinned):
AUDIT_AUTH_FAILURE = "AUTH_FAILURE"                  # bad/missing API key
AUDIT_RATE_LIMITED = "RATE_LIMITED"                  # middleware throttled a caller
AUDIT_PAYMENT_EVENT_CONFLICT = "PAYMENT_EVENT_CONFLICT"  # 409 on event redelivery with different content
AUDIT_PAYMENT_EVENT_REJECTED = "PAYMENT_EVENT_REJECTED"  # event batch failed validation
AUDIT_FORBIDDEN = "FORBIDDEN"                        # authenticated but role not allowed
AUDIT_DEMO_RESET = "DEMO_RESET"                      # sandbox ledger reset
AUDIT_DEMO_SEED = "DEMO_SEED"                        # Stage 10 demo scenario prepare/inject
AUDIT_RECOVERY_PROCESS = "RECOVERY_PROCESS"          # non-SYSTEM-triggered autonomous recovery
# Stage 11 research/ops actions (same best-effort contract as above):
AUDIT_POLICY_SIMULATION = "POLICY_SIMULATION"        # policy simulator run (SYSTEM/ADMIN)
AUDIT_CHAOS_TEST = "CHAOS_TEST"                      # chaos scenario executed (SYSTEM/ADMIN)
AUDIT_TEMPORAL_QUERY = "TEMPORAL_QUERY"              # state-at-timestamp historical query (staff)
AUDIT_GRAPH_ANALYSIS = "GRAPH_ANALYSIS"              # relationship analysis access (staff)
AUDIT_MODEL_SIGNAL = "MODEL_SIGNAL"                  # behavioral signal access (staff)

_RESULT_ALLOWED = "ALLOWED"
_RESULT_DENIED = "DENIED"

# (module, attribute) candidates for the middleware-owned request-id
# contextvar, tried lazily in order. Supports both a bare ContextVar and a
# module-level getter function named get_request_id.
_REQUEST_ID_SOURCES: tuple[tuple[str, str], ...] = (
    ("api.core.request_context", "request_id"),
    ("api.core.request_context", "request_id_var"),
    ("api.middleware", "request_id"),
    ("api.core.middleware", "request_id"),
)


def _contextvar_value(obj) -> str | None:
    """Unwrap a ContextVar (default None) or pass a plain str through.
    The middleware's sentinel "-" (no request) counts as absent."""
    if isinstance(obj, contextvars.ContextVar):
        try:
            obj = obj.get()
        except LookupError:
            return None
    return obj if isinstance(obj, str) and obj and obj != "-" else None


def current_request_id() -> str | None:
    """The middleware-owned request id for the current context, or None."""
    for module_name, attr in _REQUEST_ID_SOURCES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        value = getattr(module, attr, None)
        resolved = _contextvar_value(value)
        if resolved:
            return resolved
    try:  # a getter function beats raw attributes if the module exports one
        module = importlib.import_module("api.core.request_context")
        getter = getattr(module, "get_request_id", None)
        if callable(getter):
            resolved = getter()
            if isinstance(resolved, str) and resolved and resolved != "-":
                return resolved
    except ImportError:
        pass
    return None


def record_security_event(
    *,
    actor_type: str,
    actor_id: str | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    request_id: str | None = None,
    result: str = _RESULT_ALLOWED,
    reason: str | None = None,
    audit_metadata: dict | None = None,
    db: Session | None = None,
) -> None:
    """Append one security-audit row. Best-effort by contract: when `db` is
    None a short-lived session is created; the commit is immediate. This
    function NEVER raises into the caller — an audit failure is logged, the
    caller's operation proceeds either way."""
    if db is None:
        from api.db.database import SessionLocal  # lazy: no import cycle
        db = SessionLocal()
        owned = True
    else:
        owned = False
    try:
        db.add(
            SecurityAuditRecord(
                actor_type=actor_type,
                actor_id=actor_id or "",
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=request_id or current_request_id(),
                result=result,
                reason=(reason[:255] if reason else None),
                audit_metadata=audit_metadata,
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001 — audit must never break the caller
        logger.warning("security audit write failed for action=%s", action,
                       exc_info=True)
        if owned:
            db.rollback()
    finally:
        if owned:
            db.close()


def record_auth_failure(
    *, reason: str, request_id: str | None = None,
    audit_metadata: dict | None = None,
) -> None:
    """An unauthenticated call (missing or invalid API key). Convenience
    wrapper for the security dependency."""
    record_security_event(
        actor_type="ANONYMOUS",
        actor_id=None,
        action=AUDIT_AUTH_FAILURE,
        result=_RESULT_DENIED,
        reason=reason,
        request_id=request_id,
        audit_metadata=audit_metadata,
    )


def record_forbidden(
    auth, request, resource_type: str | None, resource_id: str | None,
    reason: str,
) -> None:
    """An authenticated caller whose role is not allowed. Convenience
    wrapper for require_roles(); `auth` is an AuthContext, `request` the
    Starlette request (used only for correlation hints)."""
    record_security_event(
        actor_type=auth.role,
        actor_id=auth.key_name,
        action=AUDIT_FORBIDDEN,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=getattr(getattr(request, "state", None), "request_id", None),
        result=_RESULT_DENIED,
        reason=reason,
    )

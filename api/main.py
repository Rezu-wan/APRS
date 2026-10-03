"""
api/main.py — FastAPI application entrypoint.

Run from the project root:
    uvicorn api.main:app --reload

Lifespan: ML artifacts are loaded ONCE at startup (no retraining, no data
generation). Config, secrets, and policy thresholds all come from the
environment via api/core/config.py. Nothing AI-related is loaded at startup —
the GenAI provider factory resolves at request time.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from api import middleware as api_middleware
from api.core.config import DEV_KEY_WARNING, get_settings
from api.core.exceptions import AppError
from api.core.request_context import RequestIdLogFilter, get_request_id
from api.core.security import ROLES
from api.db.database import engine
from api.routes import (
    auth,
    autonomous_recovery,
    behavioral,
    chaos,
    customer_reports,
    customers,
    demo,
    explanations,
    metrics,
    payment_events,
    policy_simulator,
    recovery,
    reconstruction,
    relationship,
    risk_assessment,
    sandbox,
    stats,
    support,
    temporal,
    transactions,
)
from api.services.ml_service import get_ml_service

logging.basicConfig(
    level=get_settings().log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s [request_id=%(request_id)s] %(message)s",
)
# every log record carries the current request id ("-" outside a request)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(RequestIdLogFilter())
logger = logging.getLogger("payment_recovery.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.uses_dev_api_keys:
        logger.warning(DEV_KEY_WARNING)
    if settings.uses_dev_api_keys and settings.environment == "production":
        raise RuntimeError("refusing to start in production with dev API keys")
    get_ml_service().load(settings)
    from api.services.payment_provider import restore_sandbox_ledger  # Stage 9

    restore_sandbox_ledger()

    # Stage 11A: event bus — a DEBUG logging consumer proves the wiring
    # without spamming demo logs; closed on shutdown.
    from api.services.eventbus import EventEnvelope, get_event_bus

    bus = get_event_bus()

    def _log_consumer(envelope: EventEnvelope) -> None:
        logger.debug(
            "event %s tx=%s id=%s",
            envelope.event_type,
            envelope.transaction_id,
            envelope.event_id,
        )

    bus.subscribe(_log_consumer, name="lifespan-logger")

    logger.info("startup complete (env=%s, roles=%s)", settings.environment, list(ROLES))
    yield
    bus.close()
    logger.info("shutdown")


app = FastAPI(
    title="Payment Failure Recovery & Digital Twin API",
    description=(
        "Stage 3 backend: transaction state machine, ML risk assessment "
        "(Stage 2 XGBoost engine), policy-driven recovery decisions, and an "
        "append-only Digital Twin event log.\n\n"
        "Stage 4 adds a GenAI explanation layer: schema-controlled, cached "
        "explanations of already-made decisions (GenAI explains; it never "
        "decides).\n\n"
        "Authenticate with the `X-API-Key` header (roles: SYSTEM, ADMIN, "
        "SUPPORT, CUSTOMER — see .env.example).\n\n"
        "Stage 5 adds frontend-support endpoints: identity bootstrap "
        "(/auth/me) and honest dashboard aggregates (/stats/summary)."
    ),
    version="0.5.0",
    lifespan=lifespan,
)

app.include_router(transactions.router)
app.include_router(transactions.ingest_router)
app.include_router(customer_reports.router)
app.include_router(customers.router)
app.include_router(recovery.router)
app.include_router(explanations.router)
app.include_router(auth.router)
app.include_router(stats.router)
app.include_router(payment_events.router)
app.include_router(reconstruction.router)
app.include_router(risk_assessment.router)
app.include_router(autonomous_recovery.router)
app.include_router(sandbox.sandbox_router)
app.include_router(sandbox.audit_router)
app.include_router(demo.router)
app.include_router(behavioral.router)
app.include_router(relationship.router)
app.include_router(metrics.router)
app.include_router(temporal.router)
app.include_router(policy_simulator.router)
app.include_router(chaos.router)
app.include_router(support.router)

if get_settings().cors_origin_list:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Stage 9: request-id propagation, security headers, per-key rate limiting
app.middleware("http")(api_middleware.request_middleware)


# --------------------------------------------------------------------------
# Error handling: safe bodies for clients, full detail in server logs only
# --------------------------------------------------------------------------
@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    logger.error("database integrity error on %s", request.url.path)
    return JSONResponse(
        status_code=409,
        content={
            "error": {
                "code": "CONFLICT",
                "message": "request conflicts with stored data",
                "request_id": get_request_id(),
            }
        },
    )


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    logger.warning(
        "application error on %s: %s (%s)", request.url.path, exc.message, exc.code
    )
    # Stage 9: permission denials land in the security audit trail (best-effort —
    # never breaks the error response; AUTH_FAILURE rows are written by the
    # auth dependency itself, so only FORBIDDEN is mirrored here). The actor is
    # identified by KEY NAME only — the raw key never enters logs or audit.
    if exc.code == "INSUFFICIENT_PERMISSIONS":
        try:
            from api.core.security import key_name_from_request
            from api.services.audit import AUDIT_FORBIDDEN, record_security_event

            record_security_event(
                actor_type="ANONYMOUS",
                actor_id=key_name_from_request(request) or "unknown",
                action=AUDIT_FORBIDDEN,
                resource_type="endpoint",
                resource_id=request.url.path,
                request_id=get_request_id(),
                result="DENIED",
                reason=exc.message[:255],
            )
        except Exception:  # noqa: BLE001 — audit is best-effort by contract
            logger.debug("security audit write failed (ignored)", exc_info=True)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "request_id": get_request_id(),
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    # sanitize: pydantic error dicts echo raw input values — never return them
    details = [
        {"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")}
        for e in exc.errors()[:10]
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "invalid request",
                "details": details,
                "request_id": get_request_id(),
            }
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": f"HTTP_{exc.status_code}",
                "message": str(exc.detail),
                "request_id": get_request_id(),
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.exception("unhandled server error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "unexpected server error",
                "request_id": get_request_id(),
            }
        },
    )


@app.get("/health", tags=["system"], response_model=None)
def health():
    """Liveness + readiness: DB connectivity and ML artifact status."""
    settings = get_settings()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as exc:  # noqa: BLE001 — health must never leak driver errors
        logger.error("health check database failure")
        db_status = "unavailable"
    ml_status = "loaded" if get_ml_service().loaded else "not_loaded"
    healthy = db_status == "connected" and ml_status == "loaded"
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "healthy" if healthy else "degraded",
            "database": db_status,
            "ml_models": ml_status,
            "environment": settings.environment,
        },
    )

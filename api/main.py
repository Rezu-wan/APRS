"""
api/main.py — FastAPI application entrypoint.

Run from the project root:
    uvicorn api.main:app --reload

Lifespan: ML artifacts are loaded ONCE at startup (no retraining, no data
generation). Config, secrets, and policy thresholds all come from the
environment via api/core/config.py.
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

from api.core.config import DEV_KEY_WARNING, get_settings
from api.core.exceptions import AppError
from api.core.security import ROLES
from api.db.database import engine
from api.routes import recovery, transactions
from api.services.ml_service import get_ml_service

logging.basicConfig(
    level=get_settings().log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("payment_recovery.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.uses_dev_api_keys:
        logger.warning(DEV_KEY_WARNING)
    if settings.uses_dev_api_keys and settings.environment == "production":
        raise RuntimeError("refusing to start in production with dev API keys")
    get_ml_service().load(settings)
    logger.info("startup complete (env=%s, roles=%s)", settings.environment, list(ROLES))
    yield
    logger.info("shutdown")


app = FastAPI(
    title="Payment Failure Recovery & Digital Twin API",
    description=(
        "Stage 3 backend: transaction state machine, ML risk assessment "
        "(Stage 2 XGBoost engine), policy-driven recovery decisions, and an "
        "append-only Digital Twin event log.\n\n"
        "Authenticate with the `X-API-Key` header (roles: SYSTEM, ADMIN, "
        "SUPPORT, CUSTOMER — see .env.example)."
    ),
    version="0.3.0",
    lifespan=lifespan,
)

app.include_router(transactions.router)
app.include_router(transactions.ingest_router)
app.include_router(recovery.router)

if get_settings().cors_origin_list:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


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
            }
        },
    )


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    logger.warning(
        "application error on %s: %s (%s)", request.url.path, exc.message, exc.code
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
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
        content={"error": {"code": "VALIDATION_ERROR", "message": "invalid request", "details": details}},
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": f"HTTP_{exc.status_code}", "message": str(exc.detail)}},
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

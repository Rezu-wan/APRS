"""pytest configuration — MUST run before any `api` import.

api.core.config.get_settings is lru_cache'd and api.db.database creates the
engine at import time, so the test environment must be pinned via os.environ
at the very top of this module, before anything from `api` is imported.
"""

from __future__ import annotations

import os
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]

os.environ["DATABASE_URL"] = "sqlite:///./data/test.db"
os.environ["ENVIRONMENT"] = "test"

# Pin the API keys too: pydantic-settings gives a real .env file precedence
# over the config class defaults, so a deployment .env with production keys
# would otherwise make every authenticated test 401.
os.environ["API_KEY_SYSTEM"] = "dev-system-key"
os.environ["API_KEY_ADMIN"] = "dev-admin-key"
os.environ["API_KEY_SUPPORT"] = "dev-support-key"
os.environ["API_KEY_CUSTOMER"] = "dev-customer-key"
os.environ["CUSTOMER_API_KEYS"] = "dev-customer-alice:alice,dev-customer-bob:bob"

# remove a stale test database so every run starts from a clean schema
_test_db = PROJECT_ROOT / "data" / "test.db"
if _test_db.exists():
    _test_db.unlink()

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.db.database import Base, engine  # noqa: E402
from api.main import app  # noqa: E402

# Dev API keys (defaults from api/core/config.py)
SYSTEM_KEY = {"X-API-Key": "dev-system-key"}
ADMIN_KEY = {"X-API-Key": "dev-admin-key"}
SUPPORT_KEY = {"X-API-Key": "dev-support-key"}
CUSTOMER_KEY = {"X-API-Key": "dev-customer-key"}

# Known-good LIMIT_RELEASED fixture (verified against the real models:
# p(safe_to_release) ~= 0.977 >= 0.90 policy threshold)
CLEAN_FAILED_TX = {
    "amount": 30.00,
    "gateway_latency_ms": 45,
    "retry_count": 0,
    "network_quality": "Excellent",
    "previous_failures": 0,
    "account_age_days": 2000,
    "status": "FAILED",
    "failure_reason": "Gateway Error",
}


def make_event(transaction_id: str, **overrides) -> dict:
    payload = {
        "transaction_id": transaction_id,
        "user_id": "USER-001",
        "merchant_id": "MERCHANT-001",
        "currency": "BDT",
    }
    payload.update(overrides)
    return payload


@pytest.fixture(scope="session")
def schema():
    """Create the full schema once for the whole session."""
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(scope="session")
def client(schema):
    """Session-scoped TestClient used as a context manager so the FastAPI
    lifespan (the one-time ML model load) runs exactly once."""
    with TestClient(app) as c:
        yield c

"""Stage 9: rate limiting.

Two layers:
  * Unit tests exercise SlidingWindowLimiter + bucket selection directly
    (no app state, no env juggling).
  * Integration tests flip the settings cache via get_settings.cache_clear
    + env vars (mirroring tests/conftest.py's env pinning) and restore
    afterwards, so the 203 baseline tests never see a limiter.
"""

from __future__ import annotations

import pytest

from api.core.config import get_settings
from api.middleware import (
    RATE_LIMITS,
    DEFAULT_LIMIT,
    SlidingWindowLimiter,
    bucket_for_path,
    bucket_key,
)


class TestLimiterUnit:
    def test_burst_under_limit_passes(self):
        limiter = SlidingWindowLimiter()
        for _ in range(3):
            assert limiter.check("k:default", 3) is True

    def test_burst_over_limit_is_rejected(self):
        limiter = SlidingWindowLimiter()
        for _ in range(5):
            assert limiter.check("k:default", 5) is True
        assert limiter.check("k:default", 5) is False
        assert limiter.check("k:default", 5) is False  # stays rejected

    def test_window_expiry_restores_capacity(self):
        limiter = SlidingWindowLimiter(window_seconds=60.0)
        t0 = 1000.0
        for _ in range(2):
            assert limiter.check("k:explanations", 2, now=t0) is True
        assert limiter.check("k:explanations", 2, now=t0 + 1) is False
        # a full window later the old hits have expired
        assert limiter.check("k:explanations", 2, now=t0 + 61) is True

    def test_keys_have_independent_buckets(self):
        limiter = SlidingWindowLimiter()
        for _ in range(3):
            assert limiter.check("alice:default", 3) is True
        assert limiter.check("alice:default", 3) is False
        # a different key is unaffected by alice's exhaustion
        assert limiter.check("bob:default", 3) is True

    def test_buckets_are_independent_per_key(self):
        limiter = SlidingWindowLimiter()
        for _ in range(2):
            assert limiter.check("alice:explanations", 2) is True
        assert limiter.check("alice:explanations", 2) is False
        assert limiter.check("alice:recovery", 2) is True


class TestBucketSelection:
    def test_documented_buckets_and_limits(self):
        expected = {
            "explanations": 30,
            "recovery": 20,
            "risk": 30,
            "payment-events": 60,
            "auth": 60,
            "demo": 60,  # Stage 10: demo-control endpoints
            "support": 240,  # Stage 12: support workspace (queue+search+SSE)
        }
        limits = {name: limit for name, _, limit in RATE_LIMITS}
        assert limits == expected
        assert DEFAULT_LIMIT == 120

    def test_path_fragments(self):
        assert bucket_for_path("/api/v1/explanations/transaction")[0] == "explanations"
        assert (
            bucket_for_path("/api/v1/transactions/T1/recovery/process")[0] == "recovery"
        )
        assert (
            bucket_for_path("/api/v1/transactions/T1/recovery/evaluate")[0] == "recovery"
        )
        assert bucket_for_path("/api/v1/recovery/release-limit")[0] == "recovery"
        assert bucket_for_path("/api/v1/transactions/T1/risk-assessment")[0] == "risk"
        assert bucket_for_path("/api/v1/transactions/T1/payment-events")[0] == (
            "payment-events"
        )
        assert bucket_for_path("/api/v1/auth/me")[0] == "auth"
        assert bucket_for_path("/api/v1/transactions/T1")[0] == "default"

    def test_bucket_key_uses_key_name_not_raw_secret(self):
        assert bucket_key("customer:alice", "explanations") == (
            "customer:alice:explanations"
        )
        # the raw key value must never be a bucket label
        assert "dev-customer-alice" != bucket_key("customer:alice", "explanations")


@pytest.fixture()
def rate_limit_env(monkeypatch):
    """Flip settings to rate-limiting-on with a NON-test environment, then
    restore (get_settings is lru_cache'd — same pattern conftest uses)."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    get_settings.cache_clear()
    yield
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
    get_settings.cache_clear()


class TestRateLimitingIntegration:
    def test_exceeded_limit_returns_429(self, rate_limit_env, client):
        # exhaust the tightest bucket: recovery (20/min) via anonymous hits
        # against release-limit (a 401 response still counts as a hit)
        url = "/api/v1/recovery/release-limit"
        statuses = [
            client.post(url, json={"transaction_id": "TXN-RL"}).status_code
            for _ in range(25)
        ]
        assert 429 in statuses
        idx = statuses.index(429)
        body = client.post(url, json={"transaction_id": "TXN-RL"}).json()
        assert body["error"]["code"] == "RATE_LIMITED"
        assert body["error"]["request_id"]
        assert statuses[idx:] == [429] * len(statuses[idx:])
        resp = client.post(url, json={"transaction_id": "TXN-RL"})
        assert resp.headers.get("Retry-After") is not None

    def test_different_keys_have_independent_integration_buckets(
        self, rate_limit_env, client
    ):
        # anonymous identity exhausts the auth bucket (60/min)...
        for _ in range(60):
            client.get("/api/v1/auth/me")
        assert client.get("/api/v1/auth/me").status_code == 429
        # ...but an authenticated key has its own bucket
        resp = client.get("/api/v1/auth/me", headers={"X-API-Key": "dev-system-key"})
        assert resp.status_code == 200

    def test_disabled_limiter_never_429s(self, monkeypatch, client):
        # conftest pins ENVIRONMENT=test which already disables the limiter;
        # assert that explicitly so the 203 baseline can never see 429
        assert get_settings().environment == "test"
        for _ in range(5):
            resp = client.get("/api/v1/auth/me")
            assert resp.status_code in (200, 401)

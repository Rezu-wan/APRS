"""Stage 11G — in-process metrics registry, path classes, /api/v1/metrics."""

from __future__ import annotations

import threading

import pytest

from api.services.metrics import (
    METRICS_RECOVERY_ATTEMPTS_TOTAL,
    get_metrics,
    path_class,
    reset_metrics,
)

from tests.conftest import ADMIN_KEY, CUSTOMER_KEY, SUPPORT_KEY


@pytest.fixture(autouse=True)
def clean_registry():
    reset_metrics()
    yield
    reset_metrics()


# ---------------------------------------------------------------------------
# registry math
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_counter_accumulates_floats(self):
        reg = get_metrics()
        reg.record_counter("c")
        reg.record_counter("c", 2.5)
        reg.record_counter("c", 0.5)
        assert reg.snapshot()["counters"]["c"] == pytest.approx(4.0)

    def test_counter_default_value_is_one(self):
        reg = get_metrics()
        reg.record_counter("hits")
        assert reg.snapshot()["counters"]["hits"] == 1.0

    def test_gauge_is_last_value(self):
        reg = get_metrics()
        reg.set_gauge("depth", 3)
        reg.set_gauge("depth", 7.5)
        assert reg.snapshot()["gauges"]["depth"] == 7.5

    def test_latency_math(self):
        reg = get_metrics()
        reg.record_latency("op", 10.0)
        reg.record_latency("op", 30.0)
        reg.record_latency("op", 20.0)
        entry = reg.snapshot()["latencies_ms"]["op"]
        assert entry["count"] == 3
        assert entry["sum_ms"] == pytest.approx(60.0)
        assert entry["avg_ms"] == pytest.approx(20.0)
        assert entry["min_ms"] == pytest.approx(10.0)
        assert entry["max_ms"] == pytest.approx(30.0)

    def test_single_latency_sample(self):
        reg = get_metrics()
        reg.record_latency("op", 5.0)
        entry = reg.snapshot()["latencies_ms"]["op"]
        assert entry["count"] == 1
        assert entry["avg_ms"] == pytest.approx(5.0)
        assert entry["min_ms"] == entry["max_ms"] == pytest.approx(5.0)

    def test_snapshot_shape(self):
        snap = get_metrics().snapshot()
        assert set(snap) == {"counters", "gauges", "latencies_ms", "generated_at"}
        assert snap["counters"] == {} and snap["gauges"] == {}
        assert snap["latencies_ms"] == {}
        assert snap["generated_at"]  # ISO string present

    def test_reset_clears_everything(self):
        reg = get_metrics()
        reg.record_counter(METRICS_RECOVERY_ATTEMPTS_TOTAL)
        reg.set_gauge("g", 1)
        reg.record_latency("l", 1)
        reset_metrics()
        snap = reg.snapshot()
        assert snap["counters"] == {} and snap["gauges"] == {}
        assert snap["latencies_ms"] == {}

    def test_thread_safety_exact_total(self):
        """8 threads x 1000 increments must land on exactly 8000."""
        reg = get_metrics()
        n_threads, n_incr = 8, 1000

        def worker():
            for _ in range(n_incr):
                reg.record_counter("contended")

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert reg.snapshot()["counters"]["contended"] == float(n_threads * n_incr)


# ---------------------------------------------------------------------------
# path_class — table-driven, bounded class set
# ---------------------------------------------------------------------------


class TestPathClass:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/health", "health"),
            ("/api/v1/auth/me", "auth"),
            ("/api/v1/auth/login", "auth"),
            ("/api/v1/transactions", "transactions_list"),
            ("/api/v1/transaction/event", "transaction_create"),
            ("/api/v1/transactions/TXN-123", "transaction_detail"),
            (
                "/api/v1/transactions/TXN-123/recovery/process",
                "transaction_recovery_process",
            ),
            (
                "/api/v1/transactions/TXN-123/payment-events",
                "transaction_payment_events",
            ),
            (
                "/api/v1/transactions/TXN-123/reconstruction",
                "transaction_reconstruction",
            ),
            ("/api/v1/demo/scenarios", "demo_scenarios"),
            (
                "/api/v1/demo/scenarios/S1/prepare",
                "demo_scenarios_S1_prepare",
            ),
            ("/api/v1/demo/reset", "demo_reset"),
            ("/api/v1/stats/summary", "stats_summary"),
            ("/api/v1/metrics", "metrics"),
            ("/api/v1/unknown/thing", "other"),
            ("", "other"),
            ("/", "other"),
        ],
    )
    def test_table(self, path, expected):
        assert path_class(path) == expected

    def test_ids_never_leak_into_class(self):
        for path in (
            "/api/v1/transactions/TXN-999",
            "/api/v1/transactions/user-42",
        ):
            assert path_class(path) == "transaction_detail"


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    def test_admin_200_shape(self, client):
        reset_metrics()
        resp = client.get("/api/v1/metrics", headers=ADMIN_KEY)
        assert resp.status_code == 200
        body = resp.json()
        assert body["uptime_s"] > 0
        assert isinstance(body["counters"], dict)
        assert isinstance(body["gauges"], dict)
        assert isinstance(body["latencies_ms"], dict)
        assert body["generated_at"]

    def test_support_and_customer_forbidden(self, client):
        for key in (SUPPORT_KEY, CUSTOMER_KEY):
            resp = client.get("/api/v1/metrics", headers=key)
            assert resp.status_code == 403

    def test_http_request_records_counter_and_latency(self, client):
        reset_metrics()
        assert client.get("/api/v1/metrics", headers=ADMIN_KEY).status_code == 200
        snap = get_metrics().snapshot()
        assert snap["counters"].get("http_metrics_requests_total", 0) >= 1
        assert "http_metrics" in snap["latencies_ms"]
        latency = snap["latencies_ms"]["http_metrics"]
        assert latency["count"] >= 1 and latency["min_ms"] >= 0

    def test_no_sensitive_data_in_output(self, client):
        # registry accepts any name (no ceremony) — the endpoint output must
        # still expose only the registry structure + uptime, never ids/amounts
        get_metrics().record_counter("TXN-123_total", 1.0)
        resp = client.get("/api/v1/metrics", headers=ADMIN_KEY)
        assert resp.status_code == 200
        body = resp.json()

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    assert k not in ("user_id", "amount"), f"leaked key: {k}"
                    walk(v)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(body)
        assert set(body) == {
            "uptime_s",
            "counters",
            "gauges",
            "latencies_ms",
            "generated_at",
        }

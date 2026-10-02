"""
tests/test_anomaly_ml.py — Stage 7 anomaly-scenario ML contract tests.

Covers the pinned ml/predict_anomaly.py contract: artifact loading (graceful
None when missing), prediction schema and ranges, build_features shape on
fake event objects, the leakage ban on the 18 pinned features, and
determinism of repeated predictions.
"""

from __future__ import annotations

import datetime as dt

import pytest

from ml.predict_anomaly import (
    ANOMALY_MODEL_VERSION,
    FEATURE_COLUMNS,
    build_features,
    load_anomaly_models,
    predict_anomaly,
)

FORBIDDEN_FEATURES = {
    "safe_to_release",
    "risk_score",
    "recovery_risk",
    "decision",
    "anomaly",
    "scenario",
}


class _FakeTx:
    amount = 250.0
    gateway_latency_ms = 3200.0
    retry_count = 2
    network_quality = "Poor"
    previous_failures = 1
    account_age_days = 180


class _FakeEvent:
    def __init__(self, event_type, provider_event_id, timestamp):
        self.event_type = event_type
        self.provider_event_id = provider_event_id
        self.event_timestamp = timestamp
        self.latency_ms = 100.0


class _FakeReconstruction:
    confidence = 0.87


def _fake_events():
    t0 = dt.datetime(2026, 10, 2, 12, 0, 0)
    return [
        _FakeEvent("PAYMENT_INITIATED", "pe-1", t0),
        _FakeEvent("CUSTOMER_DEBIT_CONFIRMED", "pe-2", t0 + dt.timedelta(milliseconds=800)),
        _FakeEvent("CUSTOMER_DEBIT_CONFIRMED", "pe-2", t0 + dt.timedelta(milliseconds=900)),  # dup
        _FakeEvent("GATEWAY_TIMEOUT", "pe-3", t0 + dt.timedelta(milliseconds=2500)),
    ]


@pytest.fixture(scope="module")
def models():
    return load_anomaly_models()


def test_model_loads(models):
    """Trained artifacts exist in models/ and load with the pinned API."""
    assert models is not None, (
        "anomaly artifacts missing — run: python -m ml.train_anomaly"
    )
    assert "classifier" in models and "meta" in models
    assert models["meta"]["model_version"] == ANOMALY_MODEL_VERSION


def test_missing_artifacts_return_none(tmp_path):
    """load_anomaly_models NEVER raises on missing artifacts — returns None."""
    assert load_anomaly_models(models_dir=str(tmp_path)) is None


def test_prediction_schema(models):
    features = build_features(_FakeTx(), _fake_events(), _FakeReconstruction())
    result = predict_anomaly(features, models=models)

    assert set(result) == {"predicted_scenario", "ml_anomaly_score", "class_probabilities"}
    assert isinstance(result["predicted_scenario"], str) and result["predicted_scenario"]

    score = result["ml_anomaly_score"]
    assert isinstance(score, float) and 0.0 <= score <= 1.0

    probs = result["class_probabilities"]
    assert len(probs) == 13
    for name, p in probs.items():
        assert isinstance(name, str) and 0.0 <= p <= 1.0
    # ml_anomaly_score == 1 - P(normal_success), rounded to 4 dp
    assert score == pytest.approx(round(1.0 - probs.get("normal_success", 0.0), 4), abs=1e-9)
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-3)  # rounded, so loose


def test_predict_none_models_returns_none():
    features = build_features(_FakeTx(), _fake_events(), None)
    assert predict_anomaly(features, models=None) is None


def test_feature_pipeline():
    """build_features returns exactly the 18 pinned keys with derived values."""
    features = build_features(_FakeTx(), _fake_events(), _FakeReconstruction())

    assert set(features.keys()) == set(FEATURE_COLUMNS)
    assert len(features) == 18

    assert features["event_count"] == 4
    assert features["debit_confirmation_count"] == 2
    assert features["duplicate_event_count"] == 1  # pe-2 seen twice -> 1 extra
    assert features["has_gateway_timeout"] == 1.0
    assert features["has_gateway_error"] == 0.0
    assert features["has_merchant_confirmation"] == 0.0
    assert features["reconstruction_confidence"] == pytest.approx(0.87)
    assert features["time_to_last_event_ms"] == pytest.approx(2500.0)
    assert features["amount"] == pytest.approx(250.0)
    assert features["network_quality"] == "Poor"

    # None-safe reconstruction -> 0.0
    none_rec = build_features(_FakeTx(), _fake_events(), None)
    assert none_rec["reconstruction_confidence"] == 0.0

    # Single event -> time_to_last_event_ms == 0
    single = build_features(_FakeTx(), _fake_events()[:1], None)
    assert single["time_to_last_event_ms"] == 0.0


def test_no_target_leakage():
    """The 18 pinned features contain no target/decision leakage columns."""
    lowered = {c.lower() for c in FEATURE_COLUMNS}
    assert not (lowered & FORBIDDEN_FEATURES), (
        f"leaked columns found: {lowered & FORBIDDEN_FEATURES}"
    )
    # and the ban holds even as substrings (e.g. 'risk_score' hidden in a name)
    for col in FEATURE_COLUMNS:
        for banned in FORBIDDEN_FEATURES:
            assert banned not in col.lower()


def test_prediction_determinism(models):
    features = build_features(_FakeTx(), _fake_events(), _FakeReconstruction())
    r1 = predict_anomaly(features, models=models)
    r2 = predict_anomaly(features, models=models)
    assert r1["predicted_scenario"] == r2["predicted_scenario"]
    assert r1["ml_anomaly_score"] == r2["ml_anomaly_score"]
    assert r1["class_probabilities"] == r2["class_probabilities"]

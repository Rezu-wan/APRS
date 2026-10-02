"""
ml/predict_anomaly.py — Stage 7 anomaly-scenario inference (pinned contract).

The hybrid risk engine (parallel agent) consumes P(not normal) from this
module as a SUPPORTING signal only:

    from ml.predict_anomaly import (
        ANOMALY_MODEL_VERSION, FEATURE_COLUMNS,
        load_anomaly_models, predict_anomaly, build_features,
    )

Contract (pinned — do not rename):
  ANOMALY_MODEL_VERSION = "synthetic-v1"
  FEATURE_COLUMNS       = the 18 pinned features, exact order
  load_anomaly_models(models_dir=None) -> dict | None   (None if artifacts
                          missing — never raises on missing artifacts)
  predict_anomaly(features: dict, models: dict | None = None)
        -> {"predicted_scenario": str,
            "ml_anomaly_score": float,      # round(1 - P(normal_success), 4)
            "class_probabilities": dict}    # or None if models is None
  build_features(tx, events, reconstruction) -> dict  (the 18 features)

LEAKAGE RULES: the 18 features are facts observable at assessment time only —
no safe_to_release, no risk_score/recovery_risk, no recovery-decision fields,
no anomaly label.
"""

from __future__ import annotations

import os
from typing import Any

import joblib
import pandas as pd

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")

ANOMALY_MODEL_VERSION = "synthetic-v1"

NORMAL_LABEL = "normal_success"

FEATURE_COLUMNS = [
    "amount",
    "gateway_latency_ms",
    "retry_count",
    "network_quality",
    "previous_failures",
    "account_age_days",
    "event_count",
    "debit_confirmation_count",
    "duplicate_event_count",
    "has_gateway_timeout",
    "has_gateway_error",
    "has_merchant_timeout",
    "has_merchant_error",
    "has_settlement_failure",
    "has_settlement_confirmation",
    "has_merchant_confirmation",
    "reconstruction_confidence",
    "time_to_last_event_ms",
]

_CLASSIFIER_FILE = "anomaly_classifier.joblib"
_META_FILE = "anomaly_preprocessors.joblib"


def load_anomaly_models(models_dir: str | None = None) -> dict | None:
    """Load the anomaly pipeline + meta artifact. Returns None (never raises)
    when artifacts are missing so callers can degrade gracefully."""
    models_dir = models_dir or MODELS_DIR
    classifier_path = os.path.join(models_dir, _CLASSIFIER_FILE)
    meta_path = os.path.join(models_dir, _META_FILE)
    if not (os.path.exists(classifier_path) and os.path.exists(meta_path)):
        return None
    try:
        return {
            "classifier": joblib.load(classifier_path),
            "meta": joblib.load(meta_path),
        }
    except Exception:
        return None


def build_features(tx, events, reconstruction) -> dict:
    """Derive the 18 pinned features from a Transaction-like object, a list of
    PaymentEvent-like objects, and a ReconstructionResult-like object.

    tx            : object with amount, gateway_latency_ms, retry_count,
                    network_quality, previous_failures, account_age_days
    events        : objects with event_type and event_timestamp (latency_ms
                    read if present); provider_event_id used for duplicate
                    counting when present
    reconstruction: object with confidence, or None (use 0.0)
    """
    events = list(events or [])

    event_count = len(events)
    debit_confirmation_count = sum(
        1 for e in events if getattr(e, "event_type", None) == "CUSTOMER_DEBIT_CONFIRMED"
    )

    # Duplicates: provider_event_id values seen more than once
    ids = [getattr(e, "provider_event_id", None) for e in events]
    seen: dict[Any, int] = {}
    for i in ids:
        if i is not None:
            seen[i] = seen.get(i, 0) + 1
    duplicate_event_count = sum(c - 1 for c in seen.values() if c > 1)

    event_types = {getattr(e, "event_type", None) for e in events}

    def flag(event_type: str) -> float:
        return 1.0 if event_type in event_types else 0.0

    if event_count >= 2:
        first = getattr(events[0], "event_timestamp", None)
        last = getattr(events[-1], "event_timestamp", None)
        try:
            time_to_last_event_ms = max(0.0, (last - first).total_seconds() * 1000.0)
        except (TypeError, AttributeError):
            time_to_last_event_ms = 0.0
    else:
        time_to_last_event_ms = 0.0

    return {
        "amount": float(getattr(tx, "amount", 0.0) or 0.0),
        "gateway_latency_ms": float(getattr(tx, "gateway_latency_ms", 0.0) or 0.0),
        "retry_count": float(getattr(tx, "retry_count", 0) or 0),
        "network_quality": getattr(tx, "network_quality", None),
        "previous_failures": float(getattr(tx, "previous_failures", 0) or 0),
        "account_age_days": float(getattr(tx, "account_age_days", 0) or 0),
        "event_count": float(event_count),
        "debit_confirmation_count": float(debit_confirmation_count),
        "duplicate_event_count": float(duplicate_event_count),
        "has_gateway_timeout": flag("GATEWAY_TIMEOUT"),
        "has_gateway_error": flag("GATEWAY_ERROR"),
        "has_merchant_timeout": flag("MERCHANT_TIMEOUT"),
        "has_merchant_error": flag("MERCHANT_ERROR"),
        "has_settlement_failure": flag("SETTLEMENT_FAILURE"),
        "has_settlement_confirmation": flag("SETTLEMENT_CONFIRMED"),
        "has_merchant_confirmation": flag("MERCHANT_CONFIRMED"),
        "reconstruction_confidence": float(getattr(reconstruction, "confidence", 0.0) or 0.0)
        if reconstruction is not None
        else 0.0,
        "time_to_last_event_ms": float(time_to_last_event_ms),
    }


def predict_anomaly(features: dict, models: dict | None = None) -> dict | None:
    """Score ONE feature dict. Returns None if models is None (caller should
    treat the anomaly signal as unavailable, not as an error)."""
    if models is None:
        return None

    classifier = models["classifier"]
    meta = models.get("meta") or {}
    scenario_classes = meta.get("scenario_classes")

    row = {c: features.get(c) for c in FEATURE_COLUMNS}
    proba = classifier.predict_proba(pd.DataFrame([row]))[0]

    if scenario_classes is not None:
        class_names = [str(scenario_classes[int(c)]) for c in classifier.classes_]
    else:
        class_names = [str(c) for c in classifier.classes_]

    class_probabilities = {
        name: round(float(p), 4) for name, p in zip(class_names, proba)
    }
    p_normal = class_probabilities.get(NORMAL_LABEL, 0.0)
    ranked = sorted(class_probabilities.items(), key=lambda kv: kv[1], reverse=True)

    return {
        "predicted_scenario": ranked[0][0],
        "ml_anomaly_score": round(1.0 - p_normal, 4),
        "class_probabilities": dict(sorted(class_probabilities.items())),
    }

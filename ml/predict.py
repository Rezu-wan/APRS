"""
ml/predict.py — inference layer for Stage 3 (FastAPI) and demos.

Load the saved artifacts once, then score single transactions (dicts) or batches:

    from ml.predict import load_models, predict_transaction

    models = load_models()                      # at app startup
    result = predict_transaction(
        {
            "amount": 1250.0,
            "gateway_latency_ms": 2800,
            "retry_count": 3,
            "network_quality": "Poor",
            "previous_failures": 2,
            "account_age_days": 40,
            "status": "STALLED",
            "failure_reason": None,
        },
        models=models,
    )
    # -> {"failure_prediction": ..., "recovery_risk": 0.87, "safe_to_release": false, ...}

Extra keys in the transaction dict (transaction_id, timestamp, ...) are ignored;
missing optional keys become NaN and are handled by the pipeline imputers.
The three artifacts in models/ are full sklearn Pipelines, so inference applies
exactly the transformations fitted at training time — no re-transformation code
duplicates anywhere.

CLI demo:
    python -m ml.predict
"""

from __future__ import annotations

import json
import os
from typing import Any

import joblib
import numpy as np
import pandas as pd

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")

ARTIFACTS = {
    "failure": "failure_classifier.joblib",
    "recovery": "recovery_classifier.joblib",
    "risk": "risk_regressor.joblib",
}


def load_models(models_dir: str = MODELS_DIR) -> dict[str, Any]:
    """Load all saved pipelines + preprocessing metadata. Call once at startup."""
    models: dict[str, Any] = {}
    for name, fname in ARTIFACTS.items():
        path = os.path.join(models_dir, fname)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{path} not found — train first with: python -m ml.train"
            )
        models[name] = joblib.load(path)
    meta_path = os.path.join(models_dir, "preprocessors.joblib")
    models["meta"] = joblib.load(meta_path) if os.path.exists(meta_path) else None
    return models


def _frame(transaction: dict, columns: list[str]) -> pd.DataFrame:
    row = {c: transaction.get(c, np.nan) for c in columns}
    return pd.DataFrame([row])


def predict_transaction(transaction: dict, models: dict[str, Any] | None = None) -> dict:
    """Score ONE transaction. `models` may be omitted (loads on each call —
    fine for demos; FastAPI should load once and pass it in)."""
    if models is None:
        models = load_models()

    failure_pipe = models["failure"]
    recovery_pipe = models["recovery"]
    risk_pipe = models["risk"]

    # Build input frames with each pipeline's exact training feature set.
    X_failure = _frame(transaction, list(failure_pipe.feature_names_in_))
    X_binary = _frame(transaction, list(recovery_pipe.feature_names_in_))

    # failure model was trained on integer codes; map them back to labels
    # (order == OUTCOME_CLASSES, also stored in preprocessors.joblib meta)
    meta = models.get("meta") or {}
    outcome_classes = meta.get("outcome_classes") if isinstance(meta, dict) else None
    failure_classes = [str(outcome_classes[int(c)]) if outcome_classes is not None else str(c)
                       for c in failure_pipe.classes_]
    failure_proba = failure_pipe.predict_proba(X_failure)[0]
    ranked = sorted(
        zip(failure_classes, failure_proba), key=lambda kv: kv[1], reverse=True
    )

    safe_proba = float(recovery_pipe.predict_proba(X_binary)[0][1])  # P(TRUE)
    risk = float(np.clip(risk_pipe.predict(X_binary)[0], 0.0, 1.0))

    return {
        "failure_prediction": ranked[0][0],
        "failure_probabilities": {k: round(float(p), 4) for k, p in ranked},
        "recovery_risk": round(risk, 4),
        "safe_to_release": bool(safe_proba >= 0.5),
        "safe_to_release_probability": round(safe_proba, 4),
    }


def predict_batch(transactions: list[dict], models: dict[str, Any] | None = None) -> list[dict]:
    if models is None:
        models = load_models()
    return [predict_transaction(t, models) for t in transactions]


if __name__ == "__main__":
    demo_transactions = [
        {
            "transaction_id": "TXN-DEMO-0001",
            "amount": 42.50,
            "gateway_latency_ms": 85,
            "retry_count": 0,
            "network_quality": "Excellent",
            "previous_failures": 0,
            "account_age_days": 900,
            "status": "SUCCESS",
            "failure_reason": None,
        },
        {
            "transaction_id": "TXN-DEMO-0002",
            "amount": 1250.00,
            "gateway_latency_ms": 2800,
            "retry_count": 3,
            "network_quality": "Poor",
            "previous_failures": 2,
            "account_age_days": 40,
            "status": "STALLED",
            "failure_reason": None,
        },
        {
            "transaction_id": "TXN-DEMO-0003",
            "amount": 2500.00,
            "gateway_latency_ms": 900,
            "retry_count": 2,
            "network_quality": "Fair",
            "previous_failures": 1,
            "account_age_days": 200,
            "status": "FAILED",
            "failure_reason": "Insufficient Balance",
        },
    ]

    loaded = load_models()
    for tx in demo_transactions:
        print(f"\n>>> {tx['transaction_id']}")
        print(json.dumps(predict_transaction(tx, loaded), indent=2))

"""
api/services/ml_service.py — bridge to the Stage 2 ML engine.

The trained artifacts in models/ are loaded EXACTLY ONCE (FastAPI lifespan,
see api/main.py) and reused for every request; nothing is retrained and no
synthetic data is generated at startup.

Adaptation note (intentional, not a rewrite of the ML engine): the existing
ml.predict.predict_transaction() returns the model's recovery-risk output
under the key ``recovery_risk``; the backend/API contract exposes the same
value as ``risk_score``. assess() performs that single documented mapping and
is the ONLY place the two vocabularies meet.

The Stage 2 leakage rule is preserved here by construction: predict() never
receives risk_score or safe_to_release as inputs — only raw transaction
attributes plus the observed status/failure_reason.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from api.core.config import Settings, get_settings
from api.core.exceptions import MLServiceError

logger = logging.getLogger("payment_recovery.ml")

# project root = two levels above this file (api/services/ml_service.py)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class MLService:
    """Holds the joblib pipelines; loaded once, shared across requests."""

    def __init__(self) -> None:
        self._models: dict[str, Any] | None = None

    @property
    def loaded(self) -> bool:
        return self._models is not None

    def load(self, settings: Settings | None = None) -> None:
        # imported here so importing this module never pulls torch-heavy
        # ML deps before the app decides to load models
        from ml.predict import load_models

        settings = settings or get_settings()
        model_dir = settings.model_dir
        if not os.path.isabs(model_dir):
            model_dir = os.path.join(PROJECT_ROOT, model_dir)
        logger.info("loading ML models from %s", model_dir)
        self._models = load_models(models_dir=model_dir)
        logger.info("ML models loaded: %s", sorted(k for k in self._models if k != "meta"))

    def assess(
        self,
        *,
        amount: float,
        gateway_latency_ms: int,
        retry_count: int,
        network_quality: str,
        previous_failures: int,
        account_age_days: int,
        status: str,
        failure_reason: str | None,
    ) -> dict:
        """Run the three Stage 2 models over one transaction.

        Unknown categorical values and missing optional fields are safe:
        the Stage 2 preprocessing uses OneHotEncoder(handle_unknown='ignore')
        plus imputers, so the API never crashes on odd client input.
        """
        if not self.loaded:
            raise MLServiceError("ML models are not loaded")

        from ml.predict import predict_transaction

        raw = predict_transaction(
            {
                "amount": float(amount),
                "gateway_latency_ms": int(gateway_latency_ms),
                "retry_count": int(retry_count),
                "network_quality": network_quality,
                "previous_failures": int(previous_failures),
                "account_age_days": int(account_age_days),
                "status": status,
                "failure_reason": failure_reason,
            },
            models=self._models,
        )
        assessment = {
            "failure_prediction": raw["failure_prediction"],
            "failure_probabilities": raw["failure_probabilities"],
            "failure_probability": max(raw["failure_probabilities"].values())
            if raw["failure_probabilities"]
            else 0.0,
            # documented vocabulary mapping: recovery_risk -> risk_score
            "risk_score": raw["recovery_risk"],
            "safe_to_release_probability": raw["safe_to_release_probability"],
            "safe_to_release": raw["safe_to_release"],
        }
        logger.info(
            "ML assessment completed: failure_prediction=%s risk_score=%.4f "
            "safe_to_release=%s (p=%.4f)",
            assessment["failure_prediction"],
            assessment["risk_score"],
            assessment["safe_to_release"],
            assessment["safe_to_release_probability"],
        )
        return assessment


_ml_service = MLService()


def get_ml_service() -> MLService:
    return _ml_service

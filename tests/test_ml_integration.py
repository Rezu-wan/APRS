"""ML integration: assessment populated by the FAILED event; models loaded
once at lifespan; ML layer tolerates unseen categorical values."""

from __future__ import annotations

import uuid

from tests.conftest import CLEAN_FAILED_TX, SYSTEM_KEY, make_event

URL = "/api/v1/transaction/event"


def _unique_id(prefix: str = "TXN-ML") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class TestMLAssessment:
    def test_failed_event_populates_ml_fields_in_response_and_db(self, client):
        tid = _unique_id()
        resp = client.post(URL, json=make_event(tid, **CLEAN_FAILED_TX),
                           headers=SYSTEM_KEY)
        assert resp.status_code == 200
        ml = resp.json()["ml_assessment"]
        assert ml is not None
        for key in ("failure_prediction", "failure_probability",
                    "risk_score", "safe_to_release_probability", "safe_to_release"):
            assert key in ml
        assert 0.0 <= ml["risk_score"] <= 1.0
        assert 0.0 <= ml["safe_to_release_probability"] <= 1.0
        assert ml["safe_to_release"] is True
        assert ml["safe_to_release_probability"] >= 0.90

        fetch = client.get(f"/api/v1/transactions/{tid}", headers=SYSTEM_KEY)
        tx = fetch.json()
        assert tx["failure_prediction"] == ml["failure_prediction"]
        assert tx["risk_score"] == ml["risk_score"]
        assert tx["safe_to_release_probability"] == ml["safe_to_release_probability"]
        assert tx["safe_to_release"] is True

    def test_success_event_has_no_ml_assessment(self, client):
        tid = _unique_id()
        resp = client.post(URL, json=make_event(tid, amount=50.00, status="SUCCESS"),
                           headers=SYSTEM_KEY)
        assert resp.status_code == 200
        assert resp.json()["ml_assessment"] is None

    def test_models_loaded_once_by_lifespan(self, client):
        """Lifespan loaded the models into the shared singleton; health must
        report them as loaded and MLService must report loaded."""
        health = client.get("/health").json()
        assert health["ml_models"] == "loaded"

        from api.services.ml_service import get_ml_service
        assert get_ml_service().loaded is True
        # repeated calls return the same singleton (models loaded once)
        assert get_ml_service() is get_ml_service()


class TestMLUnknownCategoryTolerance:
    def test_predict_transaction_tolerates_unseen_category(self, client):
        """Documented behaviour: the ml layer never raises on unseen
        categorical values (OneHotEncoder(handle_unknown='ignore') + imputers).
        (API-level unknown values are rejected with 422 by the schema.)"""
        from ml.predict import load_models, predict_transaction

        models = load_models()
        result = predict_transaction(
            dict(CLEAN_FAILED_TX, network_quality="Telepathic-5G"),
            models=models,
        )
        assert isinstance(result, dict)
        assert "failure_prediction" in result
        assert "recovery_risk" in result
        assert 0.0 <= result["recovery_risk"] <= 1.0
        assert isinstance(result["safe_to_release"], bool)

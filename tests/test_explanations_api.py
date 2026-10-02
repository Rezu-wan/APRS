"""POST /api/v1/explanations/transaction end-to-end: role enforcement, Bangla
customer explanations, support detail, response caching, deterministic
fallback on provider failure, error shapes, and decision immutability (GenAI
explains — it never mutates state)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from api.core.payment_lifecycle import (
    OUTCOME_CONFIRMED,
    OUTCOME_PROGRESS,
    OUTCOME_TIMEOUT,
    PaymentSource,
)
from api.db.database import engine
from api.db.models import PaymentEvent

try:  # Stage 7 model may land after this test file; guarded below.
    from api.db.models import RiskAssessmentRecord
except ImportError:  # pragma: no cover
    RiskAssessmentRecord = None
from api.main import app
from api.services.ai import get_ai_provider
from api.services.ai.mock_provider import FailingMockProvider
from tests.conftest import CLEAN_FAILED_TX, CUSTOMER_KEY, SUPPORT_KEY, SYSTEM_KEY

EVENT_URL = "/api/v1/transaction/event"
RELEASE_URL = "/api/v1/recovery/release-limit"
EXPLANATION_URL = "/api/v1/explanations/transaction"


def _unique_id(prefix: str = "TXN-EXPLAIN") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _ingest_failed(client, tid, **overrides):
    payload = dict(
        transaction_id=tid, user_id="USER-001", merchant_id="MERCHANT-001",
        currency="BDT",
    )
    payload.update(CLEAN_FAILED_TX)
    payload.update(overrides)
    return client.post(EVENT_URL, json=payload, headers=SYSTEM_KEY)


def _released_tx(client, tid):
    """Ingest a clean failed transaction and run the recovery decision so it
    sits in LIMIT_RELEASED."""
    assert _ingest_failed(client, tid).status_code == 200
    resp = client.post(RELEASE_URL, json={"transaction_id": tid},
                       headers=SYSTEM_KEY)
    assert resp.status_code == 200
    assert resp.json()["decision"] == "LIMIT_RELEASED"


def _explain(client, tid, language="bn", audience="customer", headers=SYSTEM_KEY,
             **extra):
    body = {"transaction_id": tid, "language": language, "audience": audience}
    body.update(extra)
    return client.post(EXPLANATION_URL, json=body, headers=headers)


def _has_bengali(text: str) -> bool:
    return any("ঀ" <= ch <= "৿" for ch in text)


def test_customer_bangla_explanation_from_mock_provider(client):
    tid = _unique_id()
    _released_tx(client, tid)

    resp = _explain(client, tid)
    assert resp.status_code == 200
    body = resp.json()

    assert body["transaction_id"] == tid
    assert body["explanation"].strip() != ""
    assert _has_bengali(body["explanation"])
    assert body["provider"] == "mock"
    assert body["prompt_version"] == "v3"
    assert body["is_fallback"] is False
    assert body["cached"] is False


def test_support_english_explanation_contains_decision_and_id(client):
    tid = _unique_id()
    _released_tx(client, tid)

    resp = _explain(client, tid, language="en", audience="support",
                    headers=SUPPORT_KEY)
    assert resp.status_code == 200
    body = resp.json()

    assert body["explanation"].strip() != ""
    assert tid in body["explanation"]
    assert "LIMIT_RELEASED" in body["explanation"]


def test_identical_second_call_is_served_from_cache(client):
    tid = _unique_id()
    _released_tx(client, tid)

    first = _explain(client, tid)
    assert first.status_code == 200
    assert first.json()["cached"] is False

    second = _explain(client, tid)
    assert second.status_code == 200
    replay = second.json()
    assert replay["cached"] is True
    assert replay["explanation"] == first.json()["explanation"]


def test_provider_failure_falls_back_deterministically(client):
    tid = _unique_id()
    _released_tx(client, tid)

    app.dependency_overrides[get_ai_provider] = lambda: FailingMockProvider()
    try:
        resp = _explain(client, tid)
    finally:
        app.dependency_overrides.pop(get_ai_provider, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["is_fallback"] is True
    assert body["explanation"].strip() != ""


def test_unknown_transaction_is_404_not_found(client):
    resp = _explain(client, "TXN-DOES-NOT-EXIST")
    assert resp.status_code == 404
    error = resp.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert error["message"]


def test_explanations_require_api_key_and_role(client):
    tid = _unique_id()

    resp = _explain(client, tid, headers={})
    assert resp.status_code == 401

    # CUSTOMER role must not see explanations at all.
    assert _explain(client, tid, headers=CUSTOMER_KEY).status_code == 403


def test_client_cannot_inject_decision_fields(client):
    """Extra body fields are ignored — the explanation must reflect the
    decision stored in the DB (LIMIT_RELEASED), never caller input."""
    tid = _unique_id()
    _released_tx(client, tid)

    resp = _explain(client, tid, language="en", audience="support",
                    safe_to_release=False, decision="RECOVERY_REJECTED")
    assert resp.status_code == 200
    body = resp.json()

    assert body["explanation"].strip() != ""
    assert "LIMIT_RELEASED" in body["explanation"]
    assert "RECOVERY_REJECTED" not in body["explanation"]


def _insert_payment_events(tid: str, event_types: list[tuple[str, str, str]]) -> None:
    """Insert PaymentEvent rows directly via the same engine conftest pins.
    Each entry is (event_type, source, status) with monotonically increasing
    domain timestamps so ordering is deterministic."""
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    with Session(bind=engine) as db:
        for i, (event_type, source, status) in enumerate(event_types):
            db.add(
                PaymentEvent(
                    transaction_id=tid,
                    provider_event_id=f"PE-{tid}-{i}",
                    event_type=event_type,
                    source=source,
                    status=status,
                    event_timestamp=base + timedelta(minutes=i),
                )
            )
        db.commit()


def test_explanation_includes_deterministic_root_cause(client):
    """A transaction WITH payment events gets the Stage 6 reconstruction
    projected into its explanation; the fallback leads with the fixed
    deterministic root-cause sentence (GenAI reports evidence, never
    re-derives it)."""
    tid = _unique_id("TXN-RECON")
    assert _ingest_failed(client, tid).status_code == 200
    _insert_payment_events(
        tid,
        [
            ("CUSTOMER_DEBIT_CONFIRMED", PaymentSource.BANK, OUTCOME_CONFIRMED),
            ("GATEWAY_REQUEST_SENT", PaymentSource.GATEWAY, OUTCOME_PROGRESS),
            ("GATEWAY_RESPONSE_RECEIVED", PaymentSource.GATEWAY, OUTCOME_CONFIRMED),
            (
                "MERCHANT_CONFIRMATION_TIMEOUT",
                PaymentSource.MERCHANT,
                OUTCOME_TIMEOUT,
            ),
        ],
    )

    app.dependency_overrides[get_ai_provider] = lambda: FailingMockProvider()
    try:
        resp = _explain(client, tid, language="en", audience="support",
                        headers=SUPPORT_KEY)
    finally:
        app.dependency_overrides.pop(get_ai_provider, None)

    assert resp.status_code == 200
    body = resp.json()
    assert "Root cause: merchant confirmation timeout." in body["explanation"]
    # forced FailingMockProvider -> deterministic fallback output
    assert body["is_fallback"] is True


def test_explanation_without_payment_events_unchanged(client):
    """Regression guard: a transaction with NO payment-domain events explains
    exactly as before — no reconstruction sentence, no crash."""
    tid = _unique_id("TXN-NORECON")
    _released_tx(client, tid)

    resp = _explain(client, tid, language="en", audience="support",
                    headers=SUPPORT_KEY)
    assert resp.status_code == 200
    body = resp.json()

    assert body["explanation"].strip() != ""
    assert "Root cause:" not in body["explanation"]
    assert "The payment flow is incomplete" not in body["explanation"]


def _insert_risk_assessment(tid: str) -> None:
    """Insert a minimal RiskAssessmentRecord directly via the same engine
    conftest pins — the explanation path is read-only, so the record is
    planted as if the Stage 7 engine had already produced it."""
    if RiskAssessmentRecord is None:  # pragma: no cover - guarded by skipif
        pytest.skip("RiskAssessmentRecord model not available yet")
    with Session(bind=engine) as db:
        db.add(
            RiskAssessmentRecord(
                assessment_id=str(uuid.uuid4()),
                transaction_id=tid,
                anomaly_type="GENUINE_FAILURE",
                risk_level="LOW",
                risk_score=0.2,
                ml_anomaly_score=0.15,
                deterministic_risk_score=0.2,
                recovery_candidate=True,
                recovery_block_reason=None,
                triggered_rules=[],
                evidence=[],
                model_version="synthetic-v1",
                rule_version="1",
                customer_reported_failure=False,
                evidence_fingerprint="test",
            )
        )
        db.commit()


@pytest.mark.skipif(
    RiskAssessmentRecord is None,
    reason="Stage 7 RiskAssessmentRecord model not available yet",
)
def test_risk_assessment_projected_into_explanations(client):
    """With a stored Stage 7 assessment, the SUPPORT/en explanation reports
    the classification faithfully while the CUSTOMER/bn explanation stays
    neutral — no anomaly type, no 'anomaly' wording, only review status."""
    tid = _unique_id("TXN-RISK")
    _released_tx(client, tid)
    _insert_risk_assessment(tid)

    app.dependency_overrides[get_ai_provider] = lambda: FailingMockProvider()
    try:
        support = _explain(client, tid, language="en", audience="support",
                           headers=SUPPORT_KEY)
        customer = _explain(client, tid, language="bn", audience="customer")
    finally:
        app.dependency_overrides.pop(get_ai_provider, None)

    assert support.status_code == 200
    assert "Anomaly classification:" in support.json()["explanation"]

    assert customer.status_code == 200
    customer_text = customer.json()["explanation"]
    assert "GENUINE_FAILURE" not in customer_text
    assert "anomaly" not in customer_text.lower()
    assert (
        "আপনার লেনদেনটি পেমেন্ট সিস্টেমের প্রমাণের ভিত্তিতে মূল্যায়ন করা হচ্ছে। "
        "পুনরুদ্ধারের যোগ্যতা: পর্যালোচনাধীন।" in customer_text
    )


def test_explanations_never_mutate_transaction_state(client):
    tid = _unique_id()
    _released_tx(client, tid)

    def _snapshot():
        tx = client.get(f"/api/v1/transactions/{tid}", headers=SYSTEM_KEY).json()
        timeline = client.get(f"/api/v1/transactions/{tid}/timeline",
                              headers=SYSTEM_KEY).json()
        fields = ("current_state", "risk_score", "safe_to_release", "decision")
        return {k: tx[k] for k in fields if k in tx}, timeline["event_count"]

    before_tx, before_events = _snapshot()

    assert _explain(client, tid).status_code == 200
    assert _explain(client, tid, language="en", audience="support").status_code == 200

    after_tx, after_events = _snapshot()
    assert after_tx == before_tx
    assert after_events == before_events

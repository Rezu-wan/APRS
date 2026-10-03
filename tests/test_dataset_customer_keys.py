"""tests/test_dataset_customer_keys.py — the dynamic dev-customer key scheme
(``dev-customer-CUST-XXXXXX``) and the /customers/me profile endpoint.

The dynamic scheme lets any dataset customer sign in without enumerating 600
key:customer pairs in CUSTOMER_API_KEYS. It must:
- resolve to a CUSTOMER AuthContext bound to that customer id (dev only),
- fall back to AuthenticationError for malformed ids,
- never resolve in production (the scheme is dev-only by design).
"""

import pytest

from api.core.config import get_settings
from api.core.security import AuthContext, _dynamic_customer_auth


@pytest.fixture(scope="module", autouse=True)
def dataset_rows(client):
    """The test DB is created empty (conftest create_all) — seed the minimal
    reference + transaction rows these tests read through the API."""
    from datetime import datetime, timezone
    from decimal import Decimal

    from api.db.database import SessionLocal
    from api.db.models import Customer, Merchant, Transaction

    session = SessionLocal()
    try:
        session.add(
            Customer(
                customer_id="CUST-000084",
                full_name="Sabbir Mustafi",
                email="sabbir@example.com",
                phone="+8801",
                country="BD",
                created_at=datetime(2026, 2, 20, tzinfo=timezone.utc),
                status="active",
                segment="retail",
                risk_profile="LOW",
                archetype="biller",
            )
        )
        session.add(
            Merchant(
                merchant_id="MER-TEST-1",
                name="Test Bazaar",
                category="grocery",
                country="BD",
                risk_tier="LOW",
            )
        )
        session.add(
            Transaction(
                transaction_id="TXN-DSTEST-1",
                user_id="CUST-000084",
                merchant_id="MER-TEST-1",
                amount=Decimal("1200.00"),
                currency="BDT",
                timestamp=datetime(2025, 11, 10, 20, 39, 12, tzinfo=timezone.utc),
                transaction_type="purchase",
                channel="mobile_app",
                direction="debit",
                country="BD",
                current_state="SUCCESS",
            )
        )
        session.commit()
    finally:
        session.close()


def test_dynamic_key_binds_to_its_customer_id():
    ctx = _dynamic_customer_auth("dev-customer-CUST-000084", get_settings())
    assert ctx == AuthContext(
        role="CUSTOMER", key_name="customer:CUST-000084", customer_id="CUST-000084"
    )


@pytest.mark.parametrize(
    "key",
    [
        "dev-customer-CUST-84",  # wrong id shape
        "dev-customer-CUST-0000842",  # 7 digits
        "dev-customer-alice",  # static keys live in the config, not here
        "dev-customer-cust-000001",  # case-sensitive
        "customer-CUST-000001",  # missing prefix
        "",  # nothing at all
    ],
)
def test_dynamic_key_rejects_malformed_keys(key):
    assert _dynamic_customer_auth(key, get_settings()) is None


def test_dynamic_key_never_resolves_in_production(monkeypatch):
    monkeypatch.setattr(get_settings(), "environment", "production")
    assert _dynamic_customer_auth("dev-customer-CUST-000084", get_settings()) is None


def test_dataset_customer_can_sign_in_and_read_own_transactions(client):
    # CUST-000084 owns DEMO-S1 in the loaded dataset; the ownership contract
    # (200 on own, non-enumerating 403 on foreign ids) must hold for a
    # dynamically-bound key exactly as for static pairs.
    key = {"X-API-Key": "dev-customer-CUST-000084"}
    listing = client.get("/api/v1/transactions?limit=200", headers=key)
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] >= 1
    assert {item["user_id"] for item in body["items"]} == {"CUST-000084"}
    own_id = body["items"][0]["transaction_id"]
    assert client.get(f"/api/v1/transactions/{own_id}", headers=key).status_code == 200
    assert client.get("/api/v1/transactions/DEMO-S2", headers=key).status_code == 403


def test_me_profile_serves_only_the_callers_own_row(client):
    key = {"X-API-Key": "dev-customer-CUST-000084"}
    resp = client.get("/api/v1/customers/me", headers=key)
    assert resp.status_code == 200
    profile = resp.json()
    assert profile["customer_id"] == "CUST-000084"
    assert profile["full_name"]
    assert profile["segment"]
    # staff may not read customer profiles at all
    assert client.get("/api/v1/customers/me", headers={"X-API-Key": "dev-admin-key"}).status_code == 403


def test_me_profile_404_for_identity_without_profile(client):
    # alice is a valid CUSTOMER identity but has no dataset profile row
    resp = client.get("/api/v1/customers/me", headers={"X-API-Key": "dev-customer-alice"})
    assert resp.status_code == 404


def test_transaction_responses_carry_merchant_and_attributes(client):
    key = {"X-API-Key": "dev-customer-CUST-000084"}
    body = client.get("/api/v1/transactions?limit=5", headers=key).json()
    assert body["total"] >= 1
    item = body["items"][0]
    # descriptive attributes resolved from the dataset
    assert item["transaction_type"]
    assert item["channel"]
    # merchant ids with a catalog row resolve to name + category
    if item["merchant_id"]:
        assert item["merchant_name"]
        assert item["merchant_category"]

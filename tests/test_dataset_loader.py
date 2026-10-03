"""tests/test_dataset_loader.py — unit tests for the dataset loader's row
mappers (no database): the CSV conventions are the contract.

Conventions under test (data/dataset/README.md):
- empty string = not applicable / not observed -> NULL,
- booleans are the strings "true"/"false" (bool("false") is True — the classic
  bug this guards against),
- timestamps are "YYYY-MM-DD HH:MM:SS+00:00",
- JSON columns are compact JSON; "" means NULL.
"""

from datetime import datetime, timezone
from decimal import Decimal

from scripts.load_dataset import (
    customer_row,
    merchant_row,
    parse_bool,
    parse_dt,
    parse_json,
    payment_row,
    recovery_row,
    risk_row,
    txn_row,
    twin_row,
)


def test_parse_bool_maps_dataset_strings():
    assert parse_bool("true") is True
    assert parse_bool("false") is False
    assert parse_bool("") is None


def test_parse_dt_parses_dataset_timestamps():
    parsed = parse_dt("2025-11-10 20:39:12+00:00")
    assert parsed == datetime(2025, 11, 10, 20, 39, 12, tzinfo=timezone.utc)
    assert parse_dt("") is None


def test_parse_json_empty_string_is_null():
    assert parse_json("") is None
    assert parse_json('{"attempt":2}') == {"attempt": 2}
    assert parse_json("[]") == []


def test_txn_row_maps_customer_id_to_user_id_and_skips_risk_internals():
    row = txn_row(
        {
            "transaction_id": "TXN-0000001",
            "customer_id": "CUST-000001",
            "account_id": "ACCT-000001",
            "merchant_id": "MER-0001",
            "counterparty": "",
            "direction": "debit",
            "amount": "2252.42",
            "currency": "BDT",
            "transaction_type": "purchase",
            "channel": "mobile_app",
            "country": "BD",
            "device_id": "DEV-00001",
            "timestamp": "2025-11-10 20:39:12+00:00",
            "gateway_latency_ms": "2484",
            "retry_count": "2",
            "network_quality": "Good",
            "previous_failures": "1",
            "account_age_days": "8",
            "failure_reason": "Merchant Disconnect",
            "current_state": "MANUAL_REVIEW",
            "scenario": "",
            "risk_score": "0.278",
            "risk_level": "LOW",
            "risk_decision": "ALLOW",
            "failure_prediction": "Gateway Error",
            "failure_probability": "0.887",
            "safe_to_release_probability": "0.731",
            "safe_to_release": "false",
        }
    )
    assert row["user_id"] == "CUST-000001"  # customer_id renames to user_id
    assert row["amount"] == Decimal("2252.42")
    assert row["safe_to_release"] is False
    assert row["transaction_type"] == "purchase"
    # risk-internal CSV columns have no model destination
    assert "risk_level" not in row
    assert "risk_decision" not in row
    assert "scenario" not in row
    assert "device_id" not in row
    # empty merchant id (P2P rows) is kept as the empty string (column NOT NULL)
    assert txn_row(_minimal_txn(merchant_id=""))["merchant_id"] == ""


def test_txn_row_empty_observables_become_null():
    row = txn_row(_minimal_txn())
    assert row["failure_reason"] is None
    assert row["failure_prediction"] is None
    assert row["failure_probability"] is None
    assert row["safe_to_release"] is None


def test_twin_row_metadata_empty_becomes_none():
    row = twin_row(_minimal_twin(metadata=""))
    assert row["event_metadata"] is None
    row = twin_row(_minimal_twin(metadata='{"attempt":2}'))
    assert row["event_metadata"] == {"attempt": 2}


def test_payment_row_maps_metadata_and_ids():
    row = payment_row(
        {
            "event_id": "PEV-0000001",
            "transaction_id": "TXN-0000001",
            "provider_event_id": "PEV-0000001abc-123456789",
            "event_type": "BANK_DEBIT_CONFIRMED",
            "source": "BANK",
            "status": "CONFIRMED",
            "event_timestamp": "2025-11-10 20:40:00+00:00",
            "reference_id": "REF-ABCD1234",
            "latency_ms": "210",
            "metadata": "",
            "correlation_id": "",
            "causation_id": "",
            "schema_version": "1",
        }
    )
    assert row["event_metadata"] is None
    assert row["latency_ms"] == 210
    assert row["schema_version"] == "1"
    assert row["correlation_id"] is None


def test_risk_row_parses_evidence_and_rules_json():
    row = risk_row(_minimal_risk())
    assert row["evidence"] == [{"code": "HIGH_LATENCY"}]
    assert row["triggered_rules"] == []
    assert row["recovery_candidate"] is True
    assert row["customer_reported_failure"] is False
    # columns with no model destination never reach the mapper output
    assert "risk_factors" not in row
    assert "decision" not in row


def test_recovery_row_renames_case_columns():
    row = recovery_row(_minimal_recovery())
    assert row["action"] == "RELEASE_LIMIT"  # recovery_decision
    assert row["status"] == "COMPLETED"  # recovery_status
    assert row["attempt_count"] == 1  # recovery_attempts
    assert row["decision_reason"] == "Genuine failure"  # recovery_reason
    assert row["released_amount"] is None
    assert "recovery_required" not in row
    assert "recovery_result" not in row
    assert "verification_status" not in row


def test_customer_and_merchant_rows():
    customer = customer_row(
        {
            "customer_id": "CUST-000001",
            "full_name": "Tanvir Mustafi",
            "email": "t@example.com",
            "phone": "+8801",
            "country": "BD",
            "created_at": "2025-11-02 10:21:00+00:00",
            "status": "active",
            "segment": "retail",
            "risk_profile": "LOW",
            "archetype": "shopper",
        }
    )
    assert customer["full_name"] == "Tanvir Mustafi"
    merchant = merchant_row(
        {
            "merchant_id": "MER-0001",
            "name": "Dhaka Fresh Mart",
            "category": "grocery",
            "country": "BD",
            "risk_tier": "LOW",
            "traffic_weight": "0.5",
            "created_at": "2025-10-01 00:00:00+00:00",
        }
    )
    assert merchant == {
        "merchant_id": "MER-0001",
        "name": "Dhaka Fresh Mart",
        "category": "grocery",
        "country": "BD",
        "risk_tier": "LOW",
    }  # traffic_weight/created_at have no model destination


# --- minimal-row builders ----------------------------------------------------


def _minimal_txn(**overrides):
    base = {
        "transaction_id": "TXN-0000002",
        "customer_id": "CUST-000001",
        "merchant_id": "MER-0001",
        "amount": "848.07",
        "currency": "BDT",
        "timestamp": "2025-11-11 12:47:45+00:00",
        "gateway_latency_ms": "1611",
        "retry_count": "0",
        "network_quality": "Fair",
        "previous_failures": "0",
        "account_age_days": "9",
        "failure_reason": "",
        "current_state": "SUCCESS",
        "transaction_type": "purchase",
        "channel": "mobile_app",
        "direction": "debit",
        "country": "BD",
        "failure_prediction": "",
        "failure_probability": "",
        "risk_score": "0.139",
        "safe_to_release_probability": "0.878",
        "safe_to_release": "",
    }
    base.update(overrides)
    return base


def _minimal_twin(metadata: str):
    return {
        "event_id": "EVT-0000001",
        "transaction_id": "TXN-0000002",
        "timestamp": "2025-11-11 12:47:45+00:00",
        "event_type": "TRANSACTION_CREATED",
        "previous_state": "",
        "new_state": "INITIATED",
        "failure_prediction": "",
        "risk_score": "",
        "safe_to_release_probability": "",
        "safe_to_release": "",
        "reason": "",
        "metadata": metadata,
    }


def _minimal_risk():
    return {
        "assessment_id": "RSA-0000001",
        "transaction_id": "TXN-0000002",
        "evidence_fingerprint": "f" * 64,
        "anomaly_type": "GENUINE_FAILURE",
        "risk_level": "LOW",
        "risk_score": "0.2",
        "ml_anomaly_score": "",
        "deterministic_risk_score": "0.2",
        "recovery_candidate": "true",
        "recovery_block_reason": "",
        "reconstruction_root_cause": "",
        "reconstruction_confidence": "",
        "customer_reported_failure": "false",
        "evidence": '[{"code":"HIGH_LATENCY"}]',
        "triggered_rules": "[]",
        "model_version": "synthetic-v1",
        "rule_version": "1",
        "risk_factors": "{}",
        "decision": "ALLOW",
        "model_name": "anomaly-scenario-classifier",
        "assessed_at": "2025-11-11 12:48:00+00:00",
        "created_at": "2025-11-11 12:48:00+00:00",
    }


def _minimal_recovery():
    return {
        "recovery_id": "RCV-000001",
        "transaction_id": "TXN-0000002",
        "recovery_required": "true",
        "recovery_reason": "Genuine failure",
        "recovery_status": "COMPLETED",
        "recovery_attempts": "1",
        "recovery_decision": "RELEASE_LIMIT",
        "recovery_result": "RECOVERED",
        "verification_status": "VERIFIED",
        "requested_amount": "848.07",
        "released_amount": "",
        "currency": "BDT",
        "blocked_reason": "",
        "provider": "mock",
        "provider_reference": "REL-ABCD1234",
        "policy_version": "autonomous-v1",
        "risk_assessment_id": "RSA-0000001",
        "idempotency_key": "a" * 64,
        "created_at": "2025-11-11 12:48:00+00:00",
        "completed_at": "",
        "verified_at": "",
    }

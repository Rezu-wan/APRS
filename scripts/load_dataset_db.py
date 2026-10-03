"""
scripts/load_dataset_db.py — load the `db`-branch dataset into a TEMP app DB.
==============================================================================

Takes the relational synthetic dataset from the `db` branch
(data/dataset/*.csv, SEED=42, vocabulary-aligned with the app) and bulk
loads it into an EMPTY, alembic-migrated database pointed at by
DATABASE_URL — so the app can temporarily serve the full 10.5k-transaction
test dataset without touching the normal demo database.

    # 1. create + migrate the temp DB (SQLite here, Postgres works too)
    rm -f data/test_dataset.db
    DATABASE_URL=sqlite:///./data/test_dataset.db python -m alembic upgrade head
    # 2. load
    DATABASE_URL=sqlite:///./data/test_dataset.db python -m scripts.load_dataset_db
    # 3. run the app against it
    DATABASE_URL=sqlite:///./data/test_dataset.db uvicorn api.main:app

Loaded: transactions, payment_events, digital_twin_events,
risk_assessments, recovery_cases (-> recovery_actions).
Deliberately SKIPPED (no app table; derivable live by the Stage 11
services): customers, accounts, merchants, devices, model_assessments,
behavior_signals, relationship_edges.

Safety: refuses to load into a non-empty transactions table. Verifies
counts against manifest.json and reports orphans. 100% synthetic data.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, insert, text  # noqa: E402

from api.db.database import SessionLocal  # noqa: E402  (DATABASE_URL read at import)
from api.db.models import (  # noqa: E402
    DigitalTwinEvent,
    PaymentEvent,
    RecoveryActionRecord,
    RiskAssessmentRecord,
    Transaction,
)

DATASET_DIR = os.path.join("data", "dataset")
CHUNK = 5000


def _parse_dt(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    return datetime.fromisoformat(value.strip())


def _parse_json(value: str | None):
    if value is None or not value.strip():
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}


def _dec(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    return Decimal(value)


def _int(value: str | None, default: int | None = None) -> int | None:
    if value is None or not value.strip():
        return default
    return int(float(value))


def _num(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    return float(value)


def _bool(value: str | None) -> bool | None:
    if value is None or not value.strip():
        return None
    return value.strip().lower() in ("true", "1", "yes")


def _rows(name: str):
    with open(os.path.join(DATASET_DIR, f"{name}.csv"), encoding="utf-8") as fh:
        yield from csv.DictReader(fh)


def _bulk(db, model, rows: list[dict], label: str) -> int:
    total = 0
    for start in range(0, len(rows), CHUNK):
        db.execute(insert(model), rows[start : start + CHUNK])
        total += len(rows[start : start + CHUNK])
    print(f"  {label}: {total} rows")
    return total


def _tx_row(r: dict) -> dict:
    return {
        "transaction_id": r["transaction_id"],
        "user_id": r["customer_id"],  # the app's ownership key
        "merchant_id": r["merchant_id"],
        "amount": _dec(r["amount"]),
        "currency": r["currency"],
        "timestamp": _parse_dt(r["timestamp"]),
        "gateway_latency_ms": _int(r["gateway_latency_ms"], 0) or 0,
        "retry_count": _int(r["retry_count"], 0) or 0,
        "network_quality": r["network_quality"],
        "previous_failures": _int(r["previous_failures"], 0) or 0,
        "account_age_days": _int(r["account_age_days"], 0) or 0,
        "failure_reason": r["failure_reason"] or None,
        "current_state": r["current_state"],
        "risk_score": _num(r["risk_score"]),
        "failure_prediction": r["failure_prediction"] or None,
        "failure_probability": _num(r["failure_probability"]),
        "safe_to_release_probability": _num(r["safe_to_release_probability"]),
        "safe_to_release": _bool(r["safe_to_release"]),
    }


def _event_row(r: dict) -> dict:
    return {
        "event_id": r["event_id"],
        "provider_event_id": r["provider_event_id"],
        "transaction_id": r["transaction_id"],
        "event_type": r["event_type"],
        "source": r["source"],
        "status": r["status"],
        "event_timestamp": _parse_dt(r["event_timestamp"]),
        "reference_id": r["reference_id"] or None,
        "latency_ms": _int(r["latency_ms"]),
        "event_metadata": _parse_json(r["metadata"]),
        "correlation_id": r["correlation_id"] or r["transaction_id"],
        "causation_id": r["causation_id"] or None,
        "schema_version": r["schema_version"] or "1",
    }


def _twin_row(r: dict) -> dict:
    return {
        "event_id": r["event_id"],
        "transaction_id": r["transaction_id"],
        "timestamp": _parse_dt(r["timestamp"]),
        "event_type": r["event_type"],
        "previous_state": r["previous_state"] or None,
        "new_state": r["new_state"],
        "failure_prediction": r["failure_prediction"] or None,
        "risk_score": _num(r["risk_score"]),
        "safe_to_release_probability": _num(r["safe_to_release_probability"]),
        "safe_to_release": _bool(r["safe_to_release"]),
        "reason": r["reason"] or None,
        "event_metadata": _parse_json(r["metadata"]),
    }


def _risk_row(r: dict) -> dict:
    return {
        "assessment_id": r["assessment_id"],
        "transaction_id": r["transaction_id"],
        "evidence_fingerprint": r["evidence_fingerprint"],
        "anomaly_type": r["anomaly_type"],
        "risk_level": r["risk_level"],
        "risk_score": _num(r["risk_score"]) or 0.0,
        "ml_anomaly_score": _num(r["ml_anomaly_score"]),
        "deterministic_risk_score": _num(r["deterministic_risk_score"]) or 0.0,
        "recovery_candidate": _bool(r["recovery_candidate"]) or False,
        "recovery_block_reason": r["recovery_block_reason"] or None,
        "reconstruction_root_cause": r["reconstruction_root_cause"] or None,
        "reconstruction_confidence": _num(r["reconstruction_confidence"]),
        "customer_reported_failure": _bool(r["customer_reported_failure"]) or False,
        "evidence": _parse_json(r["evidence"]),
        "triggered_rules": _parse_json(r["triggered_rules"]),
        "model_version": r["model_version"],
        "rule_version": r["rule_version"],
        "created_at": _parse_dt(r["created_at"]) or _parse_dt(r["assessed_at"]),
    }


def _recovery_row(r: dict) -> dict:
    idem = r["idempotency_key"] or hashlib.sha256(
        r["recovery_id"].encode()
    ).hexdigest()  # deterministic stand-in when the CSV omits the key
    return {
        "recovery_id": r["recovery_id"],
        "transaction_id": r["transaction_id"],
        "action": r["recovery_decision"],
        "status": r["recovery_status"],
        "idempotency_key": idem,
        "attempt_count": _int(r["recovery_attempts"], 1) or 1,
        "requested_amount": _dec(r["requested_amount"]),
        "released_amount": _dec(r["released_amount"]),
        "currency": r["currency"],
        "provider": r["provider"] or None,
        "provider_reference": r["provider_reference"] or None,
        "policy_version": r["policy_version"] or None,
        "risk_assessment_id": r["risk_assessment_id"] or None,
        "decision_reason": r["recovery_reason"] or None,
        "blocked_reason": r["blocked_reason"] or None,
        "verification_result": (
            {"verification_status": r["verification_status"]}
            if r["verification_status"]
            else None
        ),
        "created_at": _parse_dt(r["created_at"]),
        "completed_at": _parse_dt(r["completed_at"]),
        "verified_at": _parse_dt(r["verified_at"]),
    }


def main(argv: list[str] | None = None) -> int:
    global DATASET_DIR
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--dataset-dir", default=DATASET_DIR, help=f"default: {DATASET_DIR}"
    )
    args = parser.parse_args(argv)
    DATASET_DIR = args.dataset_dir

    db = SessionLocal()
    try:
        existing = db.query(func.count(Transaction.transaction_id)).scalar()
        if existing:
            print(
                f"ABORT: target DB already has {existing} transactions — "
                "the loader only fills an EMPTY temp database."
            )
            return 1

        print(f"Loading dataset from {DATASET_DIR}/")
        tx_rows = [_tx_row(r) for r in _rows("transactions")]
        known_ids = {r["transaction_id"] for r in tx_rows}
        db.execute(insert(Transaction), tx_rows)
        print(f"  transactions: {len(tx_rows)} rows")

        def _filter(rows: list[dict], label: str) -> list[dict]:
            orphans = [r for r in rows if r["transaction_id"] not in known_ids]
            if orphans:
                print(
                    f"  WARNING {label}: skipping {len(orphans)} orphaned rows "
                    "(no matching transaction)"
                )
            return [r for r in rows if r["transaction_id"] in known_ids]

        events = _filter([_event_row(r) for r in _rows("payment_events")], "payment_events")
        _bulk(db, PaymentEvent, events, "payment_events")

        twins = _filter([_twin_row(r) for r in _rows("digital_twin_events")], "twin_events")
        _bulk(db, DigitalTwinEvent, twins, "digital_twin_events")

        risks = _filter([_risk_row(r) for r in _rows("risk_assessments")], "risk_assessments")
        _bulk(db, RiskAssessmentRecord, risks, "risk_assessments")

        recs = _filter([_recovery_row(r) for r in _rows("recovery_cases")], "recovery_cases")
        _bulk(db, RecoveryActionRecord, recs, "recovery_actions")

        db.commit()

        print("\nVerification (DB vs manifest):")
        with open(os.path.join(DATASET_DIR, "manifest.json"), encoding="utf-8") as fh:
            manifest = json.load(fh)["counts"]
        for label, model, expected in (
            ("transactions", Transaction, manifest["transactions.csv"]),
            ("payment_events", PaymentEvent, manifest["payment_events.csv"]),
            ("digital_twin_events", DigitalTwinEvent, manifest["digital_twin_events.csv"]),
            ("risk_assessments", RiskAssessmentRecord, manifest["risk_assessments.csv"]),
            ("recovery_actions", RecoveryActionRecord, manifest["recovery_cases.csv"]),
        ):
            actual = db.query(func.count(text("1"))).select_from(model).scalar()
            mark = "OK " if actual == expected else "DIFF"
            print(f"  [{mark}] {label}: db={actual} manifest={expected}")
        print("\nDone — point the app at this DB with:")
        print(f"  DATABASE_URL={os.environ.get('DATABASE_URL', '(set DATABASE_URL)')} uvicorn api.main:app")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

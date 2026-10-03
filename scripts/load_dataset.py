"""Load the synthetic dataset (data/dataset/) into the app database.

Usage:
    python -m scripts.load_dataset            # wipe + load into DATABASE_URL
    python -m scripts.load_dataset --dry-run  # parse everything, load nothing

The dataset was generated to align with the existing tables. Mapping facts
(verified against the generator's output conventions):

- transactions.csv `customer_id` -> Transaction.user_id; 10 CSV columns have
  no model destination (account_id, counterparty, direction, transaction_type,
  channel, country, device_id, scenario, risk_level, risk_decision) — the four
  descriptive ones are loaded into the columns added by migration
  e8f9a0b1c2d3; the risk-internal ones stay out of the API model by design.
- digital_twin_events.csv / payment_events.csv are 1:1; CSV `metadata` maps to
  the JSON column (model attr event_metadata) and must become NULL when empty.
- risk_assessments.csv: risk_factors/decision/model_name/assessed_at have no
  model destination; evidence/triggered_rules are compact JSON.
- recovery_cases.csv -> recovery_actions with renames (recovery_status->status,
  recovery_attempts->attempt_count, recovery_decision->action,
  recovery_reason->decision_reason); recovery_required/recovery_result/
  verification_status have no model destination.
- customers.csv / merchants.csv -> the reference tables from migration
  e8f9a0b1c2d3.

Conventions: empty string = not applicable / not observed -> NULL; booleans
are the strings "true"/"false"; timestamps are "YYYY-MM-DD HH:MM:SS+00:00";
accounts/devices/behavior_signals/relationship_edges/model_assessments are
NOT loaded (no tables; risk internals the API deliberately keeps out).

The loader WIPES all transaction-domain tables first (the demo/E2E rows would
collide with the dataset's own DEMO-S1..S6 rows) and takes a timestamped
backup of a SQLite database file before touching it.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import insert, text

from api.db.database import engine, SessionLocal
from api.db.models import (
    Customer,
    DigitalTwinEvent,
    Merchant,
    PaymentEvent,
    RecoveryActionRecord,
    RiskAssessmentRecord,
    Transaction,
)

DATASET_DIR = Path("data/dataset")
BATCH_SIZE = 1000


def parse_dt(value: str) -> datetime | None:
    """'2025-11-10 20:39:12+00:00' -> aware datetime; '' -> None."""
    return datetime.fromisoformat(value) if value else None


def parse_bool(value: str) -> bool | None:
    """'true'/'false' -> bool; '' -> None. (bool('false') is True — never use.)"""
    if value == "":
        return None
    if value not in ("true", "false"):
        raise ValueError(f"not a dataset boolean: {value!r}")
    return value == "true"


def parse_float(value: str) -> float | None:
    return float(value) if value else None


def parse_int(value: str) -> int | None:
    return int(value) if value else None


def parse_dec(value: str) -> Decimal | None:
    return Decimal(value) if value else None


def parse_json(value: str) -> dict | list | None:
    """Compact JSON text -> object; '' (not observed) -> None."""
    return json.loads(value) if value else None


def parse_str(value: str) -> str | None:
    """'' (not applicable / not observed) -> NULL."""
    return value if value else None


def rows(csv_name: str):
    with open(DATASET_DIR / csv_name, newline="", encoding="utf-8") as fh:
        yield from csv.DictReader(fh)


def chunked(seq, size=BATCH_SIZE):
    batch = []
    for item in seq:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


# --- per-CSV row mappers (pure: CSV row -> model column dict) ---------------


def txn_row(r: dict) -> dict:
    return {
        "transaction_id": r["transaction_id"],
        "user_id": r["customer_id"],
        "merchant_id": r["merchant_id"],  # '' = no merchant (P2P/withdrawal)
        "amount": Decimal(r["amount"]),
        "currency": r["currency"],
        "timestamp": parse_dt(r["timestamp"]),
        "gateway_latency_ms": int(r["gateway_latency_ms"]),
        "retry_count": int(r["retry_count"]),
        "network_quality": r["network_quality"],
        "previous_failures": int(r["previous_failures"]),
        "account_age_days": int(r["account_age_days"]),
        "failure_reason": parse_str(r["failure_reason"]),
        "current_state": r["current_state"],
        "transaction_type": parse_str(r["transaction_type"]),
        "channel": parse_str(r["channel"]),
        "direction": parse_str(r["direction"]),
        "country": parse_str(r["country"]),
        "failure_prediction": parse_str(r["failure_prediction"]),
        "failure_probability": parse_float(r["failure_probability"]),
        "risk_score": parse_float(r["risk_score"]),
        "safe_to_release_probability": parse_float(r["safe_to_release_probability"]),
        "safe_to_release": parse_bool(r["safe_to_release"]),
    }


def twin_row(r: dict) -> dict:
    return {
        "event_id": r["event_id"],
        "transaction_id": r["transaction_id"],
        "timestamp": parse_dt(r["timestamp"]),
        "event_type": r["event_type"],
        "previous_state": parse_str(r["previous_state"]),
        "new_state": r["new_state"],
        "failure_prediction": parse_str(r["failure_prediction"]),
        "risk_score": parse_float(r["risk_score"]),
        "safe_to_release_probability": parse_float(r["safe_to_release_probability"]),
        "safe_to_release": parse_bool(r["safe_to_release"]),
        "reason": parse_str(r["reason"]),
        "event_metadata": parse_json(r["metadata"]),
    }


def payment_row(r: dict) -> dict:
    return {
        "event_id": r["event_id"],
        "transaction_id": r["transaction_id"],
        "provider_event_id": r["provider_event_id"],
        "event_type": r["event_type"],
        "source": r["source"],
        "status": r["status"],
        "event_timestamp": parse_dt(r["event_timestamp"]),
        "reference_id": parse_str(r["reference_id"]),
        "latency_ms": parse_int(r["latency_ms"]),
        "event_metadata": parse_json(r["metadata"]),
        "correlation_id": parse_str(r["correlation_id"]),
        "causation_id": parse_str(r["causation_id"]),
        "schema_version": parse_str(r["schema_version"]),
    }


def risk_row(r: dict) -> dict:
    return {
        "assessment_id": r["assessment_id"],
        "transaction_id": r["transaction_id"],
        "evidence_fingerprint": r["evidence_fingerprint"],
        "anomaly_type": r["anomaly_type"],
        "risk_level": r["risk_level"],
        "risk_score": float(r["risk_score"]),
        "ml_anomaly_score": parse_float(r["ml_anomaly_score"]),
        "deterministic_risk_score": float(r["deterministic_risk_score"]),
        "recovery_candidate": parse_bool(r["recovery_candidate"]),
        "recovery_block_reason": parse_str(r["recovery_block_reason"]),
        "reconstruction_root_cause": parse_str(r["reconstruction_root_cause"]),
        "reconstruction_confidence": parse_float(r["reconstruction_confidence"]),
        "customer_reported_failure": parse_bool(r["customer_reported_failure"]),
        "evidence": parse_json(r["evidence"]),
        "triggered_rules": parse_json(r["triggered_rules"]),
        "model_version": parse_str(r["model_version"]),
        "rule_version": parse_str(r["rule_version"]),
        "created_at": parse_dt(r["created_at"]),
    }


def recovery_row(r: dict) -> dict:
    return {
        "recovery_id": r["recovery_id"],
        "transaction_id": r["transaction_id"],
        "action": r["recovery_decision"],
        "status": r["recovery_status"],
        "idempotency_key": r["idempotency_key"],
        "attempt_count": int(r["recovery_attempts"]),
        "requested_amount": Decimal(r["requested_amount"]),
        "released_amount": parse_dec(r["released_amount"]),
        "currency": r["currency"],
        "policy_version": r["policy_version"],
        "decision_reason": r["recovery_reason"],
        "blocked_reason": parse_str(r["blocked_reason"]),
        "provider": parse_str(r["provider"]),
        "provider_reference": parse_str(r["provider_reference"]),
        "risk_assessment_id": parse_str(r["risk_assessment_id"]),
        "created_at": parse_dt(r["created_at"]),
        "completed_at": parse_dt(r["completed_at"]),
        "verified_at": parse_dt(r["verified_at"]),
    }


def customer_row(r: dict) -> dict:
    return {
        "customer_id": r["customer_id"],
        "full_name": r["full_name"],
        "email": r["email"],
        "phone": r["phone"],
        "country": r["country"],
        "created_at": parse_dt(r["created_at"]),
        "status": r["status"],
        "segment": r["segment"],
        "risk_profile": r["risk_profile"],
        "archetype": parse_str(r["archetype"]),
    }


def merchant_row(r: dict) -> dict:
    return {
        "merchant_id": r["merchant_id"],
        "name": r["name"],
        "category": r["category"],
        "country": r["country"],
        "risk_tier": r["risk_tier"],
    }


# --- load plan ---------------------------------------------------------------

# (csv, model, mapper) — parents before children so FKs are satisfiable at
# insert time even where the DB enforces them (PostgreSQL).
LOAD_PLAN = [
    ("customers.csv", Customer, customer_row),
    ("merchants.csv", Merchant, merchant_row),
    ("transactions.csv", Transaction, txn_row),
    ("digital_twin_events.csv", DigitalTwinEvent, twin_row),
    ("payment_events.csv", PaymentEvent, payment_row),
    ("risk_assessments.csv", RiskAssessmentRecord, risk_row),
    ("recovery_cases.csv", RecoveryActionRecord, recovery_row),
]

# Children first so a re-run can never violate FKs mid-wipe. recovery_decisions
# and sandbox_ledger_entries reference transactions too; ai_explanations and
# customer_reports are dataset-foreign derived data keyed to transaction ids
# that will no longer exist.
WIPE_ORDER = [
    "digital_twin_events",
    "payment_events",
    "risk_assessments",
    "recovery_actions",
    "recovery_decisions",
    "sandbox_ledger_entries",
    "ai_explanations",
    "customer_reports",
    "transactions",
]


def backup_sqlite(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite:///"):
        return None
    db_path = Path(database_url.removeprefix("sqlite:///"))
    if not db_path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_suffix(f".db.backup-{stamp}")
    shutil.copy2(db_path, backup)
    return backup


def wipe(session) -> None:
    for table in WIPE_ORDER:
        session.execute(text(f"DELETE FROM {table}"))  # noqa: S608 — constant names


def load(dry_run: bool = False) -> dict[str, int]:
    counts: dict[str, int] = {}
    session = SessionLocal()
    try:
        if not dry_run:
            wipe(session)
        for csv_name, model, mapper in LOAD_PLAN:
            n = 0
            for batch in chunked(mapper(r) for r in rows(csv_name)):
                if not dry_run:
                    session.execute(insert(model), batch)
                n += len(batch)
            counts[model.__tablename__] = n
            print(f"  {csv_name:<28} -> {model.__tablename__:<22} {n:>7} rows")
        if dry_run:
            session.rollback()
        else:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="parse only; write nothing")
    args = ap.parse_args()

    missing = [f for f in [c for c, _, _ in LOAD_PLAN] if not (DATASET_DIR / f).exists()]
    if missing:
        print(f"dataset files missing under {DATASET_DIR}/: {missing}", file=sys.stderr)
        return 2

    if not args.dry_run:
        from api.core.config import get_settings

        backup = backup_sqlite(get_settings().database_url)
        if backup:
            print(f"backup: {backup}")

    print("loading dataset" + (" (dry run)" if args.dry_run else ""))
    load(dry_run=args.dry_run)

    if not args.dry_run:
        manifest = json.loads((DATASET_DIR / "manifest.json").read_text())
        bad = verify_counts(manifest)
        if bad:
            print(f"{bad} count mismatches vs manifest.json", file=sys.stderr)
            return 1
    print("done")
    return 0


def verify_counts(manifest: dict) -> int:
    """Re-count via SQL and compare with manifest.json."""
    bad = 0
    session = SessionLocal()
    try:
        for csv_name, model, _ in LOAD_PLAN:
            want = manifest["counts"][csv_name]
            got = session.query(model).count()
            if got != want:
                print(f"  MISMATCH {model.__tablename__}: {got} in DB, manifest {want}")
                bad += 1
    finally:
        session.close()
    return bad


if __name__ == "__main__":
    raise SystemExit(main())

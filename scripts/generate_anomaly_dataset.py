"""
Stage 7 — Anomaly-Scenario Synthetic Dataset Generator
=====================================================

Generates data/anomaly_transactions.csv: a synthetic dataset of payment
transactions labeled with one of 13 anomaly SCENARIOS, described by the 18
pinned features that ml/predict_anomaly.build_features derives at inference
time. The scenario label plays the role of the ground truth the dedicated
anomaly classifier learns.

DESIGN PRINCIPLES (spec §14/§35):
  * REALISTIC OVERLAP — classes are NOT perfectly separable. Numeric features
    are jittered generously and specific overlap channels are built in on
    purpose:
      - ~8% of normal_success rows get high gateway latency
      - ~6% of normal_success rows get retry_count >= 3
      - 3% of normal_success rows show debit_confirmation_count = 2 (a legit
        retry that looks like a double deduction)
      - double_deduction rows sometimes have debit_confirmation_count = 1
        (the double charge is not visible in the event stream)
      - incomplete_event_chain rows overlap sparse-event rows of other classes
      - suspicious_high_retry overlaps the normal high-retry noise
      - false_complaint rows look EXACTLY like normal_success in the feature
        space (the evidence says success — that is the whole point), with only
        tiny distributional differences. The model is EXPECTED to honestly
        fail on this class most of the time.
  * Labels come from the same generator as the features (label-generation
    bias) — the model card must say so.
  * No tuning loop against metrics: distributions are fixed a priori.

Outputs:
  data/anomaly_transactions.csv

Usage:
  python scripts/generate_anomaly_dataset.py [--rows 24000] [--seed 2077]
      [--out data/anomaly_transactions.csv]
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

SEED = 2077
N_ROWS = 24_000

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data")

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

SCENARIOS = [
    "normal_success",
    "gateway_timeout",
    "gateway_error",
    "merchant_timeout",
    "merchant_error",
    "settlement_failure",
    "settlement_not_confirmed",
    "double_deduction",
    "duplicate_transaction",
    "successful_but_unconfirmed",
    "false_complaint",
    "suspicious_high_retry",
    "incomplete_event_chain",
]

NET_LEVELS = ["Excellent", "Good", "Fair", "Poor"]
NET_PROBS = [0.32, 0.33, 0.21, 0.14]

# Scenario target shares (sum = 1.0):
#   normal 40% | 6 genuine-failure scenarios ~5.8% each (~35%) | rest ~25%
SCENARIO_SHARES = {
    "normal_success": 0.40,
    "gateway_timeout": 0.059,
    "gateway_error": 0.058,
    "merchant_timeout": 0.058,
    "merchant_error": 0.058,
    "settlement_failure": 0.058,
    "settlement_not_confirmed": 0.059,
    "double_deduction": 0.045,
    "duplicate_transaction": 0.042,
    "successful_but_unconfirmed": 0.042,
    "false_complaint": 0.040,
    "suspicious_high_retry": 0.041,
    "incomplete_event_chain": 0.040,
}


def _jitter(rng: np.random.Generator, values: np.ndarray, rel: float, abs_: float) -> np.ndarray:
    """Multiplicative + additive noise so numeric features never sit on a
    clean decision boundary."""
    return np.maximum(
        0.0,
        values * rng.normal(1.0, rel, len(values)) + rng.normal(0.0, abs_, len(values)),
    )


def _base_rows(rng: np.random.Generator, n: int) -> dict[str, np.ndarray]:
    """Baseline 'healthy transaction' draw — every scenario starts from this
    distribution and then overrides, which is what creates overlap."""
    net_idx = rng.choice(len(NET_LEVELS), n, p=NET_PROBS)
    base: dict[str, np.ndarray] = {}
    base["network_quality"] = np.array(NET_LEVELS, dtype=object)[net_idx]

    lat = rng.lognormal(mean=4.45, sigma=0.45, size=n)  # median ~85 ms
    retry = rng.choice([0, 1, 2], n, p=[0.80, 0.15, 0.05])
    base["amount"] = np.round(_jitter(rng, rng.lognormal(4.6, 0.9, n), 0.02, 0.5), 2)
    base["gateway_latency_ms"] = np.round(_jitter(rng, lat, 0.25, 8.0)).astype(float)
    base["retry_count"] = retry.astype(float)
    base["previous_failures"] = rng.poisson(0.4, n).astype(float)
    base["account_age_days"] = np.round(_jitter(rng, rng.uniform(5, 2600, n), 0.0, 0.0)).astype(float)

    # Healthy event chain: initiated + debit confirmed + merchant confirmed
    # (+ settlement confirmation ~62% of the time)
    base["event_count"] = rng.choice([3, 4], n, p=[0.38, 0.62]).astype(float)
    base["debit_confirmation_count"] = np.ones(n)
    base["duplicate_event_count"] = np.zeros(n)
    for flag in (
        "has_gateway_timeout",
        "has_gateway_error",
        "has_merchant_timeout",
        "has_merchant_error",
        "has_settlement_failure",
    ):
        base[flag] = np.zeros(n)
    base["has_settlement_confirmation"] = (base["event_count"] == 4).astype(float)
    base["has_merchant_confirmation"] = np.ones(n)
    base["reconstruction_confidence"] = np.clip(rng.normal(0.92, 0.05, n), 0.0, 1.0)
    base["time_to_last_event_ms"] = np.round(
        _jitter(rng, rng.uniform(600, 4000, n), 0.30, 40.0)
    ).astype(float)
    return base


def _gen(rng: np.random.Generator, n: int) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Generate n rows; returns ({feature: array}, scenario array)."""
    # Scenario assignment
    names = list(SCENARIO_SHARES)
    probs = np.array([SCENARIO_SHARES[s] for s in names])
    probs /= probs.sum()
    scenario = rng.choice(names, n, p=probs)

    rows = _base_rows(rng, n)
    idx = {s: np.where(scenario == s)[0] for s in names}

    # --- normal_success (with deliberate noise channels) -------------------
    m = idx["normal_success"]
    if len(m):
        hi_lat = rng.random(len(m)) < 0.08          # 8% high-latency normals
        rows["gateway_latency_ms"][m[hi_lat]] = _jitter(
            rng, rng.lognormal(7.3, 0.35, int(hi_lat.sum())), 0.20, 20.0
        )
        hi_retry = rng.random(len(m)) < 0.06        # 6% retry >= 3 normals
        rows["retry_count"][m[hi_retry]] = rng.integers(3, 6, int(hi_retry.sum()))
        dbl = rng.random(len(m)) < 0.03             # 3% debit appears twice
        rows["debit_confirmation_count"][m[dbl]] = 2.0

    # --- genuine-failure family -------------------------------------------
    failure_specs = {
        # (flag, latency log-mean, retry dist, confirmations kept)
        "gateway_timeout": ("has_gateway_timeout", 7.4, [0.05, 0.20, 0.40, 0.35]),
        "gateway_error": ("has_gateway_error", 6.4, [0.15, 0.35, 0.35, 0.15]),
        "merchant_timeout": ("has_merchant_timeout", 5.6, [0.25, 0.40, 0.25, 0.10]),
        "merchant_error": ("has_merchant_error", 5.5, [0.30, 0.40, 0.22, 0.08]),
    }
    for scen, (flag, lat_mu, retry_p) in failure_specs.items():
        m = idx[scen]
        if not len(m):
            continue
        rows[flag][m] = 1.0
        rows["gateway_latency_ms"][m] = _jitter(rng, rng.lognormal(lat_mu, 0.45, len(m)), 0.25, 15.0)
        rows["retry_count"][m] = rng.choice(4, len(m), p=retry_p)
        rows["has_merchant_confirmation"][m] = rng.choice([0.0, 1.0], len(m), p=[0.85, 0.15])
        rows["has_settlement_confirmation"][m] = rng.choice([0.0, 1.0], len(m), p=[0.80, 0.20])
        rows["reconstruction_confidence"][m] = np.clip(rng.normal(0.55, 0.15, len(m)), 0, 1)
        rows["time_to_last_event_ms"][m] = _jitter(
            rng, rng.uniform(2000, 15000, len(m)), 0.35, 100.0
        )

    m = idx["settlement_failure"]
    if len(m):
        rows["has_settlement_failure"][m] = 1.0
        rows["has_settlement_confirmation"][m] = 0.0
        rows["has_merchant_confirmation"][m] = rng.choice([0.0, 1.0], len(m), p=[0.5, 0.5])
        rows["gateway_latency_ms"][m] = _jitter(rng, rng.lognormal(5.9, 0.5, len(m)), 0.3, 15.0)
        rows["retry_count"][m] = rng.choice([0, 1, 2, 3], len(m), p=[0.3, 0.35, 0.25, 0.10])
        rows["reconstruction_confidence"][m] = np.clip(rng.normal(0.60, 0.15, len(m)), 0, 1)
        rows["time_to_last_event_ms"][m] = _jitter(rng, rng.uniform(1500, 12000, len(m)), 0.35, 80.0)

    m = idx["settlement_not_confirmed"]
    if len(m):
        # Success-shaped except the settlement never confirms — overlaps
        # successful_but_unconfirmed AND plain normal (4-event rows are rare).
        rows["has_settlement_confirmation"][m] = 0.0
        rows["event_count"][m] = rng.choice([3, 4], len(m), p=[0.85, 0.15])
        rows["gateway_latency_ms"][m] = _jitter(rng, rng.lognormal(5.0, 0.5, len(m)), 0.3, 10.0)
        rows["reconstruction_confidence"][m] = np.clip(rng.normal(0.78, 0.10, len(m)), 0, 1)

    # --- remaining ~25% -----------------------------------------------------
    m = idx["double_deduction"]
    if len(m):
        dbl = rng.random(len(m)) < 0.70             # 30% show only debit=1 (invisible)
        rows["debit_confirmation_count"][m[dbl]] = 2.0
        rows["event_count"][m] = rows["event_count"][m] + (rows["debit_confirmation_count"][m] > 1)
        rows["reconstruction_confidence"][m] = np.clip(rng.normal(0.80, 0.10, len(m)), 0, 1)

    m = idx["duplicate_transaction"]
    if len(m):
        dups = rng.integers(2, 5, len(m)).astype(float)
        rows["duplicate_event_count"][m] = dups
        # duplicate re-submissions add events to the chain
        rows["event_count"][m] = rows["event_count"][m] + dups
        # Gateway latency doubled vs. baseline (duplicate submission), generous jitter
        rows["gateway_latency_ms"][m] = _jitter(
            rng, rng.lognormal(4.45, 0.45, len(m)) * 2.0, 0.30, 15.0
        )
        rows["reconstruction_confidence"][m] = np.clip(rng.normal(0.70, 0.12, len(m)), 0, 1)

    m = idx["successful_but_unconfirmed"]
    if len(m):
        rows["has_merchant_confirmation"][m] = 0.0
        rows["has_settlement_confirmation"][m] = 0.0
        rows["gateway_latency_ms"][m] = _jitter(rng, rng.lognormal(4.9, 0.55, len(m)), 0.3, 10.0)
        rows["reconstruction_confidence"][m] = np.clip(rng.normal(0.72, 0.12, len(m)), 0, 1)

    m = idx["false_complaint"]
    if len(m):
        # Identical healthy distribution — the evidence says success. Only tiny
        # distributional differences (slightly shorter chains / slightly lower
        # reconstruction confidence) that the model will honestly fail on.
        rows["event_count"][m] = rng.choice([3, 4], len(m), p=[0.45, 0.55])
        rows["has_settlement_confirmation"][m] = (rows["event_count"][m] == 4).astype(float)
        rows["reconstruction_confidence"][m] = np.clip(rng.normal(0.90, 0.06, len(m)), 0, 1)

    m = idx["suspicious_high_retry"]
    if len(m):
        rows["retry_count"][m] = rng.integers(4, 7, len(m)).astype(float)
        rows["gateway_latency_ms"][m] = _jitter(rng, rng.lognormal(6.3, 0.55, len(m)), 0.3, 20.0)
        rows["previous_failures"][m] = rng.poisson(3.0, len(m)).astype(float)
        rows["reconstruction_confidence"][m] = np.clip(rng.normal(0.65, 0.15, len(m)), 0, 1)

    m = idx["incomplete_event_chain"]
    if len(m):
        rows["event_count"][m] = rng.choice([1, 2], len(m), p=[0.4, 0.6]).astype(float)
        rows["debit_confirmation_count"][m] = rng.choice([0.0, 1.0], len(m), p=[0.55, 0.45])
        rows["has_merchant_confirmation"][m] = 0.0
        rows["has_settlement_confirmation"][m] = 0.0
        rows["reconstruction_confidence"][m] = np.clip(rng.normal(0.40, 0.15, len(m)), 0, 1)
        rows["time_to_last_event_ms"][m] = _jitter(rng, rng.uniform(100, 2500, len(m)), 0.4, 20.0)

    return rows, scenario


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 7 — anomaly-scenario dataset generator")
    ap.add_argument("--rows", type=int, default=N_ROWS)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out", default=os.path.join(DATA_DIR, "anomaly_transactions.csv"))
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    rows, scenario = _gen(rng, args.rows)

    df = pd.DataFrame({c: rows[c] for c in FEATURE_COLUMNS})
    df["scenario"] = scenario

    # Final sanity: event_count must dominate the per-event counts it implies
    assert (df["debit_confirmation_count"] <= df["event_count"]).all()
    assert (df["duplicate_event_count"] <= df["event_count"]).all()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    df.to_csv(args.out, index=False)

    print(f"Wrote {args.out}  ({len(df):,} rows, seed={args.seed})")
    print("\nScenario distribution:")
    counts = df["scenario"].value_counts()
    for s in SCENARIOS:
        c = int(counts.get(s, 0))
        print(f"  {s:<28} {c:>7,}  ({c / len(df):6.2%})")


if __name__ == "__main__":
    main()

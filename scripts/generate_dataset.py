"""
Stage 1 — Synthetic Payment Transaction Dataset Generator
Project: AI-Powered Payment Failure Recovery & Digital Twin System
===============================================================

Generates a fully synthetic dataset of payment transactions with realistic
inter-column relationships (NOT independent columns):

  - poor network        -> higher gateway latency, higher Network Drop chance
  - high latency        -> higher Timeout and STALLED chance
  - more retries        -> higher STALLED chance
  - previous_failures   -> higher future failure risk
  - large amount        -> higher Insufficient Balance chance
  - low latency + good network -> much higher SUCCESS chance

risk_score is a noisy logistic function of many features (never a copy of a
single column), and safe_to_release follows a policy rule on top of it
(threshold + amount cap + small override noise) so it is learnable but not
a deterministic copy of risk_score.

Outputs:
  data/transactions.csv
  data/transactions.xlsx

Usage:
  python generate_dataset.py
"""

import os

import numpy as np
import pandas as pd
from faker import Faker

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
SEED = 42
N_TRANSACTIONS = 25_000
N_USERS = 5_000
N_MERCHANTS = 350
WINDOW_DAYS = 90
END_TS = pd.Timestamp("2026-10-01 23:59:59")  # fixed for reproducibility

# Recovery policy used to derive safe_to_release
AUTO_RELEASE_RISK_MAX = 0.45      # auto-release only if risk below this
AUTO_RELEASE_AMOUNT_CAP = 1500.0  # ...and amount at/below this
OVERRIDE_NOISE_RATE = 0.03        # 3% flipped either way (human overrides)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data")

Faker.seed(SEED)
rng = np.random.default_rng(SEED)
fake = Faker()

# --------------------------------------------------------------------------
# Latent entity pools (users and merchants have hidden traits that make
# rows correlated, the way real payment data is)
# --------------------------------------------------------------------------
user_risk = rng.beta(2, 6, N_USERS)                # hidden flakiness per user
user_age_days = rng.uniform(5, 2600, N_USERS)      # account age at window end
user_ids = [f"USR-{fake.uuid4()[:8]}" for _ in range(N_USERS)]

merchant_flaky = rng.beta(1.2, 12, N_MERCHANTS)    # disconnect-proneness
merchant_ids = [f"MER-{fake.uuid4()[:8]}" for _ in range(N_MERCHANTS)]

# Popularity skew: a few users/merchants dominate volume (Zipf-like)
user_pop = 1.0 / np.arange(1, N_USERS + 1) ** 0.7
user_pop /= user_pop.sum()
tx_user_idx = rng.choice(N_USERS, N_TRANSACTIONS, p=user_pop)

merchant_pop = 1.0 / np.arange(1, N_MERCHANTS + 1) ** 0.5
merchant_pop /= merchant_pop.sum()
tx_merchant_idx = rng.choice(N_MERCHANTS, N_TRANSACTIONS, p=merchant_pop)

# --------------------------------------------------------------------------
# Timestamps: 90-day window with diurnal pattern (evening peak) and quieter
# weekends
# --------------------------------------------------------------------------
day = rng.integers(0, WINDOW_DAYS, N_TRANSACTIONS)
while True:
    dates = END_TS.normalize() - pd.to_timedelta(day, unit="D")
    weekend = dates.dayofweek >= 5
    rejected = weekend & (rng.random(N_TRANSACTIONS) >= 0.78)
    if not rejected.any():
        break
    day[rejected] = rng.integers(0, WINDOW_DAYS, rejected.sum())

hour_weights = np.array(
    [2, 1, 1, 1, 1, 2, 4, 8, 14, 22, 30, 36, 40, 42, 42, 40, 38, 36, 34, 34, 32, 24, 14, 6],
    dtype=float,
)
hour_weights /= hour_weights.sum()
hour = rng.choice(24, N_TRANSACTIONS, p=hour_weights)
second_of_day = hour * 3600 + rng.integers(0, 3600, N_TRANSACTIONS)
dates = END_TS.normalize() - pd.to_timedelta(day, unit="D")
timestamp = dates + pd.to_timedelta(second_of_day, unit="s")

# --------------------------------------------------------------------------
# Transaction attributes (generated in causal order)
# --------------------------------------------------------------------------
# 1) Network quality of the connection attempt
NET_LEVELS = ["Excellent", "Good", "Fair", "Poor"]
net_idx = rng.choice(4, N_TRANSACTIONS, p=[0.32, 0.33, 0.21, 0.14])
network_quality = np.array(NET_LEVELS, dtype=object)[net_idx]

# 2) Retry count: poor network -> more retries attempted
retry_probs = {
    0: [0.85, 0.11, 0.03, 0.01],
    1: [0.72, 0.19, 0.06, 0.03],
    2: [0.55, 0.26, 0.12, 0.07],
    3: [0.40, 0.30, 0.19, 0.11],
}
retry_count = np.empty(N_TRANSACTIONS, dtype=int)
for q in range(4):
    mask = net_idx == q
    retry_count[mask] = rng.choice(4, int(mask.sum()), p=retry_probs[q])

# 3) Gateway latency: driven by network quality, inflated by retries
lat_mu = np.array([4.45, 5.05, 5.75, 6.55])  # log-scale medians ~85/156/314/700 ms
latency = rng.lognormal(lat_mu[net_idx], 0.45)
latency += retry_count * rng.uniform(80, 220, N_TRANSACTIONS) * (1 + 0.6 * net_idx)
gateway_latency_ms = np.clip(latency, 20, 12_000).round().astype(int)

# Standardized log-latency used by the hazard / risk models
z_lat = (np.log(gateway_latency_ms) - 5.3) / 0.95
net_bad = net_idx.astype(float)  # 0=Excellent .. 3=Poor

# 4) Amount: log-normal body + rare big-ticket transactions
amount = rng.lognormal(3.4, 0.95, N_TRANSACTIONS)
big_ticket = rng.random(N_TRANSACTIONS) < 0.03
amount[big_ticket] = rng.lognormal(6.6, 0.5, int(big_ticket.sum()))
amount = np.clip(amount, 1.0, 6_000.0).round(2)
z_amt = (np.log(amount) - np.log(amount).mean()) / np.log(amount).std()

# 5) Account age at transaction time
days_ago = day.astype(float)
account_age_days = np.maximum(user_age_days[tx_user_idx] - days_ago, 1).round().astype(int)
young_account = (account_age_days < 90).astype(float)

# 6) previous_failures: user's recent failure count, driven by hidden risk
prev_fail = rng.poisson(0.25 + 3.0 * user_risk[tx_user_idx] + 0.4 * young_account)
previous_failures = np.clip(prev_fail, 0, 8)

# --------------------------------------------------------------------------
# Outcome: hazard model over {SUCCESS, STALLED, 5 failure reasons}
# Each hazard rises with its causal factors; probabilities are the hazards
# normalized, so relationships emerge jointly instead of being hard-coded.
# --------------------------------------------------------------------------
poor = (net_idx == 3).astype(float)
fair = (net_idx == 2).astype(float)
m_flaky = merchant_flaky[tx_merchant_idx]

h_succ = np.exp(
    0.10 - 0.55 * z_lat - 0.35 * net_bad - 0.18 * retry_count
    - 0.30 * previous_failures - 0.25 * young_account
)
h_stall = np.exp(
    -6.80 + 0.95 * z_lat + 0.55 * retry_count + 0.35 * poor + 0.12 * previous_failures
)
h_timeout = np.exp(-7.56 + 1.30 * z_lat + 0.30 * net_bad + 0.25 * retry_count)
h_netdrop = np.exp(-6.50 + 1.50 * poor + 0.90 * fair + 0.10 * retry_count)
h_merchdisc = np.exp(-6.45 + 3.00 * m_flaky + 0.20 * retry_count)
h_insufbal = np.exp(-6.00 + 0.85 * z_amt + 0.50 * young_account + 0.20 * previous_failures)
h_gwerr = np.exp(-6.42 + 0.55 * z_lat + 0.15 * retry_count + 0.10 * net_bad)

hazards = np.vstack([h_succ, h_stall, h_timeout, h_netdrop, h_merchdisc, h_insufbal, h_gwerr])
# Diagnostic: the sampled outcome share equals the MEAN PER-ROW probability
# (not the pooled hazard-sum share, which is dominated by success-heavy rows).
probs = hazards / hazards.sum(axis=0)
print("Outcome shares (mean per-row probabilities, %):",
      dict(zip(["succ", "stall", "timeout", "netdrop", "merchdisc", "insufbal", "gwerr"],
               (probs.mean(axis=1) * 100).round(2))))
cum = np.cumsum(probs, axis=0)
outcome = (rng.random(N_TRANSACTIONS)[None, :] > cum).sum(axis=0)

OUTCOME_STATUS = ["SUCCESS", "STALLED", "FAILED", "FAILED", "FAILED", "FAILED", "FAILED"]
OUTCOME_REASON = [
    None, None,
    "Timeout", "Network Drop", "Merchant Disconnect",
    "Insufficient Balance", "Gateway Error",
]
status = np.array(OUTCOME_STATUS, dtype=object)[outcome]
failure_reason = np.array(
    [r if r is not None else np.nan for r in OUTCOME_REASON], dtype=object
)[outcome]

# --------------------------------------------------------------------------
# risk_score: noisy logistic blend of MANY features (avoid single-column copy)
# --------------------------------------------------------------------------
is_stalled = (outcome == 1).astype(float)
is_failed = (outcome >= 2).astype(float)

risk_logit = (
    -2.1
    + 2.10 * is_stalled
    + 1.50 * is_failed
    + 0.65 * z_lat
    + 0.45 * retry_count
    + 0.40 * net_bad
    + 0.30 * previous_failures
    + 0.55 * z_amt
    + 0.60 * young_account
    + rng.normal(0.0, 0.55, N_TRANSACTIONS)
)
risk_score = np.clip(1.0 / (1.0 + np.exp(-risk_logit)), 0.01, 0.99).round(3)

# --------------------------------------------------------------------------
# safe_to_release: policy rule + small override noise (not a copy of risk)
# --------------------------------------------------------------------------
safe = (risk_score < AUTO_RELEASE_RISK_MAX) & (amount <= AUTO_RELEASE_AMOUNT_CAP)
flip = rng.random(N_TRANSACTIONS) < OVERRIDE_NOISE_RATE
safe_to_release = np.where(safe ^ flip, "TRUE", "FALSE")

# --------------------------------------------------------------------------
# Assemble
# --------------------------------------------------------------------------
df = pd.DataFrame(
    {
        "transaction_id": [f"TXN-{i:07d}" for i in range(1, N_TRANSACTIONS + 1)],
        "timestamp": timestamp,
        "user_id": np.array(user_ids, dtype=object)[tx_user_idx],
        "merchant_id": np.array(merchant_ids, dtype=object)[tx_merchant_idx],
        "amount": amount,
        "gateway_latency_ms": gateway_latency_ms,
        "retry_count": retry_count,
        "network_quality": network_quality,
        "previous_failures": previous_failures,
        "account_age_days": account_age_days,
        "status": status,
        "failure_reason": failure_reason,
        "risk_score": risk_score,
        "safe_to_release": safe_to_release,
    }
).sort_values("timestamp", kind="mergesort").reset_index(drop=True)

os.makedirs(DATA_DIR, exist_ok=True)
csv_path = os.path.join(DATA_DIR, "transactions.csv")
xlsx_path = os.path.join(DATA_DIR, "transactions.xlsx")
df.to_csv(csv_path, index=False)
df.to_excel(xlsx_path, index=False)

# --------------------------------------------------------------------------
# Statistics / validation report
# --------------------------------------------------------------------------
pct = lambda s: (s.value_counts(normalize=True) * 100).round(2)

print("=" * 70)
print("STAGE 1 — SYNTHETIC PAYMENT DATASET GENERATION REPORT")
print("=" * 70)

print(f"\n[1] Transactions generated : {len(df):,}")
print(f"    Users / Merchants      : {df.user_id.nunique():,} / {df.merchant_id.nunique():,}")
print(f"    Time window            : {df.timestamp.min()}  ->  {df.timestamp.max()}")

print("\n[2] Status distribution:")
for k, v in pct(df.status).items():
    print(f"    {k:<10} {df.status.value_counts()[k]:>7,}  ({v}%)")

print("\n[3] Failure-reason distribution (FAILED rows only):")
fr = df.failure_reason.dropna()
for k, v in fr.value_counts().items():
    print(f"    {k:<22} {v:>6,}  ({v / len(fr) * 100:.1f}% of failures)")

print("\n[4] Missing values per column:")
for col in df.columns:
    n = df[col].isna().sum()
    print(f"    {col:<20} {n:>7,}  ({n / len(df) * 100:.1f}%)")

print(f"\n[5] Duplicate transaction IDs: {df.transaction_id.duplicated().sum()}")

print("\n[6] Amount statistics ($):")
print(df.amount.describe().round(2).to_string())

print("\n[7] Gateway latency statistics (ms):")
print(df.gateway_latency_ms.describe().round(1).to_string())

print("\n[8] Risk-score distribution:")
print(f"    mean={df.risk_score.mean():.3f}  std={df.risk_score.std():.3f}  "
      f"min={df.risk_score.min():.3f}  max={df.risk_score.max():.3f}")
bins = [0, 0.2, 0.4, 0.6, 0.8, 1.0]
labels = ["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"]
band = pd.cut(df.risk_score, bins=bins, labels=labels, include_lowest=True)
for k, v in band.value_counts().sort_index().items():
    print(f"    {k:<9} {v:>7,}  ({v / len(df) * 100:.1f}%)")

print("\n[9] safe_to_release distribution:")
for k, v in pct(df.safe_to_release).items():
    print(f"    {k:<6} {df.safe_to_release.value_counts()[k]:>7,}  ({v}%)")

# --- relationship sanity checks (prove the generative links exist) ---------
print("\n[10] Relationship sanity checks:")
print("    a) SUCCESS rate by network quality (poor network -> less success):")
t = df.groupby("network_quality")["status"].apply(lambda s: (s == "SUCCESS").mean() * 100)
print(t.reindex(NET_LEVELS).round(1).to_string().replace("\n", "\n     "))

print("    b) Mean latency by network quality (poor network -> higher latency):")
t = df.groupby("network_quality")["gateway_latency_ms"].mean()
print(t.reindex(NET_LEVELS).round(0).to_string().replace("\n", "\n     "))

print("    c) STALLED rate by retry count (more retries -> more stalls):")
t = df.groupby("retry_count")["status"].apply(lambda s: (s == "STALLED").mean() * 100)
print(t.round(2).to_string().replace("\n", "\n     "))

print("    d) Insufficient-Balance share by amount quartile (bigger -> riskier):")
aq = pd.qcut(df.amount, 4, labels=["Q1(low)", "Q2", "Q3", "Q4(high)"])
t = df.groupby(aq, observed=True)["failure_reason"].apply(
    lambda s: (s == "Insufficient Balance").sum() / len(s) * 100
)
print(t.round(2).to_string().replace("\n", "\n     "))

print("    e) Failure rate by previous_failures (history -> future risk):")
t = df.groupby("previous_failures")["status"].apply(lambda s: (s != "SUCCESS").mean() * 100)
print(t.round(1).to_string().replace("\n", "\n     "))

print("    f) Mean risk_score by status:")
print(df.groupby("status")["risk_score"].mean().round(3).to_string().replace("\n", "\n     "))

print("    g) safe_to_release=TRUE rate by status:")
t = df.groupby("status")["safe_to_release"].apply(lambda s: (s == "TRUE").mean() * 100)
print(t.round(1).to_string().replace("\n", "\n     "))

print("\nSample rows:")
print(df.head(3).to_string())

print(f"\nSaved:\n  {csv_path}\n  {xlsx_path}")

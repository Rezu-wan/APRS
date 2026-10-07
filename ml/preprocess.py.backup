"""
ml/preprocess.py — schema, leakage policy, and sklearn pipelines for Stage 2.

This module is the SINGLE source of truth for:
  * which columns are features vs targets for each ML task
  * how categorical / numerical features are transformed
  * how the XGBoost models are wrapped in reproducible sklearn Pipelines

LEAKAGE POLICY (decided up front, enforced by the feature lists below):

  Stage 1 dataset columns:
    transaction_id, timestamp, user_id, merchant_id          -> identifiers/time
    amount, gateway_latency_ms, retry_count, network_quality,
    previous_failures, account_age_days                       -> base features
    status, failure_reason                                    -> outcome columns
    risk_score, safe_to_release                               -> Stage 1 targets

  Task 1 — Transaction outcome classification (target: outcome = failure_reason
  with SUCCESS/STALLED filled in). `status` is EXCLUDED as a feature because it
  is a direct co-product of the same generative draw as the target
  (SUCCESS <=> outcome == SUCCESS, STALLED <=> outcome == STALLED) — including
  it would hand the model the answer. `failure_reason` IS the target.

  Task 2 — Recovery safety classification (target: safe_to_release).
  `risk_score` and `safe_to_release` are excluded (risk_score is the quantity
  the Stage 1 policy thresholded to create the label — using it would be pure
  leakage). `status` and `failure_reason` ARE legitimate: at recovery-decision
  time in a real system the outcome is already observed.

  Task 3 — Risk-score regression (target: risk_score). Same features as Task 2;
  risk_score itself excluded.

  Never used as features in the baseline: transaction_id, timestamp, user_id,
  merchant_id (high-cardinality identifiers; no target encoding at baseline).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from xgboost import XGBClassifier, XGBRegressor

RANDOM_STATE = 42
TEST_SIZE = 0.20

DATA_PATH_DEFAULT = "data/transactions.csv"

# ---------------------------------------------------------------------------
# Feature schema
# ---------------------------------------------------------------------------
NUMERIC_FEATURES = [
    "amount",
    "gateway_latency_ms",
    "retry_count",
    "previous_failures",
    "account_age_days",
]

# Base feature available to every task
CATEGORICAL_FEATURES_BASE = ["network_quality"]

# Outcome columns — legitimate features ONLY for tasks whose target is not
# derived from them (Tasks 2 and 3; NOT Task 1).
CATEGORICAL_FEATURES_OUTCOME = ["status", "failure_reason"]

FEATURES_TASK1 = NUMERIC_FEATURES + CATEGORICAL_FEATURES_BASE
FEATURES_TASK2 = NUMERIC_FEATURES + CATEGORICAL_FEATURES_BASE + CATEGORICAL_FEATURES_OUTCOME
FEATURES_TASK3 = FEATURES_TASK2  # same rationale as Task 2

# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------
OUTCOME_CLASSES = [
    "SUCCESS", "STALLED",
    "Timeout", "Network Drop", "Merchant Disconnect",
    "Insufficient Balance", "Gateway Error",
]
SUCCESS_LABEL = "SUCCESS"
STALLED_LABEL = "STALLED"
NO_FAILURE_LABEL = "NoFailure"  # imputed value for NULL failure_reason

# safe_to_release is stored as TRUE/FALSE strings in the CSV
SAFE_POSITIVE = "TRUE"   # positive class = safe to auto-release
SAFE_NEGATIVE = "FALSE"


def make_outcome_target(df: pd.DataFrame) -> pd.Series:
    """Task 1 target: 7-class transaction outcome.

    CHOICE & WHY: Stage 1 leaves failure_reason NULL for both SUCCESS and
    STALLED rows. Instead of dropping ~93% of the data (train-on-failures-only)
    or collapsing STALLED into SUCCESS, we fill the NULLs: SUCCESS -> 'SUCCESS',
    STALLED -> 'STALLED'. This yields one 7-class model that serves both as an
    outcome classifier (is this transaction fine / stalled / failed-and-why?)
    and as the failure categorizer the recovery orchestrator needs. STALLED is
    kept separate because the recovery flow for a stall (watch, then release)
    differs from a hard failure (compensate / notify).
    """
    outcome = df["failure_reason"].astype("object").copy()
    outcome[df["status"] == SUCCESS_LABEL] = SUCCESS_LABEL
    outcome[outcome.isna() | (outcome.astype(str) == "nan")] = STALLED_LABEL
    return pd.Categorical(outcome, categories=OUTCOME_CLASSES)


def make_recovery_target(df: pd.DataFrame) -> pd.Series:
    """Task 2 target: safe_to_release as 1/0 (1 = TRUE = safe to auto-release)."""
    return (df["safe_to_release"].astype(str).str.upper() == SAFE_POSITIVE).astype(int)


def make_risk_target(df: pd.DataFrame) -> pd.Series:
    """Task 3 target: Stage 1 synthetic risk_score as the reference signal."""
    return df["risk_score"].astype(float)


# ---------------------------------------------------------------------------
# Transformers / pipelines
# ---------------------------------------------------------------------------
def build_preprocessor(
    categorical_features: list[str],
    numeric_features: list[str] | None = None,
) -> ColumnTransformer:
    """Impute + one-hot encode categoricals; pass numerics through.

    - OneHotEncoder, NOT ordinal/label encoding: network_quality et al. carry
      no true integer ordering, and arbitrary codes would invent false
      ordinal relationships for the tree splits.
    - handle_unknown='ignore': unseen categories at inference (FastAPI) become
      an all-zero block instead of crashing.
    - Imputers make the pipeline robust to null fields in API payloads
      (e.g. failure_reason=null for a success transaction).
    - Trees need no feature scaling, so numerics are passed through.
    """
    numeric_features = NUMERIC_FEATURES if numeric_features is None else numeric_features
    preprocessor = ColumnTransformer(
        transformers=[
            (
                "num",
                SimpleImputer(strategy="median"),
                numeric_features,
            ),
            (
                "cat",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="constant", fill_value=NO_FAILURE_LABEL)),
                        ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
                    ]
                ),
                categorical_features,
            ),
        ],
        remainder="drop",
        verbose_feature_names_out=True,
    )
    return preprocessor


XGB_BASE_PARAMS = {
    "n_estimators": 300,
    "max_depth": 6,
    "learning_rate": 0.1,
    "subsample": 0.9,
    "colsample_bytree": 0.9,
    "tree_method": "hist",
    "random_state": RANDOM_STATE,
    "n_jobs": -1,
}


def build_pipeline(
    task: str,
    numeric_features: list[str] | None = None,
    xgb_params: dict | None = None,
) -> Pipeline:
    """Full train->predict pipeline (preprocessor + XGBoost) for a task.

    Bundling preprocessing and model into ONE sklearn Pipeline (and saving that
    single object) guarantees inference applies byte-for-byte the same
    transformations that were fitted during training. `numeric_features` /
    `xgb_params` overrides exist for diagnostics and tuning experiments; the
    saved production pipelines always use the defaults.
    """
    params = dict(XGB_BASE_PARAMS)
    if xgb_params:
        params.update(xgb_params)
    if task == "failure":
        model = XGBClassifier(eval_metric="mlogloss", **params)
        pre = build_preprocessor(CATEGORICAL_FEATURES_BASE, numeric_features)
    elif task == "recovery":
        model = XGBClassifier(eval_metric="logloss", **params)
        pre = build_preprocessor(
            CATEGORICAL_FEATURES_BASE + CATEGORICAL_FEATURES_OUTCOME, numeric_features
        )
    elif task == "risk":
        model = XGBRegressor(
            objective="reg:squarederror", eval_metric="rmse",
            **dict(params, n_estimators=400, learning_rate=0.08),
        )
        pre = build_preprocessor(
            CATEGORICAL_FEATURES_BASE + CATEGORICAL_FEATURES_OUTCOME, numeric_features
        )
    else:
        raise ValueError(f"unknown task: {task!r} (expected failure|recovery|risk)")
    return Pipeline(steps=[("preprocessor", pre), ("model", model)])


def extract_original_feature_importance(pipeline: Pipeline) -> dict[str, float]:
    """Aggregate XGBoost importances from one-hot columns back to source columns.

    Returns {original_column: summed importance}, which is far more readable
    than 15+ one-hot dummy entries in reports.
    """
    pre = pipeline.named_steps["preprocessor"]
    mdl = pipeline.named_steps["model"]

    # Categorical columns actually used by this pipeline (from the fitted
    # ColumnTransformer), longest-first so prefix matching is unambiguous.
    cat_cols = sorted((c for name, _, c in pre.transformers_ if name == "cat" for c in c),
                      key=len, reverse=True)

    def original_col(out_name: str) -> str:
        if out_name.startswith("num__"):
            return out_name[len("num__"):]
        rest = out_name[len("cat__"):]
        for col in cat_cols:
            if rest.startswith(col + "_"):
                return col
        return rest

    grouped: dict[str, float] = {}
    for out_name, imp in zip(pre.get_feature_names_out(), mdl.feature_importances_):
        col = original_col(out_name)
        grouped[col] = grouped.get(col, 0.0) + float(imp)
    return dict(sorted(grouped.items(), key=lambda kv: kv[1], reverse=True))


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_dataset(path: str = DATA_PATH_DEFAULT) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def feature_matrix(df: pd.DataFrame, task: str) -> pd.DataFrame:
    """Select the task's feature columns (the ONLY place features are chosen)."""
    features = {"failure": FEATURES_TASK1, "recovery": FEATURES_TASK2, "risk": FEATURES_TASK3}[task]
    missing = [c for c in features if c not in df.columns]
    if missing:
        raise KeyError(f"dataset missing required feature columns: {missing}")
    return df[features].copy()

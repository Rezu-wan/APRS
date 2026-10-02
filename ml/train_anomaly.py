"""
ml/train_anomaly.py — Stage 7 dedicated anomaly-scenario classifier training.

Trains ONE multi-class XGBoost classifier on data/anomaly_transactions.csv to
predict which payment SCENARIO produced a transaction's evidence (13 classes).
Follows the Stage 2 pattern: a single sklearn Pipeline (imputers + OneHot +
XGBClassifier) is saved whole, so inference applies exactly the fitted
transformations.

Artifacts written (never overwrites Stage 2 models):
  models/anomaly_classifier.joblib      — the full fitted Pipeline
  models/anomaly_preprocessors.joblib   — meta: feature columns, class order,
                                          model version, training metrics

Usage (from the project root):
    python -m ml.train_anomaly
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

import joblib
import numpy as np
import pandas as pd
import sklearn
import xgboost
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    f1_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from xgboost import XGBClassifier

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ml.predict_anomaly import ANOMALY_MODEL_VERSION, FEATURE_COLUMNS  # type: ignore
else:
    from ml.predict_anomaly import ANOMALY_MODEL_VERSION, FEATURE_COLUMNS

RANDOM_STATE = 2077
CATEGORICAL_FEATURES = ["network_quality"]

SCENARIO_CLASSES = [
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


def build_anomaly_pipeline() -> Pipeline:
    """Stage 2-style pipeline: median-impute numerics, constant-impute +
    one-hot encode categoricals (handle_unknown=ignore), modest-depth XGB."""
    preprocessor = ColumnTransformer(
        transformers=[
            ("num", SimpleImputer(strategy="median"),
             [c for c in FEATURE_COLUMNS if c not in CATEGORICAL_FEATURES]),
            ("cat", Pipeline(steps=[
                ("imputer", SimpleImputer(strategy="constant", fill_value="Unknown")),
                ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
            ]), CATEGORICAL_FEATURES),
        ],
        remainder="drop",
    )
    model = XGBClassifier(
        objective="multi:softprob",
        num_class=len(SCENARIO_CLASSES),
        eval_metric="mlogloss",
        n_estimators=300,
        max_depth=4,          # modest depth: overlap-heavy data overfits fast
        learning_rate=0.1,
        subsample=0.9,
        colsample_bytree=0.9,
        tree_method="hist",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    return Pipeline(steps=[("preprocessor", preprocessor), ("model", model)])


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 7 — train anomaly-scenario classifier")
    ap.add_argument("--data", default="data/anomaly_transactions.csv")
    ap.add_argument("--models-dir", default="models")
    args = ap.parse_args()

    os.makedirs(args.models_dir, exist_ok=True)

    print("=" * 72)
    print("STAGE 7 — ANOMALY-SCENARIO CLASSIFIER TRAINING")
    print("=" * 72)
    df = pd.read_csv(args.data)
    print(f"dataset : {args.data}  ({len(df):,} rows, {df['scenario'].nunique()} classes)")

    missing = [c for c in FEATURE_COLUMNS + ["scenario"] if c not in df.columns]
    if missing:
        raise KeyError(f"dataset missing required columns: {missing}")

    X = df[FEATURE_COLUMNS].copy()
    # XGBoost needs integer-encoded labels; pd.Categorical guarantees the
    # class order == SCENARIO_CLASSES (stored in the meta artifact) so
    # predict_anomaly can map class indices back to names.
    y = np.asarray(pd.Categorical(df["scenario"].astype(str), categories=SCENARIO_CLASSES).codes)
    assert (y >= 0).all(), "dataset contains scenario labels outside SCENARIO_CLASSES"

    # Stratified 70/15/15 (seed 2077): carve test first, then val from the rest
    idx_tr, idx_te = train_test_split(
        np.arange(len(df)), test_size=0.15, random_state=RANDOM_STATE, stratify=y
    )
    idx_tr, idx_val = train_test_split(
        idx_tr, test_size=0.15 / 0.70, random_state=RANDOM_STATE, stratify=y[idx_tr]
    )
    print(f"splits  : train={len(idx_tr):,}  val={len(idx_val):,}  test={len(idx_te):,}")

    pipe = build_anomaly_pipeline()
    pipe.fit(X.iloc[idx_tr], y[idx_tr])

    # Validation metrics (the split the engine agent may sanity-check against)
    val_pred = pipe.predict(X.iloc[idx_val])
    test_pred = pipe.predict(X.iloc[idx_te])
    test_proba = pipe.predict_proba(X.iloc[idx_te])

    metrics = {
        "val_f1_macro": round(float(f1_score(y[idx_val], val_pred, average="macro", zero_division=0)), 4),
        "val_f1_weighted": round(float(f1_score(y[idx_val], val_pred, average="weighted", zero_division=0)), 4),
        "test_f1_macro": round(float(f1_score(y[idx_te], test_pred, average="macro", zero_division=0)), 4),
        "test_f1_weighted": round(float(f1_score(y[idx_te], test_pred, average="weighted", zero_division=0)), 4),
        "test_accuracy": round(float(accuracy_score(y[idx_te], test_pred)), 4),
        "test_roc_auc_ovr_weighted": round(
            float(roc_auc_score(y[idx_te], test_proba, multi_class="ovr", average="weighted")), 4),
    }
    print("\n--- metrics ---")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    print("\n--- classification report (test) ---")
    print(classification_report(y[idx_te], test_pred, labels=range(len(SCENARIO_CLASSES)),
                                target_names=SCENARIO_CLASSES, zero_division=0))

    # Artifacts
    classifier_path = os.path.join(args.models_dir, "anomaly_classifier.joblib")
    meta_path = os.path.join(args.models_dir, "anomaly_preprocessors.joblib")
    joblib.dump(pipe, classifier_path)
    joblib.dump(
        {
            "model_version": ANOMALY_MODEL_VERSION,
            "feature_columns": FEATURE_COLUMNS,
            "scenario_classes": SCENARIO_CLASSES,
            "categorical_features": CATEGORICAL_FEATURES,
            "random_state": RANDOM_STATE,
            "data_path": args.data,
            "n_rows": int(len(df)),
            "split": "stratified 70/15/15",
            "metrics": metrics,
            "trained_at": dt.datetime.now().isoformat(timespec="seconds"),
            "versions": {
                "scikit-learn": sklearn.__version__,
                "xgboost": xgboost.__version__,
                "pandas": pd.__version__,
                "numpy": np.__version__,
            },
        },
        meta_path,
    )
    print(f"Saved: {classifier_path}")
    print(f"Saved: {meta_path}")


if __name__ == "__main__":
    main()

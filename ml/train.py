"""
ml/train.py — Stage 2 training entrypoint.

Trains three baseline models on the Stage 1 synthetic dataset and writes
models/, reports/metrics.json and report plots:

  Task 1  failure   — XGBClassifier, 7-class transaction outcome
                      (SUCCESS / STALLED + 5 failure reasons)
  Task 2  recovery  — XGBClassifier, binary safe_to_release
  Task 3  risk      — XGBRegressor,  reference risk_score in [0, 1]

Split strategies (both evaluated; stratified model is the one saved):
  * stratified    — random 80/20, stratified by target, seed 42 (headline numbers)
  * chronological — first 80% by timestamp train, last 20% test (deployment-like
                    sanity check; on stationary synthetic data it should agree
                    with stratified — if it doesn't, suspect temporal leakage)

Usage (from the project root):
    python -m ml.train                     # both splits, default paths
    python -m ml.train --split stratified
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import joblib
import numpy as np
import pandas as pd
import sklearn
import xgboost
from sklearn.metrics import f1_score
from sklearn.model_selection import train_test_split
from sklearn.utils.class_weight import compute_class_weight

if __package__ in (None, ""):  # allow `python ml/train.py` too
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ml.preprocess import (  # type: ignore
        FEATURES_TASK1, FEATURES_TASK2, FEATURES_TASK3, NUMERIC_FEATURES,
        OUTCOME_CLASSES, RANDOM_STATE, TEST_SIZE, build_pipeline,
        extract_original_feature_importance, feature_matrix, load_dataset,
        make_outcome_target, make_recovery_target, make_risk_target,
    )
    from ml.evaluate import (  # type: ignore
        evaluate_classifier, evaluate_regressor, plot_confusion_matrix,
        plot_feature_importance,
    )
else:
    from ml.preprocess import (
        FEATURES_TASK1, FEATURES_TASK2, FEATURES_TASK3, NUMERIC_FEATURES,
        OUTCOME_CLASSES, RANDOM_STATE, TEST_SIZE, build_pipeline,
        extract_original_feature_importance, feature_matrix, load_dataset,
        make_outcome_target, make_recovery_target, make_risk_target,
    )
    from ml.evaluate import (
        evaluate_classifier, evaluate_regressor, plot_confusion_matrix,
        plot_feature_importance,
    )

FAILURE_LABELS = OUTCOME_CLASSES
RECOVERY_LABELS = [0, 1]  # 0 = FALSE (manual review), 1 = TRUE (auto-release)


def split_data(
    df: pd.DataFrame, y: pd.Series | np.ndarray, mode: str, stratify: bool = True
) -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray, np.ndarray]:
    y = np.asarray(y)

    if mode == "stratified":
        idx = np.arange(len(df))

        # Classification targets can be stratified.
        # Continuous regression targets such as risk_score cannot.
        stratify_y = y if stratify else None

        idx_tr, idx_te = train_test_split(
            idx,
            test_size=TEST_SIZE,
            random_state=RANDOM_STATE,
            stratify=stratify_y,
        )
    elif mode == "chronological":
        order = df["timestamp"].sort_values().index.to_numpy()
        n_tr = int(len(order) * (1 - TEST_SIZE))
        idx_tr, idx_te = order[:n_tr], order[n_tr:]
    else:
        raise ValueError(f"unknown split mode: {mode!r}")
    return df.iloc[idx_tr], df.iloc[idx_te], y[idx_tr], y[idx_te]


def balanced_weights(y: np.ndarray) -> np.ndarray:
    classes = np.unique(y)
    w = compute_class_weight(class_weight="balanced", classes=classes, y=y)
    return dict(zip(classes, w))


def train_task(
    task: str,
    df: pd.DataFrame,
    y: np.ndarray,
    labels: list,
    split_modes: list[str],
    reports_dir: str,
) -> dict:
    """Train/evaluate one task on each split mode; return its report block."""
    X = feature_matrix(df, task)
    results: dict = {"target": TASK_META[task]["target"], "features": list(X.columns)}

    for mode in split_modes:
        X_tr, X_te, y_tr, y_te = split_data(
    df,
    y,
    mode,
    stratify=(task != "risk"),
)
        pipe = build_pipeline(task)

        fit_kwargs: dict = {}
        if task == "failure":  # imbalance: up-weight rare failure reasons
            weight_map = balanced_weights(y_tr)
            fit_kwargs["model__sample_weight"] = np.array([weight_map[v] for v in y_tr])

        pipe.fit(X_tr, y_tr, **fit_kwargs)
        y_pred = pipe.predict(X_te)
        y_proba = pipe.predict_proba(X_te) if hasattr(pipe, "predict_proba") else None

        if task == "risk":
            metrics = evaluate_regressor(y_te, y_pred)
        else:
            metrics = evaluate_classifier(y_te, y_pred, y_proba, labels)
        results.setdefault("splits", {})[mode] = metrics

        if mode == "stratified":  # headline artifacts come from the stratified run
            results["model"] = pipe
            grouped = extract_original_feature_importance(pipe)
            results["feature_importance"] = grouped
            if task == "failure":
                plot_confusion_matrix(
                    np.array(metrics["confusion_matrix"]), labels,
                    os.path.join(reports_dir, "confusion_matrix_failure.png"),
                    "Task 1 — Transaction outcome classifier (stratified test set)",
                )
            elif task == "recovery":
                plot_confusion_matrix(
                    np.array(metrics["confusion_matrix"]),
                    ["FALSE (manual review)", "TRUE (auto-release)"],
                    os.path.join(reports_dir, "confusion_matrix_recovery.png"),
                    "Task 2 — Recovery safety classifier (stratified test set)",
                )
            plot_feature_importance(
                grouped,
                os.path.join(reports_dir, f"feature_importance_{task}.png"),
                f"XGBoost feature importance — {TASK_META[task]['title']}",
            )

    return results


TASK_META = {
    "failure": {
        "target": "outcome (failure_reason + SUCCESS/STALLED)",
        "title": "Task 1 failure/outcome classifier",
        "labels": FAILURE_LABELS,
        "target_fn": lambda df: np.asarray(make_outcome_target(df).codes),
    },
    "recovery": {
        "target": "safe_to_release (1 = TRUE)",
        "title": "Task 2 recovery safety classifier",
        "labels": RECOVERY_LABELS,
        "target_fn": lambda df: np.asarray(make_recovery_target(df)),
    },
    "risk": {
        "target": "risk_score (regression, reference signal)",
        "title": "Task 3 risk-score regressor",
        "labels": None,
        "target_fn": lambda df: np.asarray(make_risk_target(df)),
    },
}


def leakage_diagnostic(df: pd.DataFrame, reports: dict) -> dict:
    """Demonstrate WHY risk_score must stay out of Task 2's features.

    Trains a throwaway recovery classifier WITH risk_score included. If score
    jumps to ~perfect, that quantifies the leakage the real pipeline avoids.
    """
    X = df[FEATURES_TASK2 + ["risk_score"]].copy()
    y = np.asarray(make_recovery_target(df))
    idx_tr, idx_te = train_test_split(
        np.arange(len(df)), test_size=TEST_SIZE, random_state=RANDOM_STATE, stratify=y
    )
    # risk_score must be registered as an extra NUMERIC feature, otherwise the
    # ColumnTransformer would silently remainder-drop it and this diagnostic
    # would measure nothing.
    pipe = build_pipeline("recovery", numeric_features=NUMERIC_FEATURES + ["risk_score"])
    pipe.fit(X.iloc[idx_tr], y[idx_tr])
    y_pred = pipe.predict(X.iloc[idx_te])
    clean = reports["recovery"]["splits"]["stratified"]
    return {
        "note": "DIAGNOSTIC ONLY — this variant is NOT saved; risk_score is banned "
                "from Task 2 features because Stage 1 created safe_to_release by "
                "thresholding risk_score.",
        "accuracy_with_risk_score": round(float((y_pred == y[idx_te]).mean()), 4),
        "accuracy_clean_pipeline": clean["accuracy"],
        "f1_macro_with_risk_score": round(float(f1_score(y[idx_te], y_pred, average="macro")), 4),
        "f1_macro_clean_pipeline": clean["f1_macro"],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 2 — train baseline ML models")
    ap.add_argument("--data", default="data/transactions.csv")
    ap.add_argument("--models-dir", default="models")
    ap.add_argument("--reports-dir", default="reports")
    ap.add_argument("--split", choices=["stratified", "chronological", "both"], default="both")
    args = ap.parse_args()

    os.makedirs(args.models_dir, exist_ok=True)
    os.makedirs(args.reports_dir, exist_ok=True)

    df = load_dataset(args.data)
    split_modes = ["stratified", "chronological"] if args.split == "both" else [args.split]

    print("=" * 72)
    print("STAGE 2 — ML ENGINE TRAINING")
    print("=" * 72)
    print(f"dataset        : {args.data}  ({len(df):,} rows)")
    print(f"time window    : {df.timestamp.min()}  ->  {df.timestamp.max()}")
    print(f"split modes    : {split_modes}   (seed={RANDOM_STATE}, test={TEST_SIZE:.0%})")
    print(f"sklearn/xgboost: {sklearn.__version__} / {xgboost.__version__}")

    report: dict = {
        "meta": {
            "data_path": args.data,
            "n_rows": int(len(df)),
            "time_min": str(df.timestamp.min()),
            "time_max": str(df.timestamp.max()),
            "random_state": RANDOM_STATE,
            "test_size": TEST_SIZE,
            "split_modes": split_modes,
            "versions": {
                "scikit-learn": sklearn.__version__,
                "xgboost": xgboost.__version__,
                "pandas": pd.__version__,
                "numpy": np.__version__,
            },
            "trained_at": dt.datetime.now().isoformat(timespec="seconds"),
        },
        "tasks": {},
    }

    model_files = {
        "failure": "failure_classifier.joblib",
        "recovery": "recovery_classifier.joblib",
        "risk": "risk_regressor.joblib",
    }

    for task in ("failure", "recovery", "risk"):
        meta = TASK_META[task]
        print(f"\n--- {meta['title']} ---")
        y = meta["target_fn"](df)
        res = train_task(task, df, y, meta["labels"], split_modes, args.reports_dir)

        # Saved artifact = pipeline fitted on the stratified split
        joblib.dump(res.pop("model"), os.path.join(args.models_dir, model_files[task]))
        report["tasks"][task] = res

        for mode in split_modes:
            m = res["splits"][mode]
            if task == "risk":
                print(f"  [{mode:>13}] R2={m['r2']:.4f}  MAE={m['mae']:.4f}  "
                      f"RMSE={m['rmse']:.4f}  (naive-MAE {m['mae_naive_mean_baseline']:.4f})")
            else:
                extra = (f"  ROC-AUC={m['roc_auc']:.4f}  PR-AUC={m['pr_auc']:.4f}"
                         if "roc_auc" in m else
                         f"  ROC-AUC(ovr)={m['roc_auc_ovr_weighted']:.4f}")
                print(f"  [{mode:>13}] acc={m['accuracy']:.4f}  P={m['precision']:.4f}  "
                      f"R={m['recall']:.4f}  F1={m['f1']:.4f}  F1w={m['f1_weighted']:.4f}{extra}")

        if task == "risk":
            # attribute importance for the regressor as well
            print("  feature importance:", {k: round(v, 3) for k, v in
                                            res["feature_importance"].items()})
        else:
            print("  feature importance:", {k: round(v, 3) for k, v in
                                            res["feature_importance"].items()})

    print("\n--- Leakage diagnostic (not saved) ---")
    diag = leakage_diagnostic(df, report["tasks"])
    report["leakage_diagnostic"] = diag
    print(f"  recovery acc WITH risk_score leaked in : {diag['accuracy_with_risk_score']:.4f}"
          f"  (F1-macro {diag['f1_macro_with_risk_score']:.4f})")
    print(f"  recovery acc clean pipeline             : {diag['accuracy_clean_pipeline']:.4f}"
          f"  (F1-macro {diag['f1_macro_clean_pipeline']:.4f})")

    # Shared preprocessing metadata so inference/FastAPI can introspect the schema
    joblib.dump(
        {
            "tasks": {
                name: {"features": meta_res["features"], "target": meta_res["target"]}
                for name, meta_res in report["tasks"].items()
            },
            "numeric_features": ["amount", "gateway_latency_ms", "retry_count",
                                 "previous_failures", "account_age_days"],
            "outcome_classes": OUTCOME_CLASSES,
            "split_strategy_saved": "stratified",
            "random_state": RANDOM_STATE,
            "meta": report["meta"],
        },
        os.path.join(args.models_dir, "preprocessors.joblib"),
    )

    metrics_path = os.path.join(args.reports_dir, "metrics.json")
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\nSaved models :")
    for f_ in model_files.values():
        print(f"  {os.path.join(args.models_dir, f_)}")
    print(f"  {os.path.join(args.models_dir, 'preprocessors.joblib')}")
    print(f"Metrics      : {metrics_path}")
    print("Reports      : confusion_matrix_*.png, feature_importance_*.png")


if __name__ == "__main__":
    main()

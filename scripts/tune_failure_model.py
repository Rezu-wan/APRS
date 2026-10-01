"""
scripts/tune_failure_model.py — hyperparameter selection for the Task 1
outcome classifier (baseline tuning; no data manipulation).

Protocol (leakage-safe):
  1. hold out the SAME stratified 20% test set used by ml.train (seed 42)
  2. carve a stratified validation slice from the remaining 80%
  3. select hyperparameters by validation macro-F1
  4. refit the winner on train+validation and report test metrics ONCE

Usage:  python scripts/tune_failure_model.py
"""

import itertools
import os
import sys

import numpy as np
from sklearn.metrics import f1_score
from sklearn.model_selection import train_test_split
from sklearn.utils.class_weight import compute_class_weight

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ml.preprocess import (  # noqa: E402
    RANDOM_STATE, TEST_SIZE, build_pipeline, feature_matrix, load_dataset,
    make_outcome_target,
)


def weights_for(y: np.ndarray, scheme: str) -> np.ndarray | None:
    if scheme == "none":
        return None
    classes = np.unique(y)
    w = compute_class_weight("balanced", classes=classes, y=y)
    if scheme == "sqrt":
        w = np.sqrt(w)
    return dict(zip(classes, w))


def main() -> None:
    df = load_dataset("data/transactions.csv")
    y = np.asarray(make_outcome_target(df).codes)

    idx_all = np.arange(len(df))
    idx_trval, idx_te = train_test_split(
        idx_all, test_size=TEST_SIZE, random_state=RANDOM_STATE, stratify=y
    )
    idx_tr, idx_val = train_test_split(
        idx_trval, test_size=0.20, random_state=RANDOM_STATE,
        stratify=y[idx_trval],
    )
    print(f"train={len(idx_tr):,}  val={len(idx_val):,}  test={len(idx_te):,} (untouched)")

    grid = list(itertools.product(
        [6, 8, 10],            # max_depth
        [300, 600],            # n_estimators
        ["balanced", "sqrt", "none"],  # class-weight scheme
    ))
    rows = []
    for i, (depth, n_est, scheme) in enumerate(grid, 1):
        params = {
            "max_depth": depth,
            "n_estimators": n_est,
            "min_child_weight": 1 if depth >= 8 else 2,
        }
        pipe = build_pipeline("failure", xgb_params=params)
        w_map = weights_for(y[idx_tr], scheme)
        fit_kw = {}
        if w_map is not None:
            fit_kw["model__sample_weight"] = np.array([w_map[v] for v in y[idx_tr]])
        pipe.fit(df.iloc[idx_tr], y[idx_tr], **fit_kw)
        pred = pipe.predict(df.iloc[idx_val])
        f1m = f1_score(y[idx_val], pred, average="macro", zero_division=0)
        rows.append((f1m, depth, n_est, scheme))
        print(f"[{i:>2}/{len(grid)}] depth={depth:<2} n_est={n_est:<3} w={scheme:<8} "
              f"-> val macro-F1={f1m:.4f}")

    rows.sort(reverse=True)
    best_f1, bd, bn, bs = rows[0]
    print(f"\nWINNER: depth={bd} n_estimators={bn} weights={bs} (val macro-F1 {best_f1:.4f})")

    params = {"max_depth": bd, "n_estimators": bn,
              "min_child_weight": 1 if bd >= 8 else 2}
    pipe = build_pipeline("failure", xgb_params=params)
    w_map = weights_for(y[idx_trval], bs)
    fit_kw = {}
    if w_map is not None:
        fit_kw["model__sample_weight"] = np.array([w_map[v] for v in y[idx_trval]])
    pipe.fit(df.iloc[idx_trval], y[idx_trval], **fit_kw)
    pred = pipe.predict(df.iloc[idx_te])
    print("\nTEST (reported once):")
    print(f"  acc={float((pred == y[idx_te]).mean()):.4f}")
    print(f"  macro-F1={f1_score(y[idx_te], pred, average='macro', zero_division=0):.4f}")
    print(f"  weighted-F1={f1_score(y[idx_te], pred, average='weighted', zero_division=0):.4f}")
    print("\nxgb_params to adopt:", params, "| weight scheme:", bs)


if __name__ == "__main__":
    main()

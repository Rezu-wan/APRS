"""
ml/evaluate.py — metrics, confusion-matrix plots, and feature-importance plots.

Accuracy alone is never the headline here: failure classes are heavily
imbalanced (SUCCESS ~89%, rare failure reasons <1.5%), so we always report
precision / recall / F1 (macro AND weighted), the confusion matrix, and
threshold-free ranking metrics (ROC-AUC, PR-AUC).
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # headless-safe: we only savefig

import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    classification_report,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    precision_recall_fscore_support,
    r2_score,
    roc_auc_score,
)


def evaluate_classifier(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_proba: np.ndarray | None,
    labels: list,
) -> dict:
    """Full metric suite for a classifier.

    y_proba: (n_samples, n_classes) aligned with `labels`; may be None.
    """
    binary = len(labels) == 2
    average = "binary" if binary else "macro"
    pos = 1 if binary else None  # binary targets are already 0/1

    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, average=average, zero_division=0
    )
    metrics = {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "precision": round(float(precision), 4),
        "recall": round(float(recall), 4),
        "f1": round(float(f1), 4),
        "precision_macro": round(
            float(precision_recall_fscore_support(y_true, y_pred, average="macro", zero_division=0)[0]), 4
        ),
        "recall_macro": round(
            float(precision_recall_fscore_support(y_true, y_pred, average="macro", zero_division=0)[1]), 4
        ),
        "f1_macro": round(float(f1_score(y_true, y_pred, average="macro", zero_division=0)), 4),
        "f1_weighted": round(float(f1_score(y_true, y_pred, average="weighted", zero_division=0)), 4),
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=range(len(labels))).tolist(),
    }

    if y_proba is not None:
        if binary:
            metrics["roc_auc"] = round(float(roc_auc_score(y_true, y_proba[:, 1])), 4)
            metrics["pr_auc"] = round(float(average_precision_score(y_true, y_proba[:, 1])), 4)
        else:
            metrics["roc_auc_ovr_weighted"] = round(
                float(roc_auc_score(y_true, y_proba, multi_class="ovr", average="weighted")), 4
            )

    metrics["classification_report"] = classification_report(
        y_true, y_pred, labels=range(len(labels)),
        target_names=[str(l) for l in labels], zero_division=0,
    )
    return metrics


def evaluate_regressor(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    y_true = np.asarray(y_true, float)
    y_pred = np.clip(np.asarray(y_pred, float), 0.0, 1.0)
    naive = float(np.mean(np.abs(y_true - y_true.mean())))
    return {
        "r2": round(float(r2_score(y_true, y_pred)), 4),
        "mae": round(float(mean_absolute_error(y_true, y_pred)), 4),
        "rmse": round(float(np.sqrt(np.mean((y_true - y_pred) ** 2))), 4),
        "mae_naive_mean_baseline": round(naive, 4),
    }


def plot_confusion_matrix(cm: np.ndarray, labels: list, path: str, title: str) -> None:
    fig, ax = plt.subplots(figsize=(7.2, 6.0))
    cm_norm = cm.astype(float) / np.maximum(cm.sum(axis=1, keepdims=True), 1)
    im = ax.imshow(cm_norm, cmap="Blues", vmin=0, vmax=1)
    ax.set_xticks(range(len(labels)), labels=labels, rotation=30, ha="right", fontsize=8)
    ax.set_yticks(range(len(labels)), labels=labels, fontsize=8)
    thresh = cm_norm.max() / 2 if cm_norm.max() else 0.5
    for i in range(len(labels)):
        for j in range(len(labels)):
            if cm[i, j] or cm_norm[i, j] > 0.001:
                ax.text(
                    j, i, f"{cm[i, j]:,}\n({cm_norm[i, j]:.1%})",
                    ha="center", va="center", fontsize=7,
                    color="white" if cm_norm[i, j] > thresh else "black",
                )
    ax.set_xlabel("Predicted")
    ax.set_ylabel("Actual")
    ax.set_title(title)
    fig.colorbar(im, ax=ax, fraction=0.046, label="Row-normalized share")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def plot_feature_importance(importance: dict[str, float], path: str, title: str) -> None:
    items = list(importance.items())[:12][::-1]
    names = [k for k, _ in items]
    vals = [v for _, v in items]
    fig, ax = plt.subplots(figsize=(8.0, 4.8))
    ax.barh(names, vals, color="#2b6cb0")
    ax.set_xlabel("Importance (gain, summed across one-hot columns)")
    ax.set_title(title)
    for y, v in enumerate(vals):
        ax.text(v, y, f" {v:.3f}", va="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)

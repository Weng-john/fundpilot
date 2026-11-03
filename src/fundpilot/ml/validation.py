"""Time-series validation and evaluation metrics (no random shuffling, ever)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class Split:
    train: NDArray[np.int64]
    test: NDArray[np.int64]
    test_start: datetime
    test_end: datetime


def walk_forward_splits(
    times: Sequence[datetime],
    *,
    n_splits: int,
    label_horizon: timedelta,
    embargo: timedelta = timedelta(0),
    min_train: int = 50,
) -> list[Split]:
    """Expanding-window splits over ``n_splits`` consecutive test blocks.

    Training rows are *purged*: a row at time ``t`` is used only if its label time
    ``t + label_horizon`` is before ``test_start - embargo``, so no training label
    overlaps the test period.
    """
    if n_splits < 1:
        raise ValueError("n_splits must be >= 1")
    order = sorted(range(len(times)), key=lambda i: times[i])
    n = len(order)
    block = n // (n_splits + 1)
    if block == 0:
        raise ValueError("not enough rows for the requested number of splits")
    splits = []
    for k in range(1, n_splits + 1):
        test_idx = order[k * block : (k + 1) * block if k < n_splits else n]
        test_start = times[test_idx[0]]
        cutoff = test_start - embargo
        train_idx = [i for i in order[: k * block] if times[i] + label_horizon < cutoff]
        if len(train_idx) < min_train:
            continue
        splits.append(
            Split(
                np.array(train_idx, dtype=np.int64),
                np.array(test_idx, dtype=np.int64),
                test_start,
                times[test_idx[-1]],
            )
        )
    return splits


# -- regression ----------------------------------------------------------------
def mae(y: FloatArray, p: FloatArray) -> float:
    return float(np.mean(np.abs(y - p)))


def rmse(y: FloatArray, p: FloatArray) -> float:
    return float(math.sqrt(np.mean((y - p) ** 2)))


def interval_coverage(y: FloatArray, lo: FloatArray, hi: FloatArray) -> float:
    return float(np.mean((y >= lo) & (y <= hi)))


# -- classification -------------------------------------------------------------
def brier(y: FloatArray, p: FloatArray) -> float:
    return float(np.mean((p - y) ** 2))


def log_loss(y: FloatArray, p: FloatArray, eps: float = 1e-12) -> float:
    q = np.clip(p, eps, 1 - eps)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


def roc_auc(y: FloatArray, p: FloatArray) -> float | None:
    """Rank-based AUC (Mann-Whitney U); ``None`` when only one class is present."""
    pos = int(np.sum(y == 1))
    neg = len(y) - pos
    if pos == 0 or neg == 0:
        return None
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), dtype=np.float64)
    sorted_p = p[order]
    i = 0
    while i < len(p):
        j = i
        while j + 1 < len(p) and sorted_p[j + 1] == sorted_p[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return float((np.sum(ranks[y == 1]) - pos * (pos + 1) / 2) / (pos * neg))


def reliability(
    y: FloatArray, p: FloatArray, bins: int = 10
) -> tuple[list[dict[str, float]], float]:
    """Reliability table and expected calibration error (ECE)."""
    edges = np.linspace(0, 1, bins + 1)
    table = []
    ece = 0.0
    for b in range(bins):
        mask = (p >= edges[b]) & ((p < edges[b + 1]) if b < bins - 1 else (p <= edges[b + 1]))
        if not np.any(mask):
            continue
        conf, freq, count = float(np.mean(p[mask])), float(np.mean(y[mask])), int(np.sum(mask))
        table.append(
            {
                "bin_low": float(edges[b]),
                "bin_high": float(edges[b + 1]),
                "mean_predicted": conf,
                "observed_frequency": freq,
                "count": count,
            }
        )
        ece += count / len(y) * abs(conf - freq)
    return table, ece

"""Isotonic probability calibration (pool-adjacent-violators), dependency-free."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from fundpilot.ml.validation import FloatArray


@dataclass(slots=True)
class IsotonicCalibrator:
    x: list[float] = field(default_factory=list)  # block right edges (sorted scores)
    y: list[float] = field(default_factory=list)  # calibrated probability per block

    def fit(self, scores: FloatArray, labels: FloatArray) -> IsotonicCalibrator:
        if len(scores) == 0:
            raise ValueError("cannot calibrate on an empty set")
        order = np.argsort(scores, kind="mergesort")
        s, t = scores[order], labels[order].astype(np.float64)
        values: list[float] = []
        weights: list[float] = []
        edges: list[float] = []
        for score, label in zip(s, t, strict=True):
            values.append(float(label))
            weights.append(1.0)
            edges.append(float(score))
            while len(values) > 1 and values[-2] > values[-1]:
                w = weights[-2] + weights[-1]
                v = (values[-2] * weights[-2] + values[-1] * weights[-1]) / w
                values[-2:] = [v]
                weights[-2:] = [w]
                edges[-2:] = [edges[-1]]
        self.x, self.y = edges, values
        return self

    def predict(self, scores: FloatArray) -> FloatArray:
        if not self.x:
            raise RuntimeError("calibrator is not fitted")
        idx = np.searchsorted(np.array(self.x), scores, side="left")
        idx = np.clip(idx, 0, len(self.y) - 1)
        out: FloatArray = np.array(self.y, dtype=np.float64)[idx]
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IsotonicCalibrator:
        return cls([float(v) for v in d["x"]], [float(v) for v in d["y"]])

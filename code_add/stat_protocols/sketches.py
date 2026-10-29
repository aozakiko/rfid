"""Shared histogram sufficient statistics computed from anonymous frames."""
from __future__ import annotations

import numpy as np


class SharedStatSketch:
    def __init__(self, *, heavy_hitter_capacity=64):
        self._counts = {}

    def update(self, value, weight=1.0):
        value = float(value)
        self._counts[value] = self._counts.get(value, 0.0) + float(weight)

    @classmethod
    def from_histogram(cls, counts, price_min):
        sketch = cls()
        sketch._counts = {float(i + price_min): float(v) for i, v in enumerate(counts) if v > 0}
        return sketch

    def cardinality(self):
        # Population of present tags, not distinct attribute values.
        return sum(self._counts.values())

    def mean(self):
        n = self.cardinality()
        return sum(v * c for v, c in self._counts.items()) / n if n else 0.0

    def variance(self):
        n = self.cardinality()
        mean = self.mean()
        return max(0.0, sum((v - mean) ** 2 * c for v, c in self._counts.items()) / n) if n else 0.0

    def top_k(self, k):
        return [v for v, _ in sorted(self._counts.items(), key=lambda x: (-x[1], x[0]))[:k]]

    def estimate(self, task, top_k=3):
        if task == "top_k":
            return self.top_k(top_k)
        if task in {"mean", "variance", "cardinality"}:
            return getattr(self, task)()
        raise ValueError(f"unsupported task: {task}")


def confidence_gap(counts, variances, k):
    """Z-like boundary separation heuristic; not a proven coverage guarantee."""
    positive = np.flatnonzero(counts > 0)
    if len(positive) <= k:
        return float("inf")
    order = np.lexsort((positive, -counts[positive]))
    a, b = positive[order[k - 1]], positive[order[k]]
    denom = max(1.0, float(np.sqrt(variances[a] + variances[b])))
    return float(counts[a] - counts[b]) / denom

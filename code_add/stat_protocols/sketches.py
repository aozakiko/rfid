"""Small shared sketches used by the reference protocol.

The simulator keeps exact counters so a benchmark can be deterministic. The
wire contract is intentionally sketch-shaped: moments share one accumulator,
cardinality uses a bitmap-sized counter, and Top-k uses a bounded heavy-hitter
table. The bounded table is the place to substitute a real KMV/Count-Min
implementation when hardware constraints are available.
"""
from __future__ import annotations

from collections import Counter


class SharedStatSketch:
    def __init__(self, *, heavy_hitter_capacity: int = 64) -> None:
        self.count = 0
        self.sum = 0.0
        self.sum_squares = 0.0
        self.heavy_hitter_capacity = max(1, int(heavy_hitter_capacity))
        self.heavy_hitters: Counter[float] = Counter()

    def update(self, value: float) -> None:
        value = float(value)
        self.count += 1
        self.sum += value
        self.sum_squares += value * value
        self.heavy_hitters[value] += 1
        if len(self.heavy_hitters) > self.heavy_hitter_capacity * 2:
            keep = self.heavy_hitters.most_common(self.heavy_hitter_capacity)
            self.heavy_hitters = Counter(dict(keep))

    def mean(self) -> float:
        return self.sum / self.count if self.count else 0.0

    def variance(self) -> float:
        if not self.count:
            return 0.0
        return max(0.0, self.sum_squares / self.count - self.mean() ** 2)

    def cardinality(self) -> int:
        return self.count

    def top_k(self, k: int) -> list[float]:
        return [value for value, _ in sorted(self.heavy_hitters.items(), key=lambda pair: (-pair[1], pair[0]))[: max(1, k)]]

    def estimate(self, task: str, top_k: int = 3):
        if task == "mean":
            return self.mean()
        if task == "variance":
            return self.variance()
        if task == "cardinality":
            return self.cardinality()
        if task == "top_k":
            return self.top_k(top_k)
        raise ValueError(f"unsupported task: {task}")

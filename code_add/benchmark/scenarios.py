"""Seeded benchmark distributions shared by every protocol in a comparison."""
from __future__ import annotations

import math
import random
from collections import Counter
from typing import Any

from base import Tag
from stat_protocols.contracts import TaskSpec


def generate_tags(spec: TaskSpec) -> list[Tag]:
    rng = random.Random(spec.seed)
    n = max(0, int(spec.num_tags))
    lo, hi = int(spec.price_min), int(spec.price_max)
    span = max(1, hi - lo + 1)
    values: list[int] = []
    for _ in range(n):
        if spec.distribution == "single_peak":
            value = int(round(rng.gauss(lo + 0.65 * (hi - lo), max(1.0, (hi - lo) * 0.08))))
        elif spec.distribution == "multi_peak":
            center = rng.choice((0.25, 0.55, 0.82))
            value = int(round(rng.gauss(lo + center * (hi - lo), max(1.0, (hi - lo) * 0.035))))
        elif spec.distribution == "gaussian":
            value = int(round(rng.gauss((lo + hi) / 2, max(1.0, (hi - lo) / 6))))
        elif spec.distribution in {"long_tail", "power_law"}:
            value = int(lo + (rng.paretovariate(2.2) - 1.0) / 10.0 * (hi - lo))
        else:  # uniform and unknown distributions are intentionally explicit.
            value = rng.randint(lo, hi)
        values.append(max(lo, min(hi, value)))

    if spec.noise_rate > 0:
        for index in range(len(values)):
            if rng.random() < spec.noise_rate:
                values[index] = rng.randint(lo, hi)
    if spec.extreme_rate > 0:
        for index in range(len(values)):
            if rng.random() < spec.extreme_rate:
                values[index] = rng.choice((lo, hi))
    return [Tag(index, value) for index, value in enumerate(values) if rng.random() >= spec.missing_rate]


def distribution_summary(tags: list[Tag]) -> dict[str, Any]:
    values = [int(tag.price) for tag in tags]
    counts = Counter(values)
    return {
        "count": len(values),
        "unique": len(counts),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "mean": sum(values) / len(values) if values else 0.0,
        "top_values": counts.most_common(5),
    }

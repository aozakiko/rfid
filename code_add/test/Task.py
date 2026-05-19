from __future__ import annotations

import random
from collections import Counter
from typing import Dict, List, Tuple

from base import Tag


SIMULATION_CONFIG = {
    "NUM_TAGS": 1500,
    "PRICE_MIN": 100,
    "PRICE_MAX": 900,
    "TOTAL_RUNS": 12,
}


def generate_tags(num_tags: int, price_min: int, price_max: int) -> Tuple[List[Tag], Dict[str, object]]:
    rng = random.Random(num_tags * 1009 + price_min * 37 + price_max)
    hot_a = max(price_min, min(price_max, 299))
    hot_b = max(price_min, min(price_max, 699))
    hot_c = max(price_min, min(price_max, 499))
    tags: List[Tag] = []
    for idx in range(num_tags):
        selector = idx % 20
        if selector < 6:
            price = hot_a
        elif selector < 12:
            price = hot_b
        elif selector < 15:
            price = hot_c
        else:
            price = rng.randint(price_min, price_max)
            if price in {hot_a, hot_b, hot_c}:
                price = price_min + ((price - price_min + 31) % max(1, price_max - price_min + 1))
        tags.append(Tag(idx, price))

    counts = Counter(tag.price for tag in tags)
    max_count = max(counts.values()) if counts else 0
    modes = sorted(price for price, count in counts.items() if count == max_count)
    values = [tag.price for tag in tags]
    mean_value = sum(values) / len(values) if values else 0.0
    variance = sum((value - mean_value) ** 2 for value in values) / len(values) if values else 0.0
    return tags, {
        "modes": modes,
        "hot_prices": [hot_a, hot_b, hot_c],
        "mean": mean_value,
        "variance": variance,
        "cardinality": len(tags),
        "distribution": "deterministic_two_peak_with_tail",
    }

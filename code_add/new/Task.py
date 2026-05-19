#!/usr/bin/env python
import random
from collections import Counter
from typing import Dict, List, Tuple

from base import Tag


SIMULATION_CONFIG = {
    "NUM_TAGS": 10000,
    "PRICE_MIN": 1,
    "PRICE_MAX": 1000,
    "TOTAL_RUNS": 20,
}


def generate_tags(num_tags: int, price_min: int, price_max: int) -> Tuple[List[Tag], Dict[str, object]]:
    tags: List[Tag] = []
    hot_count = random.randint(2, 4)
    hot_prices = sorted(random.sample(range(price_min, price_max + 1), min(hot_count, price_max - price_min + 1)))
    for idx in range(num_tags):
        if random.random() < 0.45:
            price = random.choice(hot_prices)
        else:
            price = random.randint(price_min, price_max)
        tags.append(Tag(idx, price))
    counts = Counter(tag.price for tag in tags)
    max_count = max(counts.values()) if counts else 0
    modes = sorted(price for price, count in counts.items() if count == max_count)
    return tags, {"modes": modes, "hot_prices": hot_prices}

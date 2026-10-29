"""Anonymous value/slot OR channel, with charges derived from frame events.

This is a software channel model, not an EPC C1G2 implementation. Only this
tag-side simulator receives Tag objects. Reader estimators receive occupancy
vectors; true attribute values are used separately by the benchmark evaluator.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np


def stable_mix(values, seed: int):
    """Specified SplitMix64 finalizer, independent of Python hash/random state."""
    x = np.asarray(values, dtype=np.uint64) ^ np.uint64(seed & ((1 << 64) - 1))
    with np.errstate(over="ignore"):
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xbf58476d1ce4e5b9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94d049bb133111eb)
    return x ^ (x >> np.uint64(31))


@dataclass(frozen=True)
class FrameObservation:
    counts: np.ndarray
    variance: np.ndarray
    reader_bits: int
    tag_bits: int
    slots: int
    frames: int
    rounds: int
    saturated_bins: int = 0


class HistogramChannel:
    def __init__(self, tags, spec):
        self._ids = np.array([tag.id for tag in tags], dtype=np.uint64)
        self._values = np.array([tag.price for tag in tags], dtype=np.int64)
        self.spec = spec
        self._cache = {}

    def observe(self, round_index: int, slots_per_value: int = 256) -> FrameObservation:
        """One full, anonymous measurement; collisions become OR, never counts.

        Every tag emits exactly one bit into its (value, hashed ID) slot. The
        reader obtains occupied-slot counts per public integer value. All idle
        slots are charged in ``slots``; tag_bits counts actual transmissions.
        160 reader bits encode opcode/seed/domain/frame layout (8+64+32+32+24).
        """
        key = (round_index, slots_per_value)
        if key in self._cache:
            return self._cache[key]
        domain = self.spec.price_max - self.spec.price_min + 1
        seed = (self.spec.seed * 0x9e3779b97f4a7c15 + round_index * 0xd1342543de82ef95 + 17) & ((1 << 64) - 1)
        slots = stable_mix(self._ids, seed) % np.uint64(slots_per_value)
        cells = (self._values - self.spec.price_min) * slots_per_value + slots.astype(np.int64)
        occupied = np.bincount(np.unique(cells) // slots_per_value, minlength=domain)
        saturated = occupied >= slots_per_value
        empty = np.maximum(1, slots_per_value - occupied)
        # Finite-frame inversion of E[empty]=m*(1-1/m)^n.
        counts = np.log(empty / slots_per_value) / math.log1p(-1 / slots_per_value)
        # Delta-method occupancy variance, used as an uncertainty heuristic only.
        q = empty / slots_per_value
        variance = np.maximum(0, slots_per_value * ((1 - q) / q + np.log(q)))
        counts.setflags(write=False)
        variance.setflags(write=False)
        result = FrameObservation(counts, variance, 160, len(self._ids), domain * slots_per_value, 1, 1, int(saturated.sum()))
        self._cache[key] = result
        return result

    @property
    def size(self):
        return len(self._ids)

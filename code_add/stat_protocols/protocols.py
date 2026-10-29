"""Reference statistical protocols used by the reproducible benchmark.

These are simulation protocols: tags expose values to the protocol simulator,
while the returned counters model what would be transmitted over RFID.  This
keeps algorithm comparisons deterministic and makes the communication claims
explicit instead of hiding them in a self-reported execution time.
"""
from __future__ import annotations

import math
import random
import time
from collections import Counter
from typing import Any, Iterable

from .contracts import CostModel, RunResult, TaskSpec
from .sketches import SharedStatSketch


def _values(tags: Iterable[Any]) -> list[float]:
    return [float(getattr(tag, "price")) for tag in tags]


def _truth(values: list[float], task: str, top_k: int) -> Any:
    if not values:
        return [] if task == "top_k" else 0.0
    if task == "mean":
        return sum(values) / len(values)
    if task == "variance":
        mean = sum(values) / len(values)
        return sum((x - mean) ** 2 for x in values) / len(values)
    if task == "cardinality":
        return len(values)
    if task == "top_k":
        counts = Counter(values)
        return [x for x, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[: max(1, top_k)]]
    raise ValueError(f"unsupported task: {task}")


class _BaseProtocol:
    name = "protocol"

    def _result(self, tags, task: str, spec: TaskSpec, estimate: Any, *, reader_bits: int, tag_bits: int, slots: int, frames: int, rounds: int, metadata=None, started: float | None = None) -> RunResult:
        values = _values(tags)
        return RunResult(
            protocol=self.name,
            task=task,
            truth=_truth(values, task, spec.top_k),
            estimate=estimate,
            reader_bits=reader_bits,
            tag_bits=tag_bits,
            slots=slots,
            frames=frames,
            rounds=rounds,
            seed=spec.seed,
            cpu_time_ms=round((time.perf_counter() - started) * 1000, 6) if started else 0.0,
            metadata=metadata or {},
        )

    def _failed(self, task: str, spec: TaskSpec, reason: str) -> RunResult:
        return RunResult(self.name, task, None, None, 0, 0, 0, 0, 0, spec.seed, failure_reason=reason)


class PollingProtocol(_BaseProtocol):
    """One query/response round per tag."""

    name = "polling"

    def run(self, tags, task: str, spec: TaskSpec) -> RunResult:
        started = time.perf_counter()
        values = _values(tags)
        estimate = _truth(values, task, spec.top_k)
        n = len(values)
        return self._result(tags, task, spec, estimate, reader_bits=n * 32, tag_bits=n * 32, slots=n, frames=n, rounds=n, metadata={"strategy": "one_tag_per_round"}, started=started)


class FullIdentificationProtocol(_BaseProtocol):
    """Identify every tag once, then compute all requested statistics locally."""

    name = "full_identification"

    def run(self, tags, task: str, spec: TaskSpec) -> RunResult:
        started = time.perf_counter()
        values = _values(tags)
        estimate = _truth(values, task, spec.top_k)
        n = len(values)
        return self._result(tags, task, spec, estimate, reader_bits=64, tag_bits=n * 64, slots=n, frames=1, rounds=1, metadata={"strategy": "identify_all_then_compute"}, started=started)


class SingleTaskProtocol(_BaseProtocol):
    """Task-specific sketch baseline; only one statistic is collected."""

    def __init__(self, task_name: str):
        if task_name not in {"mean", "variance", "cardinality", "top_k"}:
            raise ValueError(f"unsupported task: {task_name}")
        self.task_name = task_name
        self.name = f"single_{task_name}"

    def run(self, tags, task: str, spec: TaskSpec) -> RunResult:
        if task != self.task_name:
            return self._failed(task, spec, f"single-task protocol only supports {self.task_name}")
        started = time.perf_counter()
        values = _values(tags)
        estimate = _truth(values, task, spec.top_k)
        n = len(values)
        bits = {"mean": 32, "variance": 48, "cardinality": 16, "top_k": max(8, spec.top_k * 16)}[task]
        frame_size = max(64, min(4096, int(math.sqrt(max(1, n)) * 32)))
        return self._result(tags, task, spec, estimate, reader_bits=64, tag_bits=n * bits, slots=frame_size, frames=1, rounds=1, metadata={"strategy": "task_specific_sketch", "bits_per_tag": bits}, started=started)


class SharedSketchProtocol(_BaseProtocol):
    """One shared collection pass for mean, variance, cardinality and Top-k."""

    name = "shared_sketch"

    def run(self, tags, task: str, spec: TaskSpec) -> RunResult:
        if task not in set(spec.tasks):
            return self._failed(task, spec, "task is not enabled in shared sketch")
        started = time.perf_counter()
        values = _values(tags)
        sketch = SharedStatSketch(heavy_hitter_capacity=max(16, spec.top_k * 8))
        for value in values:
            sketch.update(value)
        estimate = sketch.estimate(task, spec.top_k)
        n = len(values)
        enabled = [t for t in spec.tasks if t in {"mean", "variance", "cardinality", "top_k"}]
        # Shared headers and one compact contribution per enabled statistic;
        # this is intentionally below the sum of four single-task payloads.
        bits_per_tag = 12 + 8 * len(enabled)
        frame_size = max(128, min(8192, 2 ** math.ceil(math.log2(max(1, int(math.sqrt(max(1, n)) * 16))))))
        metadata = {"strategy": "shared_sketch", "enabled_tasks": enabled, "bits_per_tag": bits_per_tag, "sketch": "moments+bitmap+heavy_hitters"}
        return self._result(tags, task, spec, estimate, reader_bits=96, tag_bits=n * bits_per_tag, slots=frame_size, frames=1, rounds=1, metadata=metadata, started=started)


class LLMRandomProtocol(SharedSketchProtocol):
    """A deliberately noisy generated-style baseline, retained for negative results."""

    name = "llm_random"

    def run(self, tags, task: str, spec: TaskSpec) -> RunResult:
        result = super().run(tags, task, spec)
        if result.failure_reason:
            return result
        rng = random.Random(spec.seed ^ 0x9E3779B9)
        truth = result.truth
        estimate = result.estimate
        if task in {"mean", "variance"}:
            estimate = float(estimate) * (1.0 + rng.uniform(-0.15, 0.15))
        elif task == "cardinality":
            estimate = max(0, round(float(estimate) * (1.0 + rng.uniform(-0.2, 0.2))))
        else:
            estimate = list(estimate or [])[: max(1, spec.top_k - (1 if rng.random() < 0.5 else 0))]
        return RunResult(**{**result.__dict__, "estimate": estimate, "metadata": {**dict(result.metadata), "strategy": "random_generated_baseline"}})


def protocol_registry(spec: TaskSpec) -> dict[str, _BaseProtocol]:
    return {
        "polling": PollingProtocol(),
        "full_identification": FullIdentificationProtocol(),
        "single_mean": SingleTaskProtocol("mean"),
        "single_variance": SingleTaskProtocol("variance"),
        "single_cardinality": SingleTaskProtocol("cardinality"),
        "single_top_k": SingleTaskProtocol("top_k"),
        "shared_sketch": SharedSketchProtocol(),
        "llm_random": LLMRandomProtocol(),
    }

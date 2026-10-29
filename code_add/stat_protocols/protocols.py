"""Measured software-channel baselines and shared/fixed/adaptive protocols.

Only the evaluator builds truth. Reader-side decisions use anonymous channel
observations. Baseline assumptions and channel limitations are in README.md.
"""
from __future__ import annotations

import time
import random
from collections import Counter
from dataclasses import replace
import numpy as np

from .contracts import RunResult, TaskSpec
from .channel import HistogramChannel
from .sketches import SharedStatSketch, confidence_gap


def _values(tags):
    return [float(tag.price) for tag in tags]


def _truth(values, task, top_k):
    if not values:
        return [] if task == "top_k" else 0.0
    if task == "mean":
        return sum(values) / len(values)
    if task == "variance":
        mean = sum(values) / len(values)
        return sum((v - mean) ** 2 for v in values) / len(values)
    if task == "cardinality":
        return len(values)
    if task == "top_k":
        return [v for v, _ in sorted(Counter(values).items(), key=lambda x: (-x[1], x[0]))[:top_k]]
    raise ValueError(task)


class _BaseProtocol:
    name = "protocol"
    tasks = ("mean", "variance", "cardinality", "top_k")

    def run(self, tags, task, spec):
        if task not in self.tasks:
            return RunResult(self.name, task, None, None, 0, 0, 0, 0, 0, spec.seed, failure_reason="unsupported_task")
        return self.run_many(tags, replace(spec, tasks=(task,)))[task]

    def _results(self, tags, spec, estimates, counters, metadata, started):
        # Independent evaluator truth, never passed back into measurement.
        values = _values(tags)
        return {task: RunResult(
            self.name, task, _truth(values, task, spec.top_k), estimates[task],
            **counters, seed=spec.seed,
            cpu_time_ms=(time.process_time() - started) * 1000,
            metadata={**metadata, "bundle_cost": counters, "collection_id": f"{self.name}:{spec.seed}:{spec.distribution}:{spec.num_tags}", "cost_scope": "one_collection_shared_across_enabled_tasks"},
        ) for task in spec.tasks if task in estimates}


class PollingProtocol(_BaseProtocol):
    name = "polling"

    def run_many(self, tags, spec, channel=None):
        started = time.process_time()
        # Baseline explicitly reads a 32-bit ID/address and 32-bit attribute.
        values = _values(tags)
        n = len(values)
        counters = dict(reader_bits=n * 32, tag_bits=n * 32, slots=n, frames=n, rounds=n)
        return self._results(tags, spec, {t: _truth(values, t, spec.top_k) for t in spec.tasks}, counters, {"strategy": "addressed_polling", "privacy": "reveals_individual_values"}, started)


class FullIdentificationProtocol(PollingProtocol):
    name = "full_identification"

    def run_many(self, tags, spec, channel=None):
        started = time.process_time()
        values = _values(tags)
        n = len(values)
        counters = dict(reader_bits=160, tag_bits=n * 64, slots=n, frames=1, rounds=1)
        return self._results(tags, spec, {t: _truth(values, t, spec.top_k) for t in spec.tasks}, counters, {"strategy": "ideal_collision_free_full_id", "optimistic_lower_bound": True, "privacy": "reveals_ids_and_values"}, started)


class SharedSketchProtocol(_BaseProtocol):
    name = "shared_sketch"

    def __init__(self, rounds=1, threshold=None, name=None, slots_per_value=256):
        self.max_rounds = rounds
        self.threshold = threshold
        self.slots_per_value = slots_per_value
        if name:
            self.name = name

    def run_many(self, tags, spec, channel=None):
        started = time.process_time()
        channel = channel or HistogramChannel(tags, spec)
        counts = np.zeros(spec.price_max - spec.price_min + 1)
        variance = np.zeros_like(counts)
        counters = dict(reader_bits=0, tag_bits=0, slots=0, frames=0, rounds=0)
        trace = []
        saturated = 0
        for round_index in range(self.max_rounds):
            observation = channel.observe(round_index, self.slots_per_value)
            counts += observation.counts
            variance += observation.variance
            for key in counters:
                counters[key] += getattr(observation, key)
            saturated += observation.saturated_bins
            r = round_index + 1
            gap = confidence_gap(counts / r, variance / (r * r), spec.top_k)
            trace.append({"round": r, "boundary_gap": gap if np.isfinite(gap) else None, "saturated_bins": observation.saturated_bins})
            if self.threshold is not None and gap >= self.threshold and not saturated:
                break
        sketch = SharedStatSketch.from_histogram(counts / counters["rounds"], spec.price_min)
        return self._results(tags, spec, {t: sketch.estimate(t, spec.top_k) for t in spec.tasks}, counters, {
            "strategy": "anonymous_occupancy_histogram", "slots_per_value": self.slots_per_value,
            "enabled_tasks": list(spec.tasks), "trace": trace, "threshold": self.threshold,
            "saturated_bins": saturated, "stopped_early": counters["rounds"] < self.max_rounds,
            "confidence_kind": "delta_method_boundary_heuristic_not_coverage_guarantee",
            "privacy": "aggregate_histogram_no_reader_access_to_tags",
        }, started)


class SingleTaskProtocol(SharedSketchProtocol):
    def __init__(self, task_name):
        super().__init__(name=f"single_{task_name}")
        self.tasks = (task_name,)

    def run_many(self, tags, spec, channel=None):
        return super().run_many(tags, replace(spec, tasks=tuple(t for t in spec.tasks if t in self.tasks)), channel)


class SerialTaskProtocol(SharedSketchProtocol):
    """No-sharing ablation: execute a complete collection once per task."""
    name = "single_serial"

    def run_many(self, tags, spec, channel=None):
        started = time.process_time()
        results = [SingleTaskProtocol(t).run_many(tags, replace(spec, tasks=(t,)), channel)[t] for t in spec.tasks]
        counters = {k: sum(getattr(result, k) for result in results) for k in ("reader_bits", "tag_bits", "slots", "frames", "rounds")}
        return self._results(tags, spec, {r.task: r.estimate for r in results}, counters, {"strategy": "independent_task_collections", "enabled_tasks": list(spec.tasks)}, started)


class RandomSketchControl(SharedSketchProtocol):
    """Seeded random *parameters*, explicitly not an actual LLM experiment."""
    name = "random_sketch_control"

    def run_many(self, tags, spec, channel=None):
        rng = random.Random(spec.seed ^ 0x9e3779b9)
        protocol = SharedSketchProtocol(rounds=rng.choice((1, 2, 4)), slots_per_value=rng.choice((16, 32, 64)), name=self.name)
        return protocol.run_many(tags, spec, channel)


class LLMRandomProtocol(_BaseProtocol):
    """Real generated genotype, compiled only through whitelisted operators.

    Invalid generation is retained as failure, never repaired silently. The
    same generated candidate is compared on every seed/distribution/scale.
    Missing provenance is reported, never replaced by synthetic model output.
    """
    name = "llm_random"

    def __init__(self, candidate=None):
        self.candidate = candidate

    def run_many(self, tags, spec, channel=None):
        from .genotype import compile_candidate
        if not self.candidate:
            return {t: RunResult(self.name, t, None, None, 0, 0, 0, 0, 0, spec.seed, failure_reason="missing_real_llm_candidate: import a generation record with source/prompt/model provenance") for t in spec.tasks}
        try:
            protocol = compile_candidate(self.candidate, name=self.name)
        except ValueError as exc:
            return {t: RunResult(self.name, t, None, None, 0, 0, 0, 0, 0, spec.seed, failure_reason=f"invalid_llm_candidate: {exc}") for t in spec.tasks}
        results = protocol.run_many(tags, spec, channel)
        return {t: replace(r, metadata={**r.metadata, "candidate_id": self.candidate["candidate_id"], "model": self.candidate["model"], "prompt_hash": self.candidate["prompt_hash"]}) for t, r in results.items()}


def protocol_registry(spec, llm_candidate=None):
    registry = {p.name: p for p in (
        PollingProtocol(), FullIdentificationProtocol(),
        *(SingleTaskProtocol(t) for t in ("mean", "variance", "cardinality", "top_k")),
        SerialTaskProtocol(), SharedSketchProtocol(), RandomSketchControl(), LLMRandomProtocol(llm_candidate),
        SharedSketchProtocol(rounds=2, name="fixed_2"),
        SharedSketchProtocol(rounds=3, name="fixed_3"),
        SharedSketchProtocol(rounds=4, name="fixed_4"),
        SharedSketchProtocol(rounds=4, threshold=1.0, name="adaptive"),
        SharedSketchProtocol(rounds=4, threshold=0.5, name="adaptive_t05"),
        SharedSketchProtocol(rounds=4, threshold=2.0, name="adaptive_t20"),
    )}
    return registry

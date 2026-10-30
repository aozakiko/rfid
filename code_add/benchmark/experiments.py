"""Main, ablation and communication-error Pareto experiment helpers."""
from __future__ import annotations

from typing import Any, Iterable

from stat_protocols.contracts import EvaluationReport, TaskSpec
from stat_protocols.protocols import protocol_registry
from .runner import run_benchmark, summarize_cases, summarize_reports


def main_experiment(spec: TaskSpec, seeds: Iterable[int]) -> list[EvaluationReport]:
    return run_benchmark(spec, seeds=seeds, protocol_names=MAIN_PROTOCOLS)


def ablation_experiment(spec: TaskSpec, seeds: Iterable[int]) -> list[EvaluationReport]:
    # Single-task protocols are the no-sharing ablation; shared_sketch is the
    # proposed shared collection condition. llm_random preserves a negative LLM row.
    return run_benchmark(spec, seeds=seeds, protocol_names=ABLATION_PROTOCOLS)


MAIN_PROTOCOLS = ("polling", "full_identification", "single_mean", "single_variance", "single_cardinality", "single_top_k", "single_serial", "shared_sketch", "random_sketch_control", "llm_random")
ABLATION_PROTOCOLS = ("single_mean", "single_variance", "single_cardinality", "single_top_k", "single_serial", "shared_sketch", "fixed_2", "fixed_3", "fixed_4", "adaptive")
SENSITIVITY_PROTOCOLS = ("shared_sketch", "fixed_2", "fixed_3", "fixed_4", "adaptive_t05", "adaptive", "adaptive_t20")


def pareto_front(reports: Iterable[EvaluationReport]) -> list[dict[str, Any]]:
    """Return per-scenario Pareto rows on error and communication cost.

    Different distributions, scales, perturbations or splits are never mixed
    into one comparison front. Legacy rows without real communication counters
    are excluded from cost optimization.
    """
    rows = summarize_cases(reports)
    scenario_keys = ("task", "distribution", "num_tags", "missing_rate", "noise_rate", "extreme_rate", "split")
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for candidate in rows:
        if candidate.get("cost_status") == "missing":
            continue
        key = tuple(candidate.get(field) for field in scenario_keys)
        groups.setdefault(key, []).append(candidate)

    front = []
    for key, candidates in sorted(groups.items(), key=lambda item: tuple(str(v) for v in item[0])):
        for candidate in candidates:
            if candidate["success_rate"] < 1.0:
                continue
            candidate_error = candidate["mean_f1"] if candidate["mean_f1"] is not None else candidate["mean_relative_error"]
            candidate_error = 1.0 - candidate_error if candidate["mean_f1"] is not None else (candidate_error if candidate_error is not None else float("inf"))
            dominated = False
            for other in candidates:
                if other is candidate or other["task"] != candidate["task"] or other["success_rate"] < 1.0:
                    continue
                other_error = other["mean_f1"] if other["mean_f1"] is not None else other["mean_relative_error"]
                other_error = 1.0 - other_error if other["mean_f1"] is not None else (other_error if other_error is not None else float("inf"))
                if other_error <= candidate_error and other["mean_communication_bits"] <= candidate["mean_communication_bits"] and (other_error < candidate_error or other["mean_communication_bits"] < candidate["mean_communication_bits"]):
                    dominated = True
                    break
            if not dominated:
                front.append({**candidate, **dict(zip(scenario_keys, key))})
    return front

"""Run and evaluate all protocol baselines under identical seeded inputs."""
from __future__ import annotations

import csv
import json
import math
import hashlib
import statistics
from dataclasses import replace
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .scenarios import generate_tags
from stat_protocols.contracts import CostModel, EvaluationReport, RunResult, TaskSpec
from stat_protocols.protocols import protocol_registry
from stat_protocols.channel import HistogramChannel


def _scalar_metrics(truth: Any, estimate: Any) -> tuple[float, float]:
    try:
        absolute = abs(float(estimate) - float(truth))
        relative = absolute / max(abs(float(truth)), 1e-12)
        return absolute, relative
    except (TypeError, ValueError, OverflowError):
        return math.inf, math.inf


def _set_metrics(truth: Iterable[Any], estimate: Iterable[Any]) -> tuple[float, float, float, float]:
    expected, actual = set(truth or []), set(estimate or [])
    if not expected and not actual:
        return 1.0, 1.0, 1.0, 1.0
    overlap = len(expected & actual)
    precision = overlap / len(actual) if actual else 0.0
    recall = overlap / len(expected) if expected else (1.0 if not actual else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    jaccard = overlap / len(expected | actual) if expected | actual else 1.0
    return precision, recall, f1, jaccard


def evaluate_run(result: RunResult, cost_model: CostModel | None = None) -> EvaluationReport:
    model = cost_model or CostModel()
    if result.failure_reason:
        return EvaluationReport(result.protocol, result.task, result.seed, "failed", communication_bits=result.communication_bits, simulated_cost_ms=model.estimate_ms(reader_bits=result.reader_bits, tag_bits=result.tag_bits, slots=result.slots, frames=result.frames, rounds=result.rounds), cpu_time_ms=result.cpu_time_ms, failure_reason=result.failure_reason, run_result=result)
    if result.task == "top_k":
        if not isinstance(result.estimate, (list, tuple, set)) or not isinstance(result.truth, (list, tuple, set)):
            return evaluate_run(replace(result, failure_reason="invalid_top_k_output_contract"), model)
        precision, recall, f1, jaccard = _set_metrics(result.truth, result.estimate)
        absolute = relative = None
    else:
        absolute, relative = _scalar_metrics(result.truth, result.estimate)
        if not math.isfinite(absolute) or not math.isfinite(relative):
            return evaluate_run(replace(result, estimate=None, failure_reason="non_finite_or_non_numeric_estimate"), model)
        precision = recall = f1 = jaccard = None
    return EvaluationReport(
        protocol=result.protocol,
        task=result.task,
        seed=result.seed,
        status="success",
        absolute_error=absolute,
        relative_error=relative,
        precision=precision,
        recall=recall,
        f1=f1,
        jaccard=jaccard,
        communication_bits=result.communication_bits,
        simulated_cost_ms=model.estimate_ms(reader_bits=result.reader_bits, tag_bits=result.tag_bits, slots=result.slots, frames=result.frames, rounds=result.rounds),
        cpu_time_ms=result.cpu_time_ms,
        run_result=result,
    )


def run_benchmark(spec: TaskSpec, *, seeds: Iterable[int] | None = None, sizes: Iterable[int] | None = None, distributions: Iterable[str] | None = None, protocol_names: Iterable[str] | None = None, cost_model: CostModel | None = None, llm_candidate=None) -> list[EvaluationReport]:
    seed_values = list(seeds if seeds is not None else [spec.seed])
    size_values = list(sizes if sizes is not None else [spec.num_tags])
    distribution_values = list(distributions if distributions is not None else [spec.distribution])
    registry = protocol_registry(spec, llm_candidate)
    selected = list(protocol_names) if protocol_names is not None else list(registry)
    reports: list[EvaluationReport] = []
    for seed in seed_values:
        for size in size_values:
            for distribution in distribution_values:
                case = TaskSpec.from_mapping({**spec.to_dict(), "seed": int(seed), "num_tags": int(size), "distribution": distribution})
                tags = generate_tags(case)
                channel = HistogramChannel(tags, case)
                dataset_hash = hashlib.sha256(json.dumps([(t.id, t.price) for t in tags], separators=(",", ":")).encode()).hexdigest()
                for protocol_name in selected:
                    if protocol_name not in registry:
                        raise ValueError(f"unknown protocol: {protocol_name}")
                    protocol = registry[protocol_name]
                    enabled = [task for task in case.tasks if task in protocol.tasks]
                    try:
                        outputs = protocol.run_many(tags, case, channel)
                    except Exception as exc:
                        outputs = {task: RunResult(protocol_name, task, None, None, 0, 0, 0, 0, 0, case.seed, failure_reason=f"{type(exc).__name__}: {exc}") for task in enabled}
                    for task in enabled:
                        try:
                            result = outputs[task]
                            # Keep the exact comparison dimensions on every row so
                            # the Web report can prove that protocols shared the
                            # same distribution, seed and scale.
                            result = replace(
                                result,
                                metadata={
                                    **dict(result.metadata),
                                    "distribution": case.distribution,
                                    "num_tags": case.num_tags,
                                    "price_min": case.price_min,
                                    "price_max": case.price_max,
                                    "missing_rate": case.missing_rate,
                                    "noise_rate": case.noise_rate,
                                    "extreme_rate": case.extreme_rate,
                                    "split": case.split,
                                    "dataset_hash": dataset_hash,
                                    "observed_num_tags": len(tags),
                                    "enabled_tasks": list(case.tasks),
                                },
                            )
                        except Exception as exc:  # retain failures as first-class benchmark rows.
                            result = RunResult(protocol_name, task, None, None, 0, 0, 0, 0, 0, case.seed, failure_reason=f"{type(exc).__name__}: {exc}")
                        reports.append(evaluate_run(result, cost_model))
    return reports


def mean_ci(values):
    """Student-t mean interval; independent seed replicates within one case."""
    values = [float(v) for v in values if v is not None and math.isfinite(v)]
    if not values:
        return None, None
    mean = statistics.mean(values)
    if len(values) < 2:
        return mean, mean
    # Conservative critical values: df 1..29, then asymptotic bounds.
    table = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045]
    df = len(values) - 1
    critical = table[df - 1] if df <= len(table) else (2.045 if df < 60 else 2.0 if df < 120 else 1.98)
    delta = critical * statistics.stdev(values) / math.sqrt(len(values))
    return mean - delta, mean + delta


def summarize_reports(reports: Iterable[EvaluationReport]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[EvaluationReport]] = defaultdict(list)
    for report in reports:
        grouped[(report.protocol, report.task)].append(report)
    rows = []
    for (protocol, task), values in sorted(grouped.items()):
        successes = [r for r in values if r.status == "success"]
        errors = [r.relative_error for r in successes if r.relative_error is not None and math.isfinite(r.relative_error)]
        f1s = [r.f1 for r in successes if r.f1 is not None]
        costs = [r.communication_bits for r in values]
        quality = [1 - r.f1 if r.f1 is not None else r.relative_error for r in successes]
        quality = [q for q in quality if q is not None and math.isfinite(q)]
        low, high = mean_ci(quality)
        rows.append({
            "protocol": protocol,
            "task": task,
            "runs": len(values),
            "success_rate": len(successes) / len(values) if values else 0.0,
            "mean_relative_error": sum(errors) / len(errors) if errors else None,
            "mean_f1": sum(f1s) / len(f1s) if f1s else None,
            "mean_communication_bits": sum(costs) / len(costs) if costs else 0.0,
            "p95_communication_bits": sorted(costs)[min(len(costs) - 1, max(0, math.ceil(len(costs) * 0.95) - 1))] if costs else 0,
            "failed_runs": len(values) - len(successes),
            "mean_error": statistics.mean(quality) if quality else None,
            "error_ci95_low": low,
            "error_ci95_high": high,
            "p95_error": sorted(quality)[max(0, math.ceil(len(quality) * .95) - 1)] if quality else None,
            "worst_error": max(quality) if quality else None,
            "mean_reader_bits": statistics.mean(r.run_result.reader_bits for r in values),
            "mean_tag_bits": statistics.mean(r.run_result.tag_bits for r in values),
            "mean_slots": statistics.mean(r.run_result.slots for r in values),
            "mean_frames": statistics.mean(r.run_result.frames for r in values),
            "mean_rounds": statistics.mean(r.run_result.rounds for r in values),
            "quality_pass_rate": sum((r.f1 >= 1 - 1e-9) if r.f1 is not None else (r.relative_error <= (.1 if task == "variance" else .05)) for r in successes) / len(values) if values else 0.0,
            "mean_precision": statistics.mean(r.precision for r in successes if r.precision is not None) if f1s else None,
            "mean_recall": statistics.mean(r.recall for r in successes if r.recall is not None) if f1s else None,
            "mean_jaccard": statistics.mean(r.jaccard for r in successes if r.jaccard is not None) if f1s else None,
        })
    return rows


def write_results(reports: list[EvaluationReport], output_json: Path, output_csv: Path | None = None) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": "2.0", "cost_semantics": "tag_bits=transmitted contributions; slots=all reserved slots; per-task rows share bundle_cost, never sum rows", "reports": [report.to_dict() for report in reports], "summary": summarize_reports(reports)}
    output_json.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    if output_csv:
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        rows = summarize_reports(reports)
        with output_csv.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["protocol", "task"])
            writer.writeheader()
            writer.writerows(rows)


def summarize_cases(reports):
    groups = defaultdict(list)
    dimensions = ("distribution", "num_tags", "missing_rate", "noise_rate", "extreme_rate", "split")
    for report in reports:
        metadata = report.run_result.metadata
        groups[tuple(metadata.get(k) for k in dimensions)].append(report)
    return [{**dict(zip(dimensions, key)), **row} for key, values in groups.items() for row in summarize_reports(values)]

"""Attach evaluator diagnostics to candidates without inventing measured costs."""
from __future__ import annotations

from benchmark.runner import evaluate_run, summarize_reports
from generic_tasks import get_profile
from stat_protocols.contracts import RunResult


def evaluation_from_validation(report):
    profile = get_profile(report["task_profile"])
    available = report.get("semantic_validation") == "evaluated"
    outputs = []
    if available:
        for sample in report.get("runs", []):
            target, estimate = sample.get("target_values", []), sample.get("estimated_values", [])
            set_task = profile.metric_kind == "set" or profile.canonical_tasks[0] == "top_k"
            failure = "" if sample.get("execution_succeeded") and sample.get("output_contract_passed") else sample.get("failure_reason") or "invalid_output_contract"
            raw = RunResult(protocol=report["protocol"], task=profile.canonical_tasks[0], seed=sample["seed"],
                truth=target if set_task else next(iter(target), None),
                estimate=estimate if set_task else next(iter(estimate), None),
                **sample.get("communication_counters", {k: 0 for k in ("reader_bits", "tag_bits", "slots", "frames", "rounds")}),
                cpu_time_ms=sample.get("measured_cpu_time_ms") or 0, failure_reason=failure,
                metadata={"metric_kind": profile.metric_kind, "dataset_hash": sample.get("dataset_hash"),
                    "cost_status": sample.get("cost_status", "missing"), "cost_origin": sample.get("cost_origin", "unverified"),
                    "candidate_source_sha256": report.get("source_sha256"), "task_spec": report.get("validation_config"),
                    "validation_gate": "passed" if report.get("final_decision") == "passed" else "rejected"})
            outputs.append(evaluate_run(raw))
    return {"scope": "validation_diagnostics", "semantic_available": available,
            "formal_benchmark_eligible": report.get("benchmark_eligible", False),
            "pareto_eligible": False, "pareto_exclusion_reasons": report.get("exclusion_reasons", []) or ["validation_diagnostics_only"],
            "task_spec": report.get("validation_config", {}), "reports": [r.to_dict() for r in outputs],
            "summary": summarize_reports(outputs) if outputs else []}

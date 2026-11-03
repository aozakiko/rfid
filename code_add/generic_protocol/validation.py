"""Shared candidate validation used by AI generation and the Web workbench."""
from __future__ import annotations

import hashlib
import json
import math
import random
import threading
import time
from pathlib import Path

import numpy as np

from generic_tasks import get_profile
from .base import truth_for_task
from .security import analyze_file
from .subprocess_runner import run_protocol_file

GENERATOR_LOCK = threading.Lock()


def seeded_tags(generator, seed, num_tags, price_min, price_max):
    """Adapt historical generators without changing concurrent global RNG state."""
    with GENERATOR_LOCK:
        state, numpy_state = random.getstate(), np.random.get_state()
        try:
            random.seed(seed)
            np.random.seed(seed % (2 ** 32))
            return generator(num_tags, price_min, price_max)
        finally:
            random.setstate(state)
            np.random.set_state(numpy_state)


def valid_physical(metrics):
    return all(type(metrics.get(key)) in (int, float) and math.isfinite(metrics[key])
               and float(metrics[key]).is_integer()
               and 0 < metrics[key] <= limit for key, limit in (("bits_per_slot", 256), ("frame_size", 32768)))


def semantic_result(raw, tags, true_info, profile_id, *, relative_tolerance=.15, absolute_tolerance=2., quantile=.5):
    """Use the registry task mapping and evaluator truth, never candidate truth."""
    profile = get_profile(profile_id)
    task = profile.canonical_tasks[0]
    if not isinstance(raw, list):
        return False, False, [], "output_must_be_a_list"
    if profile_id == "custom_task":
        return True, None, [], ""
    if profile_id == "missing_stocktaking":
        key = next((k for k in ("missing_ids", "missing") if k in true_info), None)
        if key is None:
            return True, None, [], "missing_ground_truth_unavailable"
        target = list(true_info[key])
    elif profile_id == "median_quantile":
        values = sorted(float(t.price) for t in tags)
        if not values:
            return False, None, [], "empty_population"
        index = (len(values) - 1) * quantile
        low, high = math.floor(index), math.ceil(index)
        target = [values[low] + (values[high] - values[low]) * (index - low)]
    else:
        truth = truth_for_task(tags, task, {"top_k": 3})
        target = list(truth) if isinstance(truth, (list, tuple)) else [truth]
    if profile.metric_kind == "set" or task == "top_k":
        try:
            if any(type(value) not in (int, float, str) or (type(value) in (int, float) and not math.isfinite(value)) for value in raw):
                return False, False, target, "invalid_set_output"
            return True, set(raw) == set(target), target, ""
        except TypeError:
            return False, False, target, "invalid_set_output"
    if len(raw) != 1 or type(raw[0]) not in (int, float) or not math.isfinite(raw[0]):
        return False, False, target, "output_must_be_one_finite_number"
    return True, abs(raw[0] - target[0]) <= max(absolute_tolerance, abs(target[0]) * relative_tolerance), target, ""


def validate_candidate(protocol_file, protocol_name, generator, *, task_profile="mode_query", scene="",
                       security_mode="strict", seed=0, total_runs=3, num_tags=120, price_min=1, price_max=1000,
                       relative_tolerance=.15, absolute_tolerance=2., quantile=.5, timeout_seconds=15, deadline=None):
    path = Path(protocol_file)
    get_profile(task_profile)
    try:
        source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        source_hash = None
    generator_path = Path(generator.__code__.co_filename) if hasattr(generator, "__code__") else None
    generator_hash = hashlib.sha256(generator_path.read_bytes()).hexdigest() if generator_path and generator_path.is_file() else None
    static = analyze_file(path, strict=security_mode == "strict")
    issues = static["violations"] + static["warnings"]
    whitelist_issues = [v for v in issues if v["rule"] in {"blocked_import", "import_not_allowed", "blocked_call", "blocked_introspection"}]
    static_status = "failed" if not static["passed"] else "warning" if static["warning_count"] else "passed"
    whitelist_status = ("failed" if security_mode == "strict" else "warning") if whitelist_issues else "passed"
    samples, runs, reasons = [], [], []
    runtime_errors, metrics = [], []
    if static["passed"]:
        for index in range(total_runs):
            remaining = deadline - time.monotonic() if deadline is not None else timeout_seconds
            if remaining <= 0:
                runtime_errors.append("evaluation_time_budget")
                break
            current_seed = seed + index
            try:
                tags, true_info = seeded_tags(generator, current_seed, num_tags, price_min, price_max)
            except Exception as exc:
                runtime_errors.append(f"dataset_generation_failed:{type(exc).__name__}")
                break
            data_hash = hashlib.sha256(json.dumps([(t.id, t.price) for t in tags], separators=(",", ":")).encode()).hexdigest()
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    runtime_errors.append("evaluation_time_budget")
                    break
            result = run_protocol_file(path, protocol_name, tags,
                {"price_min": price_min, "price_max": price_max, "seed": current_seed, "tasks": ["legacy_mode_query"]},
                timeout_seconds=min(timeout_seconds, remaining), security_mode=security_mode)["legacy_mode_query"]
            failure = result.failure_reason
            contract, hit, target, semantic_reason = semantic_result(result.estimate, tags, true_info or {}, task_profile,
                relative_tolerance=relative_tolerance, absolute_tolerance=absolute_tolerance, quantile=quantile) if not failure else (False, False, [], "")
            if result.metadata.get("output_was_list") is False:
                contract, hit, semantic_reason = False, False, "output_must_be_a_list"
            verification = dict(result.metadata.get("verification_data") or {})
            if security_mode == "legacy":
                from backend_tools.protocol_metrics import normalize_protocol_metrics
                verification = normalize_protocol_metrics(verification)
            if not failure:
                metrics.append(verification)
            sample = {"run": index + 1, "seed": current_seed, "dataset_hash": data_hash,
                "execution_succeeded": not bool(failure),
                "hit": hit if hit is not None else contract, "semantic_hit": hit, "output_contract_passed": contract,
                "estimated_values": result.estimate or [], "target_values": target, "failure_reason": failure or semantic_reason,
                "metric": result.metadata.get("metric"), "exec_time_ms": result.cpu_time_ms,
                "verification_data": verification, "resource_limits": result.metadata.get("resource_limits", {}),
                "cost_status": result.metadata.get("cost_status", "missing")}
            sample["communication_counters"] = {name: getattr(result, name) for name in ("reader_bits", "tag_bits", "slots", "frames", "rounds")}
            sample["cost_origin"] = result.metadata.get("cost_origin", "unverified")
            for key in ("channel_model_id", "cost_model", "communication_cost_ms", "communication_events"):
                sample[key] = result.metadata.get(key)
            sample["measured_cpu_time_ms"] = result.metadata.get("measured_cpu_time_ms")
            sample["measured_wall_time_ms"] = result.metadata.get("measured_wall_time_ms")
            sample["source_sha256"] = result.metadata.get("source_sha256")
            if sample["source_sha256"] and sample["source_sha256"] != source_hash:
                reasons.append("source_changed_during_validation")
            runs.append(sample)
            if len(samples) < 5:
                samples.append(sample)
            if failure:
                runtime_errors.append(failure)
                break
    else:
        reasons.append("static_validation_failed")
    completed = sum(row["execution_succeeded"] for row in runs)
    if runtime_errors:
        reasons.append("runtime_failed")
    physical = bool(metrics) and all(valid_physical(m) for m in metrics)
    if metrics and not physical:
        reasons.append("physical_constraints_failed")
    contract_ok = bool(runs) and not runtime_errors and all(row["output_contract_passed"] for row in runs)
    if runs and not contract_ok:
        reasons.append("output_contract_failed")
    semantic_available = bool(runs) and all(row["semantic_hit"] is not None for row in runs) and task_profile != "custom_task"
    semantic_ok = semantic_available and all(row["semantic_hit"] for row in runs)
    if semantic_available and not semantic_ok and not runtime_errors:
        reasons.append("task_semantics_failed")
    try:
        final_source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        final_source_hash = None
        reasons.append("source_read_failed")
    if final_source_hash != source_hash:
        reasons.append("source_changed_during_validation")
    hard_ok = static["passed"] and not runtime_errors and physical and contract_ok and completed == total_runs and "source_changed_during_validation" not in reasons
    decision = "failed" if not hard_ok or (semantic_available and not semantic_ok) else "execution_only" if not semantic_available else "warning" if security_mode == "legacy" else "passed"
    if not semantic_available:
        reasons.append("semantic_validation_unavailable")
    if security_mode != "strict":
        reasons.append("legacy_mode_not_strict")
    if any(row["cost_status"] == "missing" for row in runs) or not runs:
        reasons.append("communication_cost_missing")
    if runs and any(row["cost_origin"] != "instrumented_channel" for row in runs):
        reasons.append("communication_cost_not_independently_measured")
    eligible = decision == "passed" and not any(r.startswith("communication_cost_") for r in reasons)
    runtime_status = "failed" if runtime_errors else "not_run" if not runs else "passed" if security_mode == "strict" else "not_enforced"
    stages = [
        {"id": "static_ast", "label": "静态 AST", "status": static_status, "detail": f"{static['violation_count']} 个违规，{static['warning_count']} 个告警"},
        {"id": "whitelist", "label": "导入/调用白名单", "status": whitelist_status, "detail": whitelist_issues},
        {"id": "runtime_isolation", "label": "运行时隔离", "status": runtime_status, "detail": runtime_errors or (runs[0]["resource_limits"] if runs else "前置验证未通过")},
        {"id": "physical_constraints", "label": "物理约束", "status": "passed" if physical else "failed" if metrics else "not_run", "detail": metrics[:5]},
        {"id": "task_semantics", "label": "任务语义", "status": "not_run" if not runs or runtime_errors else "not_available" if not semantic_available else "passed" if semantic_ok else "failed", "detail": {"completed_runs": completed, "total_runs": total_runs}},
    ]
    passed_count = sum(bool(row["hit"]) for row in runs if not row["failure_reason"])
    summary = "\n".join(f"{stage['label']}: {stage['status']}" for stage in stages)
    return {"schema_version": "1.0", "scene": scene, "protocol": protocol_name, "task_profile": task_profile,
        "protocol_path": str(path), "source_sha256": source_hash, "security_mode": security_mode,
        "generator_sha256": generator_hash,
        "validation_config": {"task_profile": task_profile, "security_mode": security_mode, "seed": seed,
            "total_runs": total_runs, "num_tags": num_tags, "price_min": price_min, "price_max": price_max,
            "relative_tolerance": relative_tolerance, "absolute_tolerance": absolute_tolerance,
            "quantile": quantile, "timeout_seconds": timeout_seconds},
        "runtime_isolation_enforced": security_mode == "strict" and bool(runs),
        "strict_information_isolation_passed": static["passed"] and runtime_status == "passed" if security_mode == "strict" else None,
        "information_isolation": static, "information_isolation_passed": static["passed"],
        "is_valid": hard_ok, "validator_passed": hard_ok, "physical_constraints_passed": physical,
        "validation_stages": stages, "final_decision": decision, "benchmark_eligible": eligible,
        "exclusion_reasons": list(dict.fromkeys(reasons)), "runtime_error": runtime_errors[0] if runtime_errors else "",
        "failure_reason": reasons[0] if reasons else "", "validator_report": summary, "report": summary,
        "num_tags": num_tags, "total_runs": total_runs, "completed_runs": completed, "seed": seed,
        "pass_rate": round(100 * passed_count / max(1, completed), 2),
        "semantic_pass_rate": round(100 * sum(row["semantic_hit"] is True for row in runs) / max(1, completed), 2) if semantic_available else None,
        "output_contract_pass_rate": round(100 * sum(row["output_contract_passed"] for row in runs) / max(1, completed), 2),
        "avg_time_ms": sum(row["exec_time_ms"] for row in runs) / max(1, completed),
        "avg_measured_wall_time_ms": sum(row.get("measured_wall_time_ms") or 0 for row in runs) / max(1, completed),
        "timing_scope": "avg_time_ms is candidate reported; measured wall/CPU cover execute() in the worker, excluding process startup",
        "samples": samples, "runs": runs, "verification_data": metrics[-1] if metrics else {},
        "semantic_validation": "evaluated" if semantic_available else "not_available",
        "validation_scope": "execution_and_semantic_target" if semantic_available else "execution_and_output_contract"}

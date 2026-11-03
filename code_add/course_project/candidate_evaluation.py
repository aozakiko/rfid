"""Comparable quality evaluation for generated RFID Python protocols.

The shared validator owns execution, truth and isolation. Self-reported costs
are diagnostics only and never influence population selection.
"""
from __future__ import annotations

import hashlib
import json
import math
import importlib.util
from pathlib import Path

from generic_protocol.validation import validate_candidate
from generic_tasks import get_profile


def quality_summary(report):
    rows = report.get("runs", [])
    expected = report.get("total_runs", len(rows))
    semantic = report.get("semantic_validation") == "evaluated"
    scores = []
    for row in rows:
        score = 0.0
        if row.get("execution_succeeded") and row.get("output_contract_passed") and semantic:
            actual, target = row.get("estimated_values", []), row.get("target_values", [])
            profile = get_profile(report["task_profile"])
            if profile.metric_kind == "set":
                a, b = set(actual), set(target)
                score = 2 * len(a & b) / (len(a) + len(b)) if a or b else 1.0
            elif len(actual) == len(target) == 1:
                error = abs(float(actual[0]) - float(target[0])) / max(abs(float(target[0])), 1.0)
                score = 1.0 / (1.0 + error) if math.isfinite(error) else 0.0
        scores.append(score)
    eligible = (report.get("is_valid") is True and semantic
                and report.get("strict_information_isolation_passed") is True
                and report.get("security_mode") == "strict"
                and len(rows) == expected and expected > 0)
    models = {row.get("channel_model_id") for row in rows}
    measured = (eligible and len(models) == 1 and None not in models
                and all(row.get("cost_origin") == "instrumented_channel"
                        and row.get("cost_status") == "measured"
                        and isinstance(row.get("communication_cost_ms"), (int, float))
                        and row["communication_cost_ms"] > 0 for row in rows))
    return {
        "protocol": report["protocol"],
        "source_sha256": report.get("source_sha256"),
        "selection_eligible": eligible,
        "semantic_available": semantic,
        "quality_pass_rate": sum(row.get("semantic_hit") is True for row in rows) / max(1, expected) if semantic else None,
        "quality_score": sum(scores) / max(1, expected) if semantic else None,
        "score_kind": "set_f1" if get_profile(report["task_profile"]).metric_kind == "set" else "inverse_scaled_error",
        "dataset_hashes": [row["dataset_hash"] for row in rows],
        "completed_runs": report.get("completed_runs", 0),
        "final_decision": report.get("final_decision"),
        "exclusion_reasons": report.get("exclusion_reasons", []),
        "cost_scope": "platform_software_channel" if measured else "candidate_self_reported_not_used_for_selection",
        "communication_eligible": measured,
        "channel_model_id": next(iter(models)) if measured else None,
        "mean_communication_cost_ms": sum(row["communication_cost_ms"] for row in rows) / len(rows) if measured else None,
        "formal_benchmark_eligible": False,
    }


def evaluate_protocol(path, name, generator, config, *, deadline=None):
    report = validate_candidate(path, name, generator, **config, deadline=deadline)
    from course_project.evolution_selection import structural_hash
    summary = quality_summary(report)
    summary["structural_sha256"] = structural_hash(Path(path).read_text(encoding="utf-8-sig"), name)
    return {"summary": summary, "validation": report}


def select_population(names, evaluations, size, *, objective="quality_only", minimum_pass_rate=1., incumbents=None):
    """Keep elites; ties retain incumbents and failed candidates cannot enter."""
    from course_project.evolution_selection import select
    return select(names, evaluations, size, objective=objective, minimum_pass_rate=minimum_pass_rate, incumbents=incumbents)["population"]


def feedback_for(population, evaluations):
    rows = [evaluations[name]["summary"] for name in population]
    return ("平台固定开发集评价反馈（不是盲测）：\n"
            "优先修复执行、隔离与输出契约问题，再提高完整答案通过率和质量分数。"
            "质量分为集合 F1 或 1/(1+归一化绝对误差)，不使用自报时间排序。"
            "不得硬编码样本答案，不得把该开发集结果当作泛化收益。\n"
            + json.dumps(rows, ensure_ascii=False, allow_nan=False))


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
    return hashlib.sha256(data).hexdigest()


def independent_validation(evidence_dir, winner, baseline, evaluations, config, *, deadline=None):
    """Evaluate frozen sources once, after search; never return feedback to it."""
    root = Path(evidence_dir)
    frozen = root / "holdout"
    frozen.mkdir()
    task_path = frozen / "Task.py"
    task_path.write_bytes((root / "Task.source.txt").read_bytes())
    spec = importlib.util.spec_from_file_location("frozen_holdout_" + root.name, task_path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(task_path.read_bytes(), str(task_path), "exec"), module.__dict__)
    held_config = {**config, "seed": config["seed"] + 100000}
    record = {"schema_version": "1.0", "split": "holdout", "used_for_selection": False,
              "winner": winner, "baseline": baseline, "config": held_config, "evaluations": {},
              "artifact_hashes": {"Task.py": hashlib.sha256(task_path.read_bytes()).hexdigest()}}
    # Freeze the whole comparison before evaluating any of it.
    for name in dict.fromkeys((winner, baseline)):
        path = frozen / (name + ".py")
        path.write_bytes((root / (name + ".source.txt")).read_bytes())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != evaluations[name]["summary"]["source_sha256"]:
            raise ValueError("frozen candidate hash differs from selected source")
        record["artifact_hashes"][path.name] = digest
    save_json(frozen / "selection.json", record)
    for name in dict.fromkeys((winner, baseline)):
        record["evaluations"][name] = evaluate_protocol(frozen / (name + ".py"), name, module.generate_tags, held_config, deadline=deadline)
        save_json(frozen / "comparison.json", record)
    hashes = [tuple(item["summary"]["dataset_hashes"]) for item in record["evaluations"].values()]
    development = {h for item in evaluations.values() for h in item["summary"]["dataset_hashes"]}
    overlap = sorted(development.intersection(h for group in hashes for h in group))
    record["overlapping_dataset_hashes"] = overlap
    record["datasets_comparable"] = len(set(hashes)) == 1
    record["independent"] = not overlap and record["datasets_comparable"] and all(
        item["summary"]["completed_runs"] == held_config["total_runs"]
        for item in record["evaluations"].values())
    record["status"] = "evaluated" if record["independent"] else "independence_not_established"
    a, b = (record["evaluations"][name]["summary"] for name in (winner, baseline))
    record["winner_passed"] = record["independent"] and a["final_decision"] == "passed"
    record["communication_reduction_fraction"] = (1 - a["mean_communication_cost_ms"] / b["mean_communication_cost_ms"]
        if record["winner_passed"] and a["communication_eligible"] and b["communication_eligible"]
        and b["final_decision"] == "passed" and a["channel_model_id"] == b["channel_model_id"] else None)
    save_json(frozen / "comparison.json", record)
    return {key: record[key] for key in ("status", "independent", "winner", "baseline", "winner_passed", "communication_reduction_fraction")}

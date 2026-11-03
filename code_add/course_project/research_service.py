"""Versioned candidates and reproducible post-search Python evaluations."""
from __future__ import annotations

import ast
import csv
import hashlib
import json
import math
import re
import statistics
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from benchmark.run_config import SUPPORTED_DISTRIBUTIONS, CANONICAL_PROFILE_TASKS
from benchmark.scenarios import generate_tags
from course_project.candidate_evaluation import quality_summary, save_json
from course_project.http_support import APIError
from course_project.jobs import JobCancelled
from course_project.task_process import check_cancelled
from course_project.validation import bounded_int, bounded_float
from generic_protocol.validation import validate_candidate
from generic_tasks import get_profile
from stat_protocols.contracts import TaskSpec

ROOT = Path(__file__).resolve().parents[1]
LOCK = threading.RLock()
PYTHON_PROFILES = ["mode_query", "mean_estimation", "variance_estimation", "median_quantile", "cardinality_estimation", "top_k"]


def capabilities():
    return {"schema_version": "1.0", "families": {
        "python": {"label": "Python 协议", "profiles": PYTHON_PROFILES, "engine": "strict_python_channel",
                   "cost_model": "histogram_or_v1_slot001"},
        "statistics": {"label": "统计 JSON 算子", "profiles": list(CANONICAL_PROFILE_TASKS),
                       "engine": "statistical_operator_benchmark", "cost_model": "statistics_histogram"}},
        "distributions": sorted(SUPPORTED_DISTRIBUTIONS), "splits": ["validation", "blind"],
        "limits": {"candidates": 6, "cases": 240, "seeds_per_split": 10, "max_seconds": 3600},
        "comparison_rule": "same task, input hashes, split, scenario and measured channel model only"}


def _path(value):
    if not isinstance(value, str) or not value:
        raise ValueError("资产路径不能为空")
    path = (ROOT / value).resolve()
    if ROOT.resolve() not in path.parents or not path.is_file():
        raise ValueError("候选必须来自项目内已有资产")
    return path


def _store():
    return ROOT / "course_project/outputs/research"


def _relative(path):
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def candidate(candidate_id):
    if not isinstance(candidate_id, str) or not re.fullmatch(r"[a-f0-9]{64}", candidate_id):
        raise ValueError("候选版本 ID 无效")
    folder = _store() / "candidates" / candidate_id
    record = json.loads((folder / "record.json").read_text(encoding="utf-8"))
    frozen = _path(record["snapshot_path"])
    if hashlib.sha256(frozen.read_bytes()).hexdigest() != record["source_sha256"]:
        raise APIError("冻结候选完整性校验失败", 409, code="candidate_integrity_failed", stage="freeze")
    return record


def register(payload, *, search_hashes=None, provenance=None, evidence_path=None):
    family = payload.get("family", "python")
    path = _path(payload.get("path"))
    scene = str(payload.get("scene") or "")
    profile = str(payload.get("task_profile") or "mode_query")
    if family == "python":
        get_profile(profile)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", path.stem) or path.suffix != ".py":
            raise ValueError("Python 候选必须是有效协议文件")
        rel = path.relative_to(ROOT.resolve())
        if not (rel.parts[0] == "base_protocol" or len(rel.parts) == 3 and re.fullmatch(r"generation_?\d+", rel.parts[1])):
            raise ValueError("请登记基线库或场景世代目录中的协议")
        if not scene or not re.fullmatch(r"[\w-]+", scene) or not (ROOT / scene / "Task.py").is_file():
            raise ValueError("请选择已有场景")
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            measured = any(isinstance(node, ast.FunctionDef) and node.name == "execute_channel" for node in ast.walk(tree))
        except SyntaxError:
            measured = False  # Preserve invalid candidates for a readable validation report.
        supported = [profile] if profile in PYTHON_PROFILES else []
        origin = provenance or ("baseline" if rel.parts[0] == "base_protocol" else "imported_source")
    elif family == "statistics":
        from stat_protocols.genotype import compile_candidate
        if path.suffix != ".json" or "stat_protocols/llm/" not in _relative(path):
            raise ValueError("请选择统计候选记录")
        record = json.loads(path.read_text(encoding="utf-8"))
        compile_candidate(record)
        measured, supported, origin = True, list(CANONICAL_PROFILE_TASKS), "statistics_record"
        scene, profile = "", ""
    else:
        raise ValueError("未知候选类型")
    content = path.read_bytes()
    if len(content) > 2_000_000:
        raise ValueError("候选文件超过 2 MB")
    digest = hashlib.sha256(content).hexdigest()
    if payload.get("expected_sha256") and payload["expected_sha256"] != digest:
        raise APIError("协议源码已经变化，请重新验证或登记当前版本。", 409,
            code="candidate_source_changed", stage="freeze")
    identity = hashlib.sha256(json.dumps([family, scene, profile, path.stem, digest]).encode()).hexdigest()
    folder = _store() / "candidates" / identity
    with LOCK:
        if (folder / "record.json").is_file():
            old = candidate(identity)
            if search_hashes is not None:
                old["search_dataset_hashes"] = sorted(set(old.get("search_dataset_hashes", [])) | set(search_hashes))
                old["search_history_known"] = True
                if evidence_path:
                    old["search_evidence_path"] = evidence_path
                save_json(folder / "record.json", old)
            return old
        folder.mkdir(parents=True, exist_ok=True)
        snapshot = folder / path.name
        snapshot.write_bytes(content)
        record = {"schema_version": "1.0", "id": identity, "family": family, "name": path.stem,
            "scene": scene, "task_profile": profile, "supported_profiles": supported,
            "source_path": _relative(path), "snapshot_path": _relative(snapshot), "source_sha256": digest,
            "instrumented_entry": measured, "created_at": datetime.now(timezone.utc).isoformat(),
            "provenance": origin, "search_dataset_hashes": sorted(set(search_hashes or [])),
            "search_history_known": search_hashes is not None or origin == "baseline",
            "search_evidence_path": evidence_path}
        if family == "statistics":
            record["statistics_candidate_id"] = json.loads(content)["candidate_id"]
        save_json(folder / "record.json", record)
    return record


def list_candidates(query=None):
    rows, problems = [], []
    for path in sorted((_store() / "candidates").glob("*/record.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True):
        try:
            rows.append(candidate(path.parent.name))
        except (ValueError, OSError, APIError):
            problems.append({"id": path.parent.name, "error": "integrity_or_record_error"})
    return {"schema_version": "1.0", "candidates": rows, "problems": problems}


def record_validation_usage(candidate_id, report):
    """Remember exposed validation inputs without claiming unknown search history."""
    with LOCK:
        record = candidate(candidate_id)
        if report.get("source_sha256") != record["source_sha256"]:
            raise APIError("验证报告与冻结候选源码不一致。", 409, code="candidate_source_changed", stage="validation")
        hashes = [row["dataset_hash"] for row in report.get("runs", []) if row.get("dataset_hash")]
        record["validation_dataset_hashes"] = sorted(set(record.get("validation_dataset_hashes", [])) | set(hashes))
        save_json(_store() / "candidates" / candidate_id / "record.json", record)


def register_generated(path, scene, profile, *, report=None, evidence=None):
    hashes = None
    if report is not None and report.get("runs"):
        samples = report["runs"]
        if all(row.get("dataset_hash") for row in samples):
            hashes = [row["dataset_hash"] for row in samples]
    if evidence:
        run_path = _path(evidence)
        hashes = []
        for item in run_path.parent.glob("*.evaluation.json"):
            hashes.extend(json.loads(item.read_text(encoding="utf-8"))["summary"]["dataset_hashes"])
        held = run_path.parent / "holdout/comparison.json"
        if held.is_file():
            for item in json.loads(held.read_text(encoding="utf-8")).get("evaluations", {}).values():
                hashes.extend(item["summary"]["dataset_hashes"])
    return register({"family": "python", "path": str(path), "scene": scene, "task_profile": profile},
        search_hashes=hashes, provenance="generated_protocol", evidence_path=evidence)


def _ints(value, name, minimum, maximum, count=10):
    if not isinstance(value, list) or not 1 <= len(value) <= count or any(type(n) is not int or not minimum <= n <= maximum for n in value) or len(set(value)) != len(value):
        raise ValueError(f"{name} 需为不重复整数数组，范围 {minimum}..{maximum}，最多 {count} 项")
    return value


def _strings(value, allowed, name, count=6):
    if not isinstance(value, list) or not 1 <= len(value) <= count or any(not isinstance(n, str) or n not in allowed for n in value) or len(set(value)) != len(value):
        raise ValueError(f"{name} 为空、重复或包含不支持的值")
    return value


def normalize(payload):
    ids = payload.get("candidate_ids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 6 or any(not isinstance(value, str) for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("请选择 1..6 个不同候选版本")
    records = [candidate(value) for value in ids]
    families = {row["family"] for row in records}
    if len(families) != 1:
        raise APIError("Python 与统计 JSON 使用不同接口和通信模型，请分别运行。", 409,
                       code="incompatible_candidate_families", suggestion="选择同一类型候选，分别保存对照结果。")
    profile = str(payload.get("task_profile") or records[0]["task_profile"] or "mean_estimation")
    if any(profile not in row["supported_profiles"] for row in records):
        raise ValueError("候选登记任务与本次评测不一致，请按目标任务重新登记版本")
    validations = _ints(payload.get("validation_seeds", [1000001, 1000002]), "验证 Seeds", 0, 2**31 - 2)
    blinds = _ints(payload.get("blind_seeds", [2000001, 2000002]), "盲测 Seeds", 0, 2**31 - 2)
    if set(validations) & set(blinds):
        raise ValueError("验证与盲测 Seeds 不得重叠")
    distributions = _strings(payload.get("distributions", ["multi_peak", "uniform"]), SUPPORTED_DISTRIBUTIONS, "分布")
    sizes = _ints(payload.get("sizes", [80, 200]), "标签规模", 20, 200000, 3)
    perturbations = payload.get("perturbations", [{"missing_rate": 0, "noise_rate": 0, "extreme_rate": 0}, {"missing_rate": .1, "noise_rate": .1, "extreme_rate": 0}])
    if not isinstance(perturbations, list) or not 1 <= len(perturbations) <= 4:
        raise ValueError("扰动配置需有 1..4 组")
    disturbances = []
    for row in perturbations:
        if not isinstance(row, dict) or set(row) - {"missing_rate", "noise_rate", "extreme_rate"}:
            raise ValueError("扰动配置字段无效")
        disturbances.append({key: bounded_float(row, key, 0, 0, 1) for key in ("missing_rate", "noise_rate", "extreme_rate")})
    if len({json.dumps(row, sort_keys=True) for row in disturbances}) != len(disturbances):
        raise ValueError("扰动场景不得重复")
    cases = len(ids) * len(distributions) * len(sizes) * len(disturbances) * (len(validations) + len(blinds))
    if cases > 240:
        raise ValueError("Web 评测最多 240 个候选/场景/种子组合，请缩小配置")
    low = bounded_int(payload, "price_min", 1, 1, 100000)
    high = bounded_int(payload, "price_max", 64, low + 2, min(low + 9999, 1000000))
    baseline = payload.get("baseline_id") or ids[0]
    if baseline not in ids:
        raise ValueError("基线版本必须在所选候选中")
    return records, {"schema_version": "1.0", "candidate_ids": ids, "family": records[0]["family"],
        "task_profile": profile, "baseline_id": baseline, "validation_seeds": validations, "blind_seeds": blinds,
        "distributions": distributions, "sizes": sizes, "perturbations": disturbances,
        "price_min": low, "price_max": high, "relative_tolerance": bounded_float(payload, "relative_tolerance", .15, 0, 1),
        "absolute_tolerance": bounded_float(payload, "absolute_tolerance", 2, 0, 1e12),
        "quantile": bounded_float(payload, "quantile", .5, 0, 1),
        "minimum_pass_rate": bounded_float(payload, "minimum_pass_rate", 1, 0, 1),
        "timeout_seconds": bounded_int(payload, "timeout_seconds", 10, 1, 60),
        "max_seconds": bounded_int(payload, "max_seconds", 1800, 1, 3600), "case_count": cases}


def _wilson(passed, total):
    z, n = 1.95996398454, max(total, 1)
    p = passed / n
    center = (p + z*z/(2*n)) / (1+z*z/n)
    half = z * math.sqrt(p*(1-p)/n+z*z/(4*n*n)) / (1+z*z/n)
    return [max(0, center-half), min(1, center+half)]


def summarize(rows, config, expected, references):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["candidate_id"], row["split"], row["distribution"], row["num_tags"], row["perturbation"])].append(row)
    summaries = []
    for key, group in groups.items():
        cid, split, distribution, size, perturbation = key
        n = len(config[split + "_seeds"])
        passes = sum(row["report"]["final_decision"] == "passed" for row in group)
        costs = [row["quality"]["mean_communication_cost_ms"] for row in group if row["quality"]["communication_eligible"]]
        models = {row["quality"]["channel_model_id"] for row in group}
        hashes = [h for row in group for h in row["quality"]["dataset_hashes"]]
        overlap = sorted(set(hashes) & references)
        reasons = []
        if len(group) != n: reasons.append("incomplete_runs")
        if passes / n < config["minimum_pass_rate"]: reasons.append("quality_threshold_not_met")
        if len(costs) != n or len(models) != 1 or None in models: reasons.append("unmeasured_or_different_channel")
        if overlap: reasons.append("dataset_overlaps_recorded_search_or_validation")
        if len(hashes) != n or len(set(hashes)) != n: reasons.append("missing_or_repeated_datasets")
        summaries.append({"candidate_id": cid, "name": group[0]["name"], "split": split, "distribution": distribution,
            "num_tags": size, "perturbation": perturbation, "completed": len(group), "expected": n,
            "pass_rate": passes / n, "pass_ci95": _wilson(passes, n), "mean_cost_ms": statistics.mean(costs) if len(costs) == n else None,
            "channel_model_id": next(iter(models)) if len(models) == 1 else None, "dataset_hashes": hashes,
            "overlap_hashes": overlap, "eligible": not reasons, "exclusion_reasons": reasons,
            "is_pareto": False, "cost_reduction_fraction": None})
    for row in summaries:
        peers = [other for other in summaries if all(other[key] == row[key] for key in ("split", "distribution", "num_tags", "perturbation"))]
        baseline = next((other for other in peers if other["candidate_id"] == config["baseline_id"]), None)
        if baseline and baseline["eligible"] and row["eligible"] and baseline["dataset_hashes"] == row["dataset_hashes"] and baseline["channel_model_id"] == row["channel_model_id"]:
            row["cost_reduction_fraction"] = 1 - row["mean_cost_ms"] / baseline["mean_cost_ms"]
        row["is_pareto"] = row["eligible"] and not any(other["eligible"] and other["channel_model_id"] == row["channel_model_id"] and other["dataset_hashes"] == row["dataset_hashes"] and other["mean_cost_ms"] <= row["mean_cost_ms"] and other["pass_rate"] >= row["pass_rate"] and (other["mean_cost_ms"] < row["mean_cost_ms"] or other["pass_rate"] > row["pass_rate"]) for other in peers)
    return summaries


def run_evaluation(folder, records, config, update):
    import time
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": "1.0", "run_id": folder.name, "status": "running", "config": config,
        "candidates": records, "frozen_before_evaluation": True, "used_for_selection": False,
        "scope": "python_post_search_generalization", "created_at": datetime.now(timezone.utc).isoformat(), "artifact_hashes": {}}
    rows, references, validation_hashes = [], set(), set()
    for record in records:
        references.update(record.get("search_dataset_hashes", []))
        references.update(record.get("validation_dataset_hashes", []))
    history_known = all(row["search_history_known"] for row in records)
    manifest["search_history_known"] = history_known
    manifest["independence_status"] = "pending"
    # Freeze the exact bytes and configuration before executing any case.
    frozen_paths = {}
    for record in records:
        checked = candidate(record["id"])
        data = _path(checked["snapshot_path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != record["source_sha256"]:
            raise APIError("候选在冻结时发生变化，请重新登记。", 409, code="candidate_integrity_failed", stage="freeze")
        name = record["id"] + ".py"
        (folder / name).write_bytes(data)
        frozen_paths[record["id"]] = folder / name
        manifest["artifact_hashes"][name] = hashlib.sha256(data).hexdigest()
    manifest["artifact_hashes"]["selection.json"] = save_json(folder / "selection.json", manifest)
    deadline = time.monotonic() + config["max_seconds"]
    try:
        for split in ("validation", "blind"):
            for distribution in config["distributions"]:
                for size in config["sizes"]:
                    for disturbance in config["perturbations"]:
                        for seed in config[split + "_seeds"]:
                            # TaskSpec supplies only the statistical data generator here.
                            # Python mode/quantile semantics remain with their task profile.
                            data_task = CANONICAL_PROFILE_TASKS.get(config["task_profile"], "mean")
                            spec = TaskSpec(task=data_task, tasks=(data_task,), distribution=distribution, num_tags=size,
                                price_min=config["price_min"], price_max=config["price_max"], seed=seed, split=split, **disturbance)
                            def generator(num_tags, price_min, price_max):
                                return generate_tags(spec), {}
                            for record in records:
                                check_cancelled(update)
                                if time.monotonic() >= deadline:
                                    raise TimeoutError("research_evaluation_time_budget")
                                frozen_path = frozen_paths[record["id"]]
                                if hashlib.sha256(frozen_path.read_bytes()).hexdigest() != record["source_sha256"]:
                                    raise APIError("运行源码快照完整性校验失败。", 409, code="candidate_integrity_failed", stage="evaluation")
                                report = validate_candidate(frozen_path, record["name"], generator,
                                    task_profile=config["task_profile"], scene=record["scene"], security_mode="strict", seed=seed, total_runs=1,
                                    num_tags=size, price_min=config["price_min"], price_max=config["price_max"],
                                    relative_tolerance=config["relative_tolerance"], absolute_tolerance=config["absolute_tolerance"], quantile=config["quantile"],
                                    timeout_seconds=config["timeout_seconds"], deadline=deadline,
                                    cancel_check=lambda: check_cancelled(update))
                                row = {"candidate_id": record["id"], "name": record["name"], "split": split,
                                    "distribution": distribution, "num_tags": size, "perturbation": json.dumps(disturbance, sort_keys=True),
                                    "seed": seed, "task_profile": config["task_profile"],
                                    "data_spec": spec.to_dict(), "quality": quality_summary(report), "report": report}
                                rows.append(row)
                                if split == "validation": validation_hashes.update(row["quality"]["dataset_hashes"])
                                save_json(folder / "results.json", {"rows": rows})
                                update(stage=split, progress=int(90 * len(rows) / config["case_count"]),
                                    log=f"{split}: {record['name']} / {distribution} / {size} / Seed {seed}: {report['final_decision']}",
                                    result={"run_id": folder.name, "completed": len(rows), "expected": config["case_count"], "partial": True})
        manifest["status"] = "success"
    except BaseException as exc:
        manifest["status"] = "cancelled" if isinstance(exc, JobCancelled) else "failed"
        manifest["failure_type"] = type(exc).__name__
        raise
    finally:
        summaries = summarize(rows, config, config["case_count"], references)
        blind_hashes = {h for row in rows if row["split"] == "blind" for h in row["quality"]["dataset_hashes"]}
        overlap = blind_hashes & (references | validation_hashes)
        for row in summaries:
            if row["split"] == "blind" and set(row["dataset_hashes"]) & validation_hashes:
                row["eligible"] = row["is_pareto"] = False
                row["cost_reduction_fraction"] = None
                row["exclusion_reasons"].append("validation_blind_dataset_overlap")
        all_executed = len(rows) == config["case_count"] and all(
            row["report"].get("completed_runs") == 1 and len(row["quality"]["dataset_hashes"]) == 1
            and row["report"].get("source_sha256") == next(record["source_sha256"] for record in records if record["id"] == row["candidate_id"])
            for row in rows)
        all_overlap = {h for row in rows for h in row["quality"]["dataset_hashes"]} & references
        manifest["independence_status"] = "overlap_detected" if overlap or all_overlap else "verified_against_recorded_history" if history_known and all_executed and manifest["status"] == "success" else "not_established"
        manifest["overlap_hashes"] = sorted(overlap | all_overlap)
        manifest["completed"] = len(rows)
        manifest["artifact_hashes"]["results.json"] = save_json(folder / "results.json", {"rows": rows})
        manifest["artifact_hashes"]["summary.json"] = save_json(folder / "summary.json", {"rows": summaries})
        with (folder / "summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            keys = ["candidate_id", "name", "split", "distribution", "num_tags", "perturbation", "completed", "expected", "pass_rate", "pass_ci95", "mean_cost_ms", "channel_model_id", "eligible", "exclusion_reasons", "cost_reduction_fraction"]
            writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(summaries)
        manifest["artifact_hashes"]["summary.csv"] = hashlib.sha256((folder / "summary.csv").read_bytes()).hexdigest()
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        save_json(folder / "manifest.json", manifest)
        if update:
            update(result={"run_id": folder.name, "completed": len(rows), "expected": config["case_count"], "partial": manifest["status"] != "success"})
    return {"run_id": folder.name, "completed": len(rows), "expected": config["case_count"],
            "independence_status": manifest["independence_status"], "evidence_path": _relative(folder / "manifest.json")}


def start_evaluation(manager, payload):
    records, config = normalize(payload)
    if config["family"] == "statistics":
        if len(records) != 1 or len(config["perturbations"]) != 1:
            raise ValueError("统计 JSON 入口每次选择一个候选和一组扰动，与内置基线运行完整盲测流水线")
        from course_project.engineering_service import start_pipeline
        from stat_protocols.genotype import compile_candidate
        record = json.loads(_path(records[0]["snapshot_path"]).read_text(encoding="utf-8"))
        compile_candidate(record)
        development = _ints(payload.get("development_seeds", [10, 11]), "开发 Seeds", 0, 2**31-2)
        if set(development) & set(config["validation_seeds"] + config["blind_seeds"]):
            raise ValueError("开发、验证和盲测 Seeds 不得重叠")
        return start_pipeline(manager, {"config": {"name": "registered-" + records[0]["id"][:8],
            "profiles": [config["task_profile"]], "protocols": ["polling", "shared_sketch", "llm_random"],
            "distributions": config["distributions"], "sizes": config["sizes"], "seeds": development,
            "price_min": config["price_min"], "price_max": config["price_max"], "split": "development",
            "llm_candidate": record, **config["perturbations"][0]},
            "validation_seeds": config["validation_seeds"], "blind_seeds": config["blind_seeds"], "max_seconds": config["max_seconds"]})
    folder = _store() / "runs" / uuid4().hex
    return manager.submit("research_evaluation", config, lambda update: run_evaluation(folder, records, config, update))


def runs(query=None):
    query = query or {}
    run_id = query.get("id", [""])[0]
    if not run_id:
        paths = sorted((_store() / "runs").glob("*/manifest.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        return {"runs": [{key: record.get(key) for key in ("run_id", "status", "created_at", "completed", "independence_status", "config")}
            for record in (json.loads(path.read_text(encoding="utf-8")) for path in paths[:80])]}
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("运行 ID 无效")
    folder = _store() / "runs" / run_id
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    failures = [name for name, digest in manifest["artifact_hashes"].items()
        if not (folder / name).is_file() or hashlib.sha256((folder / name).read_bytes()).hexdigest() != digest]
    rows = json.loads((folder / "summary.json").read_text(encoding="utf-8"))["rows"]
    if failures or manifest["status"] != "success":
        for row in rows:
            row["eligible"] = row["is_pareto"] = False
            row["cost_reduction_fraction"] = None
            row["exclusion_reasons"].append("run_incomplete_or_integrity_failed")
    return {"manifest": manifest, "rows": rows, "audit": {"passed": not failures, "failures": failures},
        "artifacts": [{"name": p.name, "path": _relative(p)} for p in folder.iterdir() if p.is_file()]}

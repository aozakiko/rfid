"""Engineering integration for frozen experiments and recorded candidate lineage."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import queue
import threading
import time
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

from benchmark.evidence import comparison_rows, read_reports
from benchmark.provenance import audit_manifest
from benchmark.run_config import RunConfig
from course_project.http_support import APIError
from course_project.statistics_service import ROOT, EXPERIMENT_ROOT, SUITE_LOCK


def _relative(path):
    return Path(path).relative_to(ROOT).as_posix()


def _artifacts(root):
    return [{"name": p.relative_to(root).as_posix(), "path": _relative(p), "size": p.stat().st_size,
             "kind": p.suffix.lstrip(".")} for p in sorted(root.rglob("*")) if p.is_file()]


def _run(command, update, stage, timeout=1800):
    from course_project.task_process import run_process
    result = run_process(command, cwd=ROOT, timeout=timeout, update=update, stage=stage, env={"MPLBACKEND": "Agg"})
    if result["returncode"]:
        raise RuntimeError(f"{stage} failed with exit code {result['returncode']}; see task log")


def recorded_references():
    """Only audited completed recorded inputs; unknown historical use stays unknown."""
    paths = []
    for manifest_path in sorted(EXPERIMENT_ROOT.rglob("manifest.json")):
        folder = manifest_path.parent
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") not in (None, "success") or not audit_manifest(folder)["passed"]:
            continue
        candidates = [folder / "results.json"] if (folder / "results.json").is_file() else sorted(folder.glob("*/*_reports.json"))
        for path in candidates:
            payload = json.loads(path.read_text(encoding="utf-8"))
            reports = payload.get("reports", [])
            if reports and all(row.get("run_result", {}).get("metadata", {}).get("dataset_hash") for row in reports):
                paths.append(path)
    return paths


def config_run(query=None):
    query = query or {}
    root = EXPERIMENT_ROOT / "config_runs"
    available = sorted((p for p in root.glob("*") if p.is_dir() and re.fullmatch(r"[0-9a-f]{32}", p.name)),
                       key=lambda p: p.stat().st_mtime_ns, reverse=True)
    run_id = query.get("run_id", [""])[0]
    if run_id and not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("invalid run ID")
    folder = root / run_id if run_id else next((p for p in available if (p / "raw" / "manifest.json").is_file()), None)
    inventory = [{"run_id": p.name, "path": _relative(p)} for p in available[:20]]
    if folder is None:
        return {"available": False, "runs": inventory}
    manifest_path = folder / "raw" / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("run manifest not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_path = folder / "raw" / "results.json"
    rows = comparison_rows(read_reports(raw_path)) if raw_path.is_file() else []
    audit = audit_manifest(folder / "raw")
    if manifest.get("status") != "success" or not audit["passed"]:
        for row in rows:
            row.update(is_pareto=False, pareto_eligible=False)
            row["pareto_exclusion_reasons"].append("experiment_audit_failed")
    return {"available": True, "run_id": folder.name, "runs": inventory,
            "manifest": manifest, "audit": audit, "rows": rows,
            "artifacts": _artifacts(folder)}


def start_config(manager, payload):
    raw = payload.get("config")
    if not isinstance(raw, dict):
        raise ValueError("config must be a JSON object")
    run_id = uuid4().hex
    folder = EXPERIMENT_ROOT / "config_runs" / run_id
    raw = {**raw, "output_dir": _relative(folder / "raw")}
    try:
        config = RunConfig(**raw)
    except TypeError as exc:
        raise ValueError(f"invalid config fields: {exc}") from exc
    if len(config.seeds) > 30 or len(config.sizes) > 3 or len(config.distributions) * len(config.seeds) * len(config.sizes) * len(config.protocols) > 3000:
        raise ValueError("Web config allows at most 30 seeds, 3 sizes and 3000 protocol cases; use the CLI for larger runs")
    if not SUITE_LOCK.acquire(blocking=False):
        raise APIError("统计实验已经运行，请等待当前任务完成", 409)

    def work(update):
        try:
            folder.mkdir(parents=True)
            config_file = folder / "config.json"
            config_file.write_text(json.dumps(config.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            update(log=f"实验 {run_id}：固定配置已保存。", stage="benchmark", progress=10)
            references = recorded_references() if config.split in {"validation", "blind"} else []
            _run([sys.executable, "-u", "-m", "benchmark.run_config", "--config", str(config_file),
                  "--output-dir", str(folder / "raw")] + [v for p in references for v in ("--reference", str(p))], update, "benchmark")
            update(log="原始结果已保存，正在从原始数据重建表格和图表。", stage="evidence", progress=70)
            _run([sys.executable, "-u", "-m", "benchmark.evidence", str(folder / "raw"),
                  "--output-dir", str(folder / "derived")], update, "evidence")
            audit = audit_manifest(folder / "raw")
            if not audit["passed"]:
                raise RuntimeError("raw artifact audit failed")
            return {"run_id": run_id, "root": _relative(folder), "audit": audit}
        finally:
            SUITE_LOCK.release()
    try:
        return manager.submit("statistics_config", config.to_dict(), work, on_skipped=SUITE_LOCK.release)
    except Exception:
        SUITE_LOCK.release()
        raise


def start_rebuild(manager, payload):
    run_id = payload.get("run_id", "")
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("invalid run ID")
    folder = EXPERIMENT_ROOT / "config_runs" / run_id
    if not (folder / "raw" / "manifest.json").is_file():
        raise FileNotFoundError("run not found")
    if not audit_manifest(folder / "raw")["passed"]:
        raise ValueError("raw artifacts failed integrity audit; rebuild refused")
    if not SUITE_LOCK.acquire(blocking=False):
        raise APIError("统计实验已经运行，请等待当前任务完成", 409)

    def work(update):
        try:
            update(log="仅读取原始结果，重新计算汇总和图表。", stage="evidence", progress=20)
            _run([sys.executable, "-u", "-m", "benchmark.evidence", str(folder / "raw"),
                  "--output-dir", str(folder / "derived")], update, "evidence")
            return {"run_id": run_id, "root": _relative(folder), "audit": audit_manifest(folder / "raw")}
        finally:
            SUITE_LOCK.release()
    try:
        return manager.submit("statistics_rebuild", {"run_id": run_id}, work, on_skipped=SUITE_LOCK.release)
    except Exception:
        SUITE_LOCK.release()
        raise


def pipeline_run(query=None):
    from benchmark.pipeline import audit_pipeline
    query = query or {}
    root = EXPERIMENT_ROOT / "pipelines"
    paths = sorted(root.glob("*/pipeline_manifest.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    run_id = query.get("run_id", [""])[0]
    if run_id and not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("invalid pipeline ID")
    path = root / run_id / "pipeline_manifest.json" if run_id else next(iter(paths), None)
    runs = [{"run_id": p.parent.name} for p in paths[:20]]
    if path is None:
        return {"available": False, "runs": runs}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return {"available": True, "run_id": path.parent.name, "runs": runs, "manifest": manifest,
            "audit": audit_pipeline(path.parent), "artifacts": _artifacts(path.parent)}


def start_pipeline(manager, payload):
    from benchmark.pipeline import seed_groups, audit_pipeline
    from course_project.validation import bounded_int
    timeout = bounded_int(payload, "max_seconds", 1800, 1, 3600)
    raw = payload.get("config")
    if not isinstance(raw, dict):
        raise ValueError("config must be a JSON object")
    try:
        config = RunConfig(**{**raw, "output_dir": "server-controlled"})
    except TypeError as exc:
        raise ValueError(f"invalid config fields: {exc}") from exc
    _, validation, blind = seed_groups(config, payload.get("validation_seeds"), payload.get("blind_seeds"))
    cases = len(config.distributions) * len(config.seeds) * len(config.sizes) * len(config.protocols)
    if max(len(config.seeds), len(validation), len(blind)) > 30 or len(config.sizes) > 3 or cases > 1000:
        raise ValueError("Web pipeline allows 30 seeds, 3 sizes and 1000 base protocol cases; use CLI for larger runs")
    if not SUITE_LOCK.acquire(blocking=False):
        raise APIError("统计实验已经运行，请等待当前任务完成", 409)
    run_id = uuid4().hex
    folder = EXPERIMENT_ROOT / "pipelines" / run_id
    def work(update):
        try:
            config_folder = EXPERIMENT_ROOT / "pipeline_configs"
            config_folder.mkdir(parents=True, exist_ok=True)
            config_file = config_folder / f"{run_id}.json"
            config_file.write_text(json.dumps(config.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            update(log="已冻结配置：开发 → 验证 → 缺失/噪声/异常 → 盲测 → 双次复现", stage="pipeline", progress=5)
            references = recorded_references()
            _run([sys.executable, "-u", "-m", "benchmark.pipeline", "--config", str(config_file), "--output-dir", str(folder),
                  "--validation-seeds", ",".join(map(str, validation)), "--blind-seeds", ",".join(map(str, blind))]
                 + [v for p in references for v in ("--reference", str(p))], update, "pipeline", timeout=timeout)
            audit = audit_pipeline(folder)
            if not audit["passed"]:
                raise RuntimeError("pipeline artifact audit failed")
            return {"run_id": run_id, "kind": "pipeline", "audit": audit}
        finally:
            SUITE_LOCK.release()
    try:
        return manager.submit("statistics_pipeline", {"config": config.to_dict(), "validation_seeds": validation, "blind_seeds": blind}, work, on_skipped=SUITE_LOCK.release)
    except Exception:
        SUITE_LOCK.release()
        raise


def candidate_lineage(query=None):
    scene = (query or {}).get("scene", [""])[0]
    if scene and (not re.fullmatch(r"[\w-]+", scene) or not (ROOT / scene / "Task.py").is_file()):
        raise ValueError("invalid scene")
    paths = list((ROOT / scene).glob("generation*/*.candidate.json")) if scene else list(ROOT.glob("*/generation*/*.candidate.json"))
    paths.extend((ROOT / scene).glob("outputs/protocol_traces/*/*/attempt_*/candidate.json") if scene
                 else ROOT.glob("*/outputs/protocol_traces/*/*/attempt_*/candidate.json"))
    if not scene:
        paths.extend((EXPERIMENT_ROOT / "llm").glob("*/candidate.json"))
    nodes, errors, indexed, workflows = [], [], {}, {}
    for path in sorted(paths):
        if ROOT.resolve() not in path.resolve().parents:
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or not record.get("candidate_id"):
                raise ValueError("missing candidate_id")
            clean = {k: record.get(k) for k in ("candidate_id", "protocol_name", "candidate_type", "parents", "task_profile",
                "genotype", "prompt_hash", "response_hash", "model", "source_hash", "validation_result", "benchmark_result", "status", "usage", "provenance", "validation_error")}
            archived = path.parent.name.startswith("attempt_")
            source = path.parent / "protocol.py" if archived else path.with_name(path.name.replace(".candidate.json", ".py")) if path.name.endswith(".candidate.json") else None
            actual_hash = hashlib.sha256(source.read_bytes()).hexdigest() if source and source.is_file() else None
            genotype = clean.get("genotype") or {}
            node = {**clean, "parents": clean.get("parents") or [], "record_path": _relative(path), "record_paths": [_relative(path)],
                "scene": path.relative_to(ROOT).parts[0] if source else "stat_operator_json",
                "generation": genotype.get("generation") or (path.parent.name if source and not archived else None),
                "source_sha256": actual_hash, "source_hash_match": actual_hash == record.get("source_hash") if actual_hash else None,
                "workflow": None, "formal_results": []}
            if archived:
                workflow_path = path.parent.parent / "workflow.json"
                key = str(workflow_path)
                if key not in workflows:
                    from generic_protocol.journal import audit_journal
                    trace = json.loads(workflow_path.read_text(encoding="utf-8"))
                    evidence = []
                    for attempt in trace.get("attempts", []):
                        folder = workflow_path.parent / attempt["folder"]
                        if workflow_path.parent.resolve() not in folder.resolve().parents:
                            raise ValueError("invalid trace attempt path")
                        report_path = folder / "validation.json"
                        evaluation_path = folder / "evaluation.json"
                        evidence.append({**attempt, "validation": json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None,
                            "evaluation": json.loads(evaluation_path.read_text(encoding="utf-8")) if evaluation_path.is_file() else None,
                            "artifact_paths": {p.name: _relative(p) for p in folder.glob("*") if p.is_file()}})
                    workflows[key] = {"workflow_id": trace["workflow_id"], "status": trace["status"], "attempts": evidence,
                        "repair_count": max(0, len(evidence) - 1), "audit": audit_journal(workflow_path)}
                node["workflow"] = workflows[key]
            previous = indexed.get(node["candidate_id"])
            if previous:
                record_paths = previous["record_paths"] + node["record_paths"]
                # Immutable attempt evidence takes precedence over the mutable working file.
                if archived:
                    previous.update(node)
                previous["record_paths"] = record_paths
            else:
                indexed[node["candidate_id"]] = node
                nodes.append(node)
        except (OSError, ValueError, TypeError) as exc:
            errors.append({"path": _relative(path), "error": str(exc)})
    if not scene:
        for manifest_path in sorted(EXPERIMENT_ROOT.glob("runs/*/manifest.json")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                frozen = manifest.get("llm_record") or {}
                node = indexed.get(frozen.get("candidate_id"))
                if not node or not frozen.get("genotype") or frozen["genotype"] != node.get("genotype"):
                    continue
                if any(frozen.get(k) != node.get(k) for k in ("prompt_hash", "response_hash")):
                    continue
                audit = audit_manifest(manifest_path.parent)
                if not audit["passed"]:
                    errors.append({"path": _relative(manifest_path), "error": "formal experiment artifact audit failed"})
                    continue
                for raw_path in sorted(manifest_path.parent.glob("*/*_reports.json")):
                    reports = read_reports(raw_path)
                    protocols = {r.protocol for r in reports if r.run_result.metadata.get("candidate_id") == node["candidate_id"]}
                    if not protocols:
                        continue
                    # Keep competitors in the comparison before selecting this candidate's rows.
                    rows = [r for r in comparison_rows(reports) if r["protocol"] in protocols]
                    node["formal_results"].append({"run_path": _relative(manifest_path.parent), "mode": raw_path.parent.name,
                        "reproducibility_key": manifest.get("reproducibility_key"), "rows": rows, "audit_passed": True})
            except (OSError, ValueError, TypeError) as exc:
                errors.append({"path": _relative(manifest_path), "error": str(exc)})
    by_name = defaultdict(list)
    for node in nodes:
        by_name[(node["scene"], node.get("protocol_name"))].append(node["candidate_id"])
    ids = {n["candidate_id"] for n in nodes}
    edges = []
    for node in nodes:
        for parent in node["parents"]:
            recorded = next((p for p in (node.get("genotype") or {}).get("parent_evidence", []) if p.get("reference") == parent), None)
            exact_id = recorded.get("candidate_id") if recorded else None
            matches = [exact_id] if exact_id in ids else [parent] if parent in ids else by_name.get((node["scene"], parent), [])
            if recorded and recorded.get("source_sha256"):
                matches = [value for value in matches if indexed[value].get("source_hash") == recorded["source_sha256"]]
            edges.append({"parent_reference": parent, "parent_ids": matches, "child_id": node["candidate_id"],
                          "recorded_source_sha256": recorded.get("source_sha256") if recorded else None,
                          "resolution": "recorded" if len(matches) == 1 else "ambiguous" if matches else "unrecorded"})
    return {"nodes": nodes, "edges": edges, "errors": errors,
            "note": "仅展示已有候选记录；未记录的父代保留引用，不推断谱系或演化评价。"}

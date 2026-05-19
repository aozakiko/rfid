from __future__ import annotations

import argparse
import importlib.util
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_protocol_optimizer import TASK_GUIDANCE, run_ai_optimization_workflow
from backend_tools.openai_compatible import LLMConfig, OpenAICompatibleClient
from cloud_edge_demo.pipeline import run_distributed_demo
from prompt_generation import create_crossover_prompts
from Validator import ProtocolValidator
from course_project.artifacts import export_course_result, make_run_dir
from course_project.benchmark import run_scalability_benchmark
from course_project.dashboard import generate_dashboard
from course_project.database import (
    database_stats,
    get_design_case,
    get_run_detail,
    init_database,
    list_design_cases,
    list_runs,
    list_topology,
    store_ai_job,
    store_course_run,
    store_design_case,
    store_security_check,
)
from course_project.platform_core import (
    OPTIMIZATION_PRIORITIES,
    SCENARIO_PRESETS,
    TASK_LIBRARY,
    build_design_case,
)
from course_project.security import run_security_check
from course_project.selfopt_ui import INDEX_HTML as ADVANCED_INDEX_HTML


COURSE_ROOT = Path(__file__).resolve().parent
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
GENERATION_RE = re.compile(r"^generation_?(\d+)$")
TEXT_FILE_SUFFIXES = {".py", ".txt", ".json", ".csv", ".md", ".log", ".html"}
MAX_WEB_FILE_BYTES = 900_000
BACKGROUND_JOBS: dict[str, dict[str, Any]] = {}
BACKGROUND_JOBS_LOCK = threading.Lock()


def discover_scenes() -> list[str]:
    scenes: list[str] = []
    for child in PROJECT_ROOT.iterdir():
        if not child.is_dir():
            continue
        if (child / "Task.py").exists() and any(p.is_dir() and p.name.startswith("generation") for p in child.iterdir()):
            scenes.append(child.name)
    return sorted(scenes)


def discover_protocols(scene: str) -> list[str]:
    names: set[str] = set()
    scene_path = PROJECT_ROOT / scene
    if scene_path.exists():
        for generation_dir in scene_path.iterdir():
            if generation_dir.is_dir() and generation_dir.name.startswith("generation"):
                for file_path in generation_dir.glob("*.py"):
                    if file_path.stem != "__init__":
                        names.add(file_path.stem)
    base_path = PROJECT_ROOT / "base_protocol"
    if base_path.exists():
        for file_path in base_path.glob("*.py"):
            if file_path.stem != "__init__":
                names.add(file_path.stem)
    return sorted(names)


def _generation_sort_key(path: Path) -> tuple[int, str]:
    match = GENERATION_RE.match(path.name)
    if match:
        return int(match.group(1)), path.name
    return 10_000, path.name


def _validate_identifier(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not IDENTIFIER_RE.match(text):
        raise ValueError(f"{label} 必须是合法 Python 标识符")
    return text


def _validate_scene(scene: str, *, allow_new: bool = False) -> str:
    name = _validate_identifier(scene, "场景名称")
    if not allow_new and name not in discover_scenes():
        raise ValueError(f"未知场景: {name}")
    return name


def _scene_path(scene: str, *, allow_new: bool = False) -> Path:
    name = _validate_scene(scene, allow_new=allow_new)
    path = (PROJECT_ROOT / name).resolve()
    root = PROJECT_ROOT.resolve()
    if path != root and root not in path.parents:
        raise ValueError("场景路径越界")
    return path


def _resolve_project_path(relative_path: str) -> Path:
    text = str(relative_path or "").replace("\\", "/").strip()
    if not text or "\x00" in text:
        raise ValueError("文件路径不能为空")
    raw = Path(text)
    if raw.is_absolute():
        raise ValueError("只允许访问项目内相对路径")
    candidate = (PROJECT_ROOT / raw).resolve()
    root = PROJECT_ROOT.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("文件路径越界")
    return candidate


def _relative_to_project(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


def _assert_text_file(path: Path) -> None:
    if path.suffix.lower() not in TEXT_FILE_SUFFIXES:
        raise ValueError(f"暂不支持读取 {path.suffix or '无后缀'} 文件")
    if path.exists() and path.stat().st_size > MAX_WEB_FILE_BYTES:
        raise ValueError("文件过大，不适合在 Web 编辑器中打开")


def _assert_editable_project_file(path: Path) -> None:
    _assert_text_file(path)
    rel = _relative_to_project(path)
    parts = Path(rel).parts
    if not parts:
        raise ValueError("文件路径无效")
    if parts[0] == "base_protocol" and len(parts) == 2 and path.suffix == ".py":
        return
    if parts[0] in discover_scenes():
        if len(parts) == 2 and parts[1] in {"Task.py", "config.py", "README.md"}:
            return
        if len(parts) >= 3 and (
            parts[1].startswith("generation") or parts[1] == "prompts" or parts[1] == "outputs"
        ):
            return
    raise ValueError("该路径不属于可编辑的自优化项目资产")


def _protocol_names(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = re.split(r"[\s,，;；]+", value)
    elif isinstance(value, list):
        raw = value
    else:
        raw = []
    names: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        names.append(_validate_identifier(text, "协议名称"))
    return list(dict.fromkeys(names))


def _protocol_patterns(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = re.split(r"[\s,，;；]+", value)
    elif isinstance(value, list):
        raw = value
    else:
        raw = []
    patterns: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        if text != "all" and not re.match(r"^[A-Za-z_][A-Za-z0-9_*?]*$", text):
            raise ValueError(f"协议通配符不合法: {text}")
        patterns.append(text)
    return list(dict.fromkeys(patterns))


def _protocol_file_rows(scene: str) -> list[dict[str, Any]]:
    scene_dir = _scene_path(scene)
    rows: list[dict[str, Any]] = []
    for generation_dir in sorted(
        [p for p in scene_dir.iterdir() if p.is_dir() and p.name.startswith("generation")],
        key=_generation_sort_key,
    ):
        for file_path in sorted(generation_dir.glob("*.py")):
            if file_path.stem == "__init__":
                continue
            rows.append(
                {
                    "name": file_path.stem,
                    "generation": generation_dir.name,
                    "path": _relative_to_project(file_path),
                    "size": file_path.stat().st_size,
                    "mtime": int(file_path.stat().st_mtime),
                }
            )
    return rows


def _base_protocol_rows() -> list[dict[str, Any]]:
    base_dir = PROJECT_ROOT / "base_protocol"
    if not base_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for file_path in sorted(base_dir.glob("*.py")):
        if file_path.stem == "__init__":
            continue
        rows.append(
            {
                "name": file_path.stem,
                "path": _relative_to_project(file_path),
                "size": file_path.stat().st_size,
                "mtime": int(file_path.stat().st_mtime),
            }
        )
    return rows


def _prompt_rows(scene: str, *, limit: int = 60) -> list[dict[str, Any]]:
    prompt_dir = _scene_path(scene) / "prompts"
    if not prompt_dir.exists():
        return []
    files = sorted(prompt_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {
            "name": file_path.name,
            "path": _relative_to_project(file_path),
            "size": file_path.stat().st_size,
            "mtime": int(file_path.stat().st_mtime),
        }
        for file_path in files[:limit]
    ]


def _experiment_artifact_rows(*, limit: int = 80) -> list[dict[str, Any]]:
    roots = [PROJECT_ROOT / "Experiments"]
    suffixes = {".csv", ".json", ".pdf", ".png", ".svg", ".txt", ".log"}
    rows: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for file_path in root.glob("*"):
            if not file_path.is_file() or file_path.suffix.lower() not in suffixes:
                continue
            resolved = file_path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            rows.append(
                {
                    "name": file_path.name,
                    "path": _relative_to_project(file_path),
                    "size": file_path.stat().st_size,
                    "mtime": int(file_path.stat().st_mtime),
                    "kind": file_path.suffix.lower().lstrip("."),
                }
            )
    rows.sort(key=lambda row: row["mtime"], reverse=True)
    return rows[:limit]


def _evolution_scenario_names() -> list[str]:
    names: list[str] = []
    data_path = PROJECT_ROOT / "Experiments" / "evolution_data_combined.json"
    if data_path.exists():
        try:
            with data_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                names.extend(str(key) for key in payload.keys())
        except (OSError, json.JSONDecodeError):
            pass
    if not names:
        names.extend(["single_peak", "multi_peak"])
    for scene in discover_scenes():
        if scene not in names:
            names.append(scene)
    return names


def _output_artifact_rows(*, limit: int = 240) -> list[dict[str, Any]]:
    output_root = COURSE_ROOT / "outputs"
    suffixes = {".csv", ".json", ".html", ".md", ".txt", ".log", ".pdf", ".png", ".svg"}
    rows: list[dict[str, Any]] = []
    if not output_root.exists():
        return rows
    for file_path in output_root.rglob("*"):
        if not file_path.is_file() or file_path.suffix.lower() not in suffixes:
            continue
        try:
            rel = _relative_to_project(file_path)
        except ValueError:
            continue
        run_name = file_path.parent.name if file_path.parent != output_root else "outputs"
        rows.append(
            {
                "name": file_path.name,
                "run": run_name,
                "path": rel,
                "size": file_path.stat().st_size,
                "mtime": int(file_path.stat().st_mtime),
                "kind": file_path.suffix.lower().lstrip(".") or "file",
            }
        )
    rows.sort(key=lambda row: row["mtime"], reverse=True)
    return rows[:limit]


def _assert_safe_raw_artifact(path: Path) -> None:
    resolved = path.resolve()
    allowed_roots = [(COURSE_ROOT / "outputs").resolve(), (PROJECT_ROOT / "Experiments").resolve()]
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise ValueError("只能预览 outputs 或 Experiments 中的产物")
    if not resolved.is_file():
        raise FileNotFoundError(f"找不到产物文件: {resolved}")


def _scene_summary(scene: str) -> dict[str, Any]:
    scene_dir = _scene_path(scene)
    generation_dirs = sorted(
        [p for p in scene_dir.iterdir() if p.is_dir() and p.name.startswith("generation")],
        key=_generation_sort_key,
    )
    protocols = _protocol_file_rows(scene)
    generations = []
    for generation_dir in generation_dirs:
        files = [p for p in generation_dir.glob("*.py") if p.stem != "__init__"]
        generations.append(
            {
                "name": generation_dir.name,
                "path": _relative_to_project(generation_dir),
                "protocol_count": len(files),
            }
        )
    return {
        "name": scene,
        "path": _relative_to_project(scene_dir),
        "task_path": _relative_to_project(scene_dir / "Task.py") if (scene_dir / "Task.py").exists() else "",
        "config_path": _relative_to_project(scene_dir / "config.py") if (scene_dir / "config.py").exists() else "",
        "generation_count": len(generation_dirs),
        "protocol_count": len(protocols),
        "prompt_count": len(list((scene_dir / "prompts").glob("*.txt"))) if (scene_dir / "prompts").exists() else 0,
        "generations": generations,
        "protocol_files": protocols,
        "prompts": _prompt_rows(scene, limit=20),
    }


def selfopt_state() -> dict[str, Any]:
    catalog = build_catalog()
    db = database_stats()
    return {
        "project_root": str(PROJECT_ROOT),
        "default_scene": catalog["defaults"]["scene"],
        "scenes": [_scene_summary(scene) for scene in catalog["scenes"]],
        "protocols": catalog["protocols"],
        "base_protocols": _base_protocol_rows(),
        "task_profiles": TASK_GUIDANCE,
        "project_vision": {
            "name": "面向多维统计场景的 RFID 协议自优化 Web 平台",
            "source": "2025 年大学生创新创业训练计划项目申报书",
            "summary": "平台目标是把 RFID 协议设计从人工推导升级为智能生成，支持自然语言需求、GRDL 建模、扩展父代协议基因库、LLM 进化生成、在线仿真沙箱与三重正确性验证。",
            "statistics": ["mode_query", "mean_estimation", "variance_estimation", "median_quantile", "cardinality_estimation", "missing_stocktaking"],
            "validation": ["功能逻辑验证", "物理约束验证", "信息隔离验证"],
            "domains": ["智能制造", "供应链管理", "智慧医疗", "工业传感监测", "库存盘点", "质量分布评估"],
        },
        "experiment_artifacts": _experiment_artifact_rows(),
        "experiment_options": {
            "robustness_factors": [
                ["density", "密度/取值范围"],
                ["dominance", "众数支配度"],
                ["scale", "标签规模"],
            ],
            "evolution_scenarios": _evolution_scenario_names(),
            "evolution_charts": [
                ["correction", "LLM 纠正次数"],
                ["performance", "性能-准确率"],
                ["time", "耗时收敛"],
                ["accuracy", "准确率收敛"],
                ["all", "全部图表"],
            ],
        },
        "output_artifacts": _output_artifact_rows(),
        "ai_runtime": {
            "base_url_configured": bool(os.getenv("AI_API_BASE_URL") or os.getenv("OPENAI_BASE_URL") or os.getenv("GEEK_API_BASE_URL")),
            "default_base_url": os.getenv("AI_API_BASE_URL") or os.getenv("OPENAI_BASE_URL") or os.getenv("GEEK_API_BASE_URL") or "https://geekspace.cloud",
            "model_configured": bool(os.getenv("AI_API_MODEL") or os.getenv("OPENAI_MODEL") or os.getenv("GEEK_API_MODEL")),
            "default_model": os.getenv("AI_API_MODEL") or os.getenv("OPENAI_MODEL") or os.getenv("GEEK_API_MODEL") or "gpt-4o-mini",
            "default_endpoint_path": os.getenv("AI_API_ENDPOINT_PATH") or "/v1/chat/completions",
            "api_key_configured": bool(os.getenv("AI_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("GEEK_API_KEY")),
            "default_api_key_env": "GEEK_API_KEY",
        },
        "ai_jobs": db.get("ai_jobs", [])[:20],
        "database_counts": db.get("counts", {}),
    }


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    copied = dict(job)
    copied["elapsed_s"] = round(time.perf_counter() - float(job.get("started_perf", time.perf_counter())), 3)
    copied.pop("started_perf", None)
    return copied


def _create_background_job(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    job_id = uuid4().hex[:12]
    job = {
        "id": job_id,
        "kind": kind,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "started_perf": time.perf_counter(),
        "payload": {
            key: value
            for key, value in payload.items()
            if key not in {"api_key", "api_key_value"}
        },
        "logs": ["任务已进入后台队列，浏览器会持续轮询进度。"],
        "result": None,
        "error": "",
    }
    with BACKGROUND_JOBS_LOCK:
        BACKGROUND_JOBS[job_id] = job
        if len(BACKGROUND_JOBS) > 80:
            for old_id in sorted(BACKGROUND_JOBS, key=lambda item: BACKGROUND_JOBS[item]["created_at"])[:20]:
                BACKGROUND_JOBS.pop(old_id, None)
    return _public_job(job)


def _update_background_job(
    job_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    progress: int | None = None,
    log: str | None = None,
    result: Any = None,
    error: str | None = None,
) -> None:
    with BACKGROUND_JOBS_LOCK:
        job = BACKGROUND_JOBS.get(job_id)
        if not job:
            return
        if status is not None:
            job["status"] = status
        if stage is not None:
            job["stage"] = stage
        if progress is not None:
            job["progress"] = max(0, min(100, int(progress)))
        if log:
            job["logs"].append(f"[{time.strftime('%H:%M:%S')}] {log}")
            job["logs"] = job["logs"][-300:]
        if result is not None:
            job["result"] = result
        if error is not None:
            job["error"] = error
        job["updated_at"] = _now_iso()


def get_background_job(job_id: str) -> dict[str, Any]:
    with BACKGROUND_JOBS_LOCK:
        job = BACKGROUND_JOBS.get(job_id)
        if not job:
            raise KeyError(f"未知后台任务: {job_id}")
        return _public_job(job)


def start_background_task(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    job = _create_background_job(kind, payload)
    job_id = job["id"]

    def runner() -> None:
        try:
            _update_background_job(job_id, status="running", stage="initializing", progress=3, log="后端任务已启动，正在读取配置与父代协议。")
            if kind == "ai_generation":
                result = run_ai_generation(
                    payload,
                    progress_logger=lambda message, progress=None, stage=None: _update_background_job(
                        job_id,
                        stage=stage or "ai_generation",
                        progress=progress,
                        log=str(message),
                    ),
                )
            elif kind == "auto_iterate":
                result = run_selfopt_auto_iterate(
                    payload,
                    progress_logger=lambda message, progress=None, stage=None: _update_background_job(
                        job_id,
                        stage=stage or "auto_iterate",
                        progress=progress,
                        log=str(message),
                    ),
                )
            else:
                raise ValueError(f"未知后台任务类型: {kind}")
            _update_background_job(job_id, status="success", stage="done", progress=100, log="任务完成，结果已写入平台状态。", result=result)
        except Exception as exc:
            _update_background_job(job_id, status="failed", stage="failed", progress=100, log=f"任务失败: {exc}", error=traceback.format_exc())

    thread = threading.Thread(target=runner, name=f"selfopt-{kind}-{job_id}", daemon=True)
    thread.start()
    return get_background_job(job_id)


def read_project_file(relative_path: str) -> dict[str, Any]:
    path = _resolve_project_path(relative_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"文件不存在: {relative_path}")
    _assert_text_file(path)
    return {
        "path": _relative_to_project(path),
        "name": path.name,
        "content": path.read_text(encoding="utf-8", errors="replace"),
        "size": path.stat().st_size,
        "mtime": int(path.stat().st_mtime),
    }


def save_project_file(payload: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_project_path(str(payload.get("path") or ""))
    _assert_editable_project_file(path)
    content = str(payload.get("content") or "")
    if len(content.encode("utf-8")) > MAX_WEB_FILE_BYTES:
        raise ValueError("保存内容过大")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"saved": True, "path": _relative_to_project(path), "size": path.stat().st_size}


TASK_TEMPLATE = '''#!/usr/bin/env python
import random
from collections import Counter
from typing import Dict, List, Tuple

from base import Tag


SIMULATION_CONFIG = {
    "NUM_TAGS": 10000,
    "PRICE_MIN": 1,
    "PRICE_MAX": 1000,
    "TOTAL_RUNS": 20,
}


def generate_tags(num_tags: int, price_min: int, price_max: int) -> Tuple[List[Tag], Dict[str, object]]:
    tags: List[Tag] = []
    hot_count = random.randint(2, 4)
    hot_prices = sorted(random.sample(range(price_min, price_max + 1), min(hot_count, price_max - price_min + 1)))
    for idx in range(num_tags):
        if random.random() < 0.45:
            price = random.choice(hot_prices)
        else:
            price = random.randint(price_min, price_max)
        tags.append(Tag(idx, price))
    counts = Counter(tag.price for tag in tags)
    max_count = max(counts.values()) if counts else 0
    modes = sorted(price for price, count in counts.items() if count == max_count)
    return tags, {"modes": modes, "hot_prices": hot_prices}
'''


CONFIG_TEMPLATE = '''SCENE_PROFILE = {
    "name": "RFID protocol self-optimization scene",
    "objective": "mode query accuracy, execution time and frame constraint balance",
    "edge_budget_ms": 300,
    "cloud_feedback": True,
}
'''


def create_selfopt_scene(payload: dict[str, Any]) -> dict[str, Any]:
    scene = _validate_scene(str(payload.get("scene") or payload.get("name") or ""), allow_new=True)
    scene_dir = _scene_path(scene, allow_new=True)
    if scene_dir.exists():
        raise ValueError(f"场景已存在: {scene}")
    generation_dir = scene_dir / "generation_0"
    prompt_dir = scene_dir / "prompts"
    generation_dir.mkdir(parents=True)
    prompt_dir.mkdir()
    base_dir = PROJECT_ROOT / "base_protocol"
    copied = []
    if base_dir.exists():
        for file_path in sorted(base_dir.glob("*.py")):
            if file_path.stem == "__init__":
                continue
            target = generation_dir / file_path.name
            shutil.copy2(file_path, target)
            copied.append(_relative_to_project(target))
    (scene_dir / "Task.py").write_text(TASK_TEMPLATE, encoding="utf-8")
    (scene_dir / "config.py").write_text(CONFIG_TEMPLATE, encoding="utf-8")
    return {"created": True, "scene": scene, "path": _relative_to_project(scene_dir), "copied_protocols": copied}


def generate_crossover_prompt_files(payload: dict[str, Any]) -> dict[str, Any]:
    scene = _validate_scene(str(payload.get("scene") or build_catalog()["defaults"]["scene"]))
    parent_names = _protocol_names(payload.get("parents") or payload.get("parent_names"))
    if len(parent_names) < 2:
        raise ValueError("至少选择两个父代协议")
    available = set(discover_protocols(scene))
    missing = [name for name in parent_names if name not in available]
    if missing:
        raise ValueError(f"场景 {scene} 中找不到协议: {', '.join(missing)}")
    scene_dir = _scene_path(scene)
    count, errors = create_crossover_prompts(parent_names, str(scene_dir))
    return {"scene": scene, "parents": parent_names, "generated_count": count, "errors": errors, "prompts": _prompt_rows(scene)}


def _find_protocol_file(scene: str, protocol_name: str, *, include_base: bool = True) -> Path:
    protocol = _validate_identifier(protocol_name, "协议名称")
    scene_dir = _scene_path(scene)
    generation_dirs = sorted(
        [p for p in scene_dir.iterdir() if p.is_dir() and p.name.startswith("generation")],
        key=_generation_sort_key,
        reverse=True,
    )
    for generation_dir in generation_dirs:
        file_path = generation_dir / f"{protocol}.py"
        if file_path.exists():
            return file_path
    if include_base:
        file_path = PROJECT_ROOT / "base_protocol" / f"{protocol}.py"
        if file_path.exists():
            return file_path
    raise FileNotFoundError(f"找不到协议文件: {scene}/{protocol}.py")


def _scan_information_isolation(protocol_file: Path) -> dict[str, Any]:
    """Heuristic static scan for direct reader access to tag private attributes."""
    try:
        text = protocol_file.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return {"passed": False, "warnings": [f"无法读取协议源码: {exc}"], "warning_count": 1}

    patterns = [
        ("tag.price", "直接访问 tag.price"),
        ("getattr(tag, \"price\")", "通过 getattr(tag, \"price\") 访问私有属性"),
        ("getattr(tag, 'price')", "通过 getattr(tag, 'price') 访问私有属性"),
    ]
    warnings: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for needle, message in patterns:
            if needle in stripped:
                warnings.append({"line": line_no, "rule": message, "code": stripped[:160]})

    return {
        "passed": not warnings,
        "warnings": warnings[:20],
        "warning_count": len(warnings),
        "note": "启发式静态扫描：仿真中允许标签侧响应读取自身属性，但读写器决策逻辑不应直接依赖单个标签私有值。",
    }


def _load_scene_task(scene: str):
    scene_name = _validate_scene(scene)
    task_path = _scene_path(scene_name) / "Task.py"
    spec = importlib.util.spec_from_file_location(f"web_selfopt.task.{scene_name}.{time.time_ns()}", task_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 Task.py: {task_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    generate_tags = getattr(module, "generate_tags", None)
    if generate_tags is None:
        raise AttributeError(f"{task_path} 缺少 generate_tags 函数")
    return generate_tags, getattr(module, "SIMULATION_CONFIG", {})


def _load_protocol_instance(protocol_file: Path, protocol_name: str):
    spec = importlib.util.spec_from_file_location(
        f"web_selfopt.protocol.{protocol_name}.{time.time_ns()}",
        protocol_file,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载协议文件: {protocol_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    protocol_class = getattr(module, protocol_name)
    return protocol_class()


def _target_values_for_profile(tags: list[Any], true_info: dict[str, Any], task_profile: str, payload: dict[str, Any]) -> list[float]:
    prices = [float(getattr(tag, "price", 0.0)) for tag in tags]
    if not prices:
        return [0.0]
    if task_profile == "mode_query":
        return [float(x) for x in true_info.get("modes", [])]
    if task_profile == "mean_estimation":
        return [sum(prices) / len(prices)]
    if task_profile == "variance_estimation":
        mean = sum(prices) / len(prices)
        return [sum((x - mean) ** 2 for x in prices) / len(prices)]
    if task_profile == "median_quantile":
        q = float(payload.get("quantile", 0.5))
        q = max(0.0, min(1.0, q))
        ordered = sorted(prices)
        index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))
        return [ordered[index]]
    if task_profile == "cardinality_estimation":
        return [float(len(tags))]
    if task_profile == "missing_stocktaking":
        return [float(len(tags))]
    return [float(x) for x in true_info.get("modes", [])]


def _estimated_values(raw: Any) -> list[float]:
    if isinstance(raw, (int, float)):
        return [float(raw)]
    if isinstance(raw, (list, tuple, set)):
        values = []
        for item in raw:
            if isinstance(item, (int, float)):
                values.append(float(item))
        return values
    return []


def _profile_hit(estimated: list[float], target: list[float], task_profile: str, payload: dict[str, Any]) -> bool:
    if not estimated or not target:
        return False
    if task_profile == "mode_query":
        return set(int(x) for x in estimated).issubset(set(int(x) for x in target))
    tolerance = float(payload.get("relative_tolerance", 0.15))
    absolute_tolerance = float(payload.get("absolute_tolerance", 2.0))
    error = abs(estimated[0] - target[0])
    limit = max(absolute_tolerance, abs(target[0]) * tolerance)
    return error <= limit


def validate_selfopt_protocol(payload: dict[str, Any]) -> dict[str, Any]:
    scene = _validate_scene(str(payload.get("scene") or build_catalog()["defaults"]["scene"]))
    protocol = _validate_identifier(str(payload.get("protocol") or ""), "协议名称")
    task_profile = str(payload.get("task_profile") or "mode_query")
    if task_profile not in TASK_GUIDANCE:
        raise ValueError(f"未知任务画像: {task_profile}")
    protocol_file = _find_protocol_file(scene, protocol, include_base=True)
    information_isolation = _scan_information_isolation(protocol_file)
    generate_tags, task_config = _load_scene_task(scene)
    price_min = _coerce_int(payload, "price_min", int(task_config.get("PRICE_MIN", 1)), 1, 100000)
    price_max = _coerce_int(payload, "price_max", int(task_config.get("PRICE_MAX", 1000)), price_min + 1, 1000000)
    num_tags = _coerce_int(payload, "num_tags", 120, 20, 200000)
    total_runs = _coerce_int(payload, "total_runs", 3, 1, 200)

    protocol_instance = _load_protocol_instance(protocol_file, protocol)
    validation_tags, _ = generate_tags(num_tags, price_min, price_max)
    validator = ProtocolValidator(protocol_name=protocol, protocol_instance=protocol_instance)
    is_valid, report = validator.validate(validation_tags, price_min, price_max)

    passed_runs = 0
    total_time = 0.0
    samples: list[dict[str, Any]] = []
    runtime_error = ""
    for run_index in range(total_runs):
        tags, true_info = generate_tags(num_tags, price_min, price_max)
        try:
            raw_values, metric, exec_time_ms, verification_data = protocol_instance.execute(tags, price_min, price_max)
        except Exception as exc:
            runtime_error = str(exc)
            break
        target = _target_values_for_profile(tags, true_info if isinstance(true_info, dict) else {}, task_profile, payload)
        estimated = _estimated_values(raw_values)
        hit = _profile_hit(estimated, target, task_profile, payload)
        passed_runs += int(hit)
        total_time += float(exec_time_ms or 0.0)
        if len(samples) < 5:
            samples.append(
                {
                    "run": run_index + 1,
                    "hit": hit,
                    "estimated_values": estimated,
                    "target_values": target,
                    "metric": metric,
                    "exec_time_ms": exec_time_ms,
                    "verification_data": verification_data,
                }
            )

    completed_runs = len(samples) if runtime_error else total_runs
    avg_time = round(total_time / max(1, completed_runs), 4)
    pass_rate = round(passed_runs / max(1, completed_runs) * 100, 2)
    return {
        "scene": scene,
        "protocol": protocol,
        "task_profile": task_profile,
        "task_title": TASK_GUIDANCE[task_profile]["title"],
        "protocol_path": _relative_to_project(protocol_file),
        "validator_passed": bool(is_valid),
        "validator_report": report,
        "information_isolation_passed": information_isolation["passed"],
        "information_isolation": information_isolation,
        "runtime_error": runtime_error,
        "num_tags": num_tags,
        "total_runs": total_runs,
        "completed_runs": completed_runs,
        "pass_rate": pass_rate,
        "avg_time_ms": avg_time,
        "samples": samples,
    }


def copy_baseline_to_scene(payload: dict[str, Any]) -> dict[str, Any]:
    scene = _validate_scene(str(payload.get("scene") or build_catalog()["defaults"]["scene"]))
    protocol = _validate_identifier(str(payload.get("protocol") or ""), "基线协议名称")
    source = PROJECT_ROOT / "base_protocol" / f"{protocol}.py"
    if not source.exists():
        raise FileNotFoundError(f"找不到基线协议: {protocol}")
    target_generation = str(payload.get("target_generation") or "generation_0").strip() or "generation_0"
    if not target_generation.startswith("generation") or not re.match(r"^generation_?\d+$", target_generation):
        raise ValueError("目标世代必须形如 generation_0 或 generation1")
    target_dir = _scene_path(scene) / target_generation
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    shutil.copy2(source, target)
    return {"copied": True, "scene": scene, "protocol": protocol, "source": _relative_to_project(source), "target": _relative_to_project(target)}


def run_ai_generation(payload: dict[str, Any], progress_logger=None) -> dict[str, Any]:
    catalog = build_catalog()
    scene = _validate_scene(str(payload.get("scene") or catalog["defaults"]["scene"]))
    parent_a = _validate_identifier(str(payload.get("parent_a") or ""), "父代 A")
    parent_b = _validate_identifier(str(payload.get("parent_b") or ""), "父代 B")
    task_profile = str(payload.get("task_profile") or "mode_query")
    if task_profile not in TASK_GUIDANCE:
        raise ValueError(f"未知任务画像: {task_profile}")
    protocol_name = _default_protocol_name_for_task(payload.get("protocol_name") or "web_ai_candidate", task_profile)
    target_generation = str(payload.get("target_generation") or "next")
    run_mode = str(payload.get("run_mode") or "generate").strip()
    dry_run = run_mode == "preview" or bool(payload.get("dry_run", False))
    requirement_text = str(payload.get("requirement_text") or "")
    retry_attempts = _coerce_int(payload, "retry_attempts", 3, 1, 6)

    available = set(catalog["protocols"].get(scene, []))
    for parent in (parent_a, parent_b):
        if parent not in available:
            raise ValueError(f"场景 {scene} 中找不到父代协议: {parent}")

    logs: list[str] = []

    def emit(message: str, progress: int | None = None, stage: str | None = None) -> None:
        logs.append(message)
        if progress_logger:
            progress_logger(message, progress=progress, stage=stage)

    emit("已校验场景、父代协议和任务画像。", progress=8, stage="validated")
    if dry_run:
        emit("当前为预览生成任务：只生成 Prompt 与目标路径，不调用大模型。", progress=18, stage="preview")
    else:
        emit("当前为真实生成：即将连接大模型网关。", progress=18, stage="connecting_model")
    def workflow_progress(message: str) -> tuple[int | None, str]:
        text = str(message)
        if "已生成 AI 优化提示词" in text or "目标输出路径" in text:
            return 28, "prompt_written"
        if "模型配置已解析" in text or "正在请求大模型" in text or "AI 自优化第" in text:
            return 42, "connecting_model"
        if "已收到模型响应" in text:
            return 62, "running_model"
        if "已写入候选协议" in text:
            return 72, "candidate_written"
        if "正在执行协议" in text or "任务烟测" in text or "信息隔离" in text:
            return 84, "validation"
        if "通过验证" in text:
            return 92, "validation"
        if "自修复" in text:
            return 88, "repairing"
        return None, "running_model"

    result = run_ai_optimization_workflow(
        scene_name=scene,
        parent_a=parent_a,
        parent_b=parent_b,
        protocol_name=protocol_name,
        target_generation=target_generation,
        base_url=payload.get("base_url"),
        model=payload.get("model"),
        endpoint_path=payload.get("endpoint_path"),
        api_key_env=str(payload.get("api_key_env") or "GEEK_API_KEY"),
        api_key_value=payload.get("api_key_value") or payload.get("api_key") or None,
        temperature=float(payload.get("temperature", 0.2)),
        max_tokens=_coerce_int(payload, "max_tokens", 2800, 256, 32000),
        timeout_seconds=_coerce_int(payload, "timeout_seconds", 120, 20, 3600),
        validate_num_tags=_coerce_int(payload, "validate_num_tags", 120, 20, 5000),
        max_repair_rounds=_coerce_int(payload, "max_repair_rounds", 0 if dry_run else 1, 0, 8),
        dry_run=dry_run,
        retry_attempts=retry_attempts,
        task_profile=task_profile,
        requirement_text=requirement_text,
        logger=lambda message: emit(
            str(message),
            progress=workflow_progress(str(message))[0],
            stage=workflow_progress(str(message))[1],
        ),
    )
    emit(f"AI 任务返回状态: {result.get('status')}", progress=96, stage="persisting")
    result["task_profile"] = task_profile
    result["task_title"] = TASK_GUIDANCE[task_profile]["title"]
    request = {
        "scene": scene,
        "parent_a": parent_a,
        "parent_b": parent_b,
        "protocol_name": protocol_name,
        "target_generation": target_generation,
        "dry_run": dry_run,
        "task_profile": task_profile,
        "run_mode": "preview" if dry_run else "generate",
    }
    job_id = store_ai_job(request=request, result=result, logs=logs)
    return {"job_id": job_id, "request": request, "result": result, "logs": logs, "state": selfopt_state()}


def probe_ai_runtime(payload: dict[str, Any]) -> dict[str, Any]:
    api_key_value = payload.get("api_key_value") or payload.get("api_key") or None
    config = LLMConfig.from_sources(
        base_url=payload.get("base_url"),
        model=payload.get("model") or os.getenv("AI_API_MODEL") or os.getenv("OPENAI_MODEL") or os.getenv("GEEK_API_MODEL") or "gpt-4o-mini",
        endpoint_path=payload.get("endpoint_path"),
        api_key=api_key_value,
        api_key_env=str(payload.get("api_key_env") or "GEEK_API_KEY"),
        temperature=0.0,
        max_tokens=_coerce_int(payload, "max_tokens", 64, 16, 512),
        timeout_seconds=_coerce_int(payload, "timeout_seconds", 60, 10, 300),
    )
    client = OpenAICompatibleClient(config)
    mode = str(payload.get("mode") or "probe")
    if mode == "list":
        return {"mode": "list", "models": client.list_models(), "base_url": config.base_url}
    text = client.generate_text(system_prompt="You are a health check endpoint.", user_prompt="Reply with exactly OK.")
    return {"mode": "probe", "reply": text.strip(), "model": config.model, "base_url": config.base_url}


def _parse_batch_results(stdout: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\|\s*([0-9.]+)%\s*\|\s*([0-9.]+)\s*\|\s*(\d+)", line)
        if match:
            rows.append(
                {
                    "name": match.group(1),
                    "pass_rate": float(match.group(2)),
                    "avg_time": float(match.group(3)),
                    "rank": int(match.group(4)),
                }
            )
    return rows


def run_selfopt_batch_test(payload: dict[str, Any]) -> dict[str, Any]:
    scene = _validate_scene(str(payload.get("scene") or build_catalog()["defaults"]["scene"]))
    protocols = _protocol_patterns(payload.get("protocols") or payload.get("patterns"))
    if not protocols:
        pattern = str(payload.get("pattern") or "all").strip()
        protocols = ["all"] if pattern == "all" else _protocol_patterns(pattern)
    num_tags = _coerce_int(payload, "num_tags", 1000, 20, 200000)
    total_runs = _coerce_int(payload, "total_runs", 5, 1, 500)
    price_min = _coerce_int(payload, "price_min", 1, 1, 100000)
    price_max = _coerce_int(payload, "price_max", 1000, price_min + 1, 1000000)
    timeout = _coerce_int(payload, "timeout_seconds", 180, 20, 1800)
    command = [
        sys.executable,
        "Test.py",
        "--scene",
        scene,
        *protocols,
        "--num-tags",
        str(num_tags),
        "--total-runs",
        str(total_runs),
        "--price-min",
        str(price_min),
        "--price-max",
        str(price_max),
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=timeout,
    )
    wall_clock_s = round(time.perf_counter() - started, 3)
    return {
        "scene": scene,
        "protocols": protocols,
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "wall_clock_s": wall_clock_s,
        "results": _parse_batch_results(completed.stdout),
    }


def _default_parent_names(scene: str) -> list[str]:
    preferred = ["g2_1", "g2_2", "g8_3", "g8_4", "g7_1", "ModeQueryTCS", "ModeQueryTKQ"]
    available = discover_protocols(scene)
    chosen = [name for name in preferred if name in available]
    for name in available:
        if name not in chosen:
            chosen.append(name)
        if len(chosen) >= 4:
            break
    return chosen[:4]


def _default_protocol_name_for_task(value: Any, task_profile: str) -> str:
    text = str(value or "").strip()
    if not text or text in {"web_ai_candidate", "g_course_ai_demo"}:
        text = f"web_{task_profile}_candidate"
    return _validate_identifier(text, "输出协议名")


def _default_iteration_prefix_for_task(value: Any, task_profile: str) -> str:
    text = str(value or "").strip()
    if not text or text == "web_auto_iter":
        text = f"web_{task_profile}_iter"
    return _validate_identifier(text, "协议前缀")


def run_selfopt_auto_iterate(payload: dict[str, Any], progress_logger=None) -> dict[str, Any]:
    scene = _validate_scene(str(payload.get("scene") or build_catalog()["defaults"]["scene"]))
    parents = _protocol_names(payload.get("parents") or payload.get("parent_names"))
    if len(parents) < 2:
        parents = _default_parent_names(scene)
    if len(parents) < 2:
        raise ValueError("自动迭代至少需要两个父代协议")
    available = set(discover_protocols(scene))
    missing = [name for name in parents if name not in available]
    if missing:
        raise ValueError(f"场景 {scene} 中找不到协议: {', '.join(missing)}")

    task_profile = str(payload.get("task_profile") or "mode_query")
    if task_profile not in TASK_GUIDANCE:
        raise ValueError(f"未知任务画像: {task_profile}")
    prefix = _default_iteration_prefix_for_task(payload.get("prefix") or "web_auto_iter", task_profile)
    iterations = _coerce_int(payload, "iterations", 3, 1, 12)
    dry_run = bool(payload.get("dry_run", True))
    max_repair_rounds = _coerce_int(payload, "max_repair_rounds", 0 if dry_run else 1, 0, 5)
    validate_num_tags = _coerce_int(payload, "validate_num_tags", 80, 20, 5000)
    retry_attempts = _coerce_int(payload, "retry_attempts", 3, 1, 6)
    target_generation = str(payload.get("target_generation") or "next")
    requirement_text = str(payload.get("requirement_text") or "")

    pairs = []
    for i, parent_a in enumerate(parents):
        for parent_b in parents[i + 1 :]:
            pairs.append((parent_a, parent_b))
    results: list[dict[str, Any]] = []
    started = time.perf_counter()
    if progress_logger:
        progress_logger(
            f"自动迭代已准备 {len(pairs)} 个父代组合，将执行 {iterations} 轮；模式：{'预览' if dry_run else '真实生成'}。",
            progress=5,
            stage="planning",
        )
    for index in range(iterations):
        parent_a, parent_b = pairs[index % len(pairs)]
        protocol_name = f"{prefix}_{index + 1:02d}"
        logs: list[str] = []
        base_progress = 8 + int(index / max(1, iterations) * 82)
        if progress_logger:
            progress_logger(
                f"第 {index + 1}/{iterations} 轮：父代 {parent_a} + {parent_b} -> 子代 {protocol_name}",
                progress=base_progress,
                stage=f"iteration_{index + 1}",
            )

        def iter_log(message: str) -> None:
            logs.append(message)
            text = str(message)
            step_progress = base_progress + 3
            stage = f"iteration_{index + 1}"
            if "模型配置已解析" in text or "正在请求大模型" in text or "AI 自优化第" in text:
                step_progress = base_progress + 9
                stage = "running_model"
            elif "已收到模型响应" in text:
                step_progress = base_progress + 14
                stage = "running_model"
            elif "已写入候选协议" in text:
                step_progress = base_progress + 18
                stage = "persisting"
            elif "正在执行协议" in text or "任务烟测" in text or "信息隔离" in text:
                step_progress = base_progress + 22
                stage = "validation"
            elif "通过验证" in text or "返回最后候选" in text:
                step_progress = base_progress + 25
                stage = "validation"
            if progress_logger:
                progress_logger(
                    f"第 {index + 1} 轮 | {message}",
                    progress=step_progress,
                    stage=stage,
                )

        result = run_ai_optimization_workflow(
            scene_name=scene,
            parent_a=parent_a,
            parent_b=parent_b,
            protocol_name=protocol_name,
            target_generation=target_generation,
            base_url=payload.get("base_url"),
            model=payload.get("model"),
            endpoint_path=payload.get("endpoint_path"),
            api_key_env=str(payload.get("api_key_env") or "GEEK_API_KEY"),
            api_key_value=payload.get("api_key_value") or payload.get("api_key"),
            temperature=float(payload.get("temperature", 0.2)),
            max_tokens=_coerce_int(payload, "max_tokens", 2800, 256, 16000),
            timeout_seconds=_coerce_int(payload, "timeout_seconds", 120, 20, 1800),
            validate_num_tags=validate_num_tags,
            max_repair_rounds=max_repair_rounds,
            dry_run=dry_run,
            retry_attempts=retry_attempts,
            task_profile=task_profile,
            requirement_text=requirement_text,
            logger=iter_log,
        )
        result["task_profile"] = task_profile
        result["task_title"] = TASK_GUIDANCE[task_profile]["title"]
        request = {
            "scene": scene,
            "parent_a": parent_a,
            "parent_b": parent_b,
            "protocol_name": protocol_name,
            "target_generation": target_generation,
            "dry_run": dry_run,
            "task_profile": task_profile,
        }
        job_id = store_ai_job(request=request, result=result, logs=logs)
        item = {
            "iteration": index + 1,
            "job_id": job_id,
            "parent_a": parent_a,
            "parent_b": parent_b,
            "protocol_name": protocol_name,
            "status": result.get("status"),
            "prompt_file": result.get("prompt_file"),
            "target_file": result.get("target_file"),
            "protocol_exists": result.get("protocol_exists", bool(result.get("target_file") and Path(str(result.get("target_file"))).exists())),
            "logs": logs,
            "validation": result.get("validation"),
        }
        results.append(item)
        if progress_logger:
            progress_logger(
                f"第 {index + 1}/{iterations} 轮完成：{protocol_name} -> {result.get('status')}",
                progress=8 + int((index + 1) / max(1, iterations) * 88),
                stage=f"iteration_{index + 1}_done",
            )
        if not dry_run and result.get("status") == "success":
            available.add(protocol_name)
    return {
        "scene": scene,
        "parents": parents,
        "prefix": prefix,
        "dry_run": dry_run,
        "task_profile": task_profile,
        "iterations": results,
        "wall_clock_s": round(time.perf_counter() - started, 3),
        "state": selfopt_state(),
    }


def _safe_experiment_path(path_text: str, *, default_name: str) -> str:
    raw_text = str(path_text or default_name).replace("\\", "/").strip()
    raw = Path(raw_text)
    if raw.is_absolute():
        raise ValueError("实验路径只允许使用项目内相对路径")
    candidate = (PROJECT_ROOT / raw).resolve()
    exp_root = (PROJECT_ROOT / "Experiments").resolve()
    if candidate != exp_root and exp_root not in candidate.parents:
        raise ValueError("实验路径必须位于 Experiments 目录内")
    return _relative_to_project(candidate)


def _safe_experiment_output_dir(path_text: str | None = None) -> Path:
    rel_path = _safe_experiment_path(str(path_text or "Experiments"), default_name="Experiments")
    output_dir = (PROJECT_ROOT / rel_path).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _normalise_score(value: float, best: float, worst: float, *, higher_is_better: bool) -> float:
    if worst == best:
        return 1.0
    raw = (value - worst) / (best - worst) if higher_is_better else (worst - value) / (worst - best)
    return max(0.0, min(1.0, raw))


def _protocol_metrics_for_scene(
    scene: str,
    protocol: str,
    *,
    task_profile: str,
    num_tags: int,
    total_runs: int = 2,
) -> dict[str, Any]:
    result = validate_selfopt_protocol(
        {
            "scene": scene,
            "protocol": protocol,
            "task_profile": task_profile,
            "num_tags": num_tags,
            "total_runs": total_runs,
            "relative_tolerance": 0.2,
        }
    )
    return {
        "accuracy": max(0.0, min(1.0, float(result.get("pass_rate", 0.0)) / 100.0)),
        "time": float(result.get("avg_time_ms", 0.0) or 0.0),
        "validator_passed": bool(result.get("validator_passed")),
        "information_isolation_passed": bool(result.get("information_isolation_passed")),
    }


def _scene_evolution_data_path(scene: str, payload: dict[str, Any]) -> tuple[Path, str]:
    scene_name = _validate_scene(scene)
    task_profile = str(payload.get("task_profile") or "mode_query")
    if task_profile not in TASK_GUIDANCE:
        raise ValueError(f"未知任务画像: {task_profile}")
    output_dir = _safe_experiment_output_dir(payload.get("output_dir"))
    data_path = output_dir / f"evolution_scene_{scene_name}.json"
    num_tags = _coerce_int(payload, "num_tags", 60, 20, 5000)
    total_runs = _coerce_int(payload, "total_runs", 2, 1, 20)

    scene_dir = _scene_path(scene_name)
    generation_dirs = sorted(
        [p for p in scene_dir.iterdir() if p.is_dir() and p.name.startswith("generation")],
        key=_generation_sort_key,
    )
    if not generation_dirs:
        raise ValueError(f"场景 {scene_name} 没有 generation* 协议目录，无法生成进化图数据")

    previous_generation_protocols: list[str] = []
    generations: list[dict[str, Any]] = []
    all_times: list[float] = []
    raw_generation_rows: list[list[dict[str, Any]]] = []
    for gen_index, generation_dir in enumerate(generation_dirs, start=1):
        protocol_files = sorted([p for p in generation_dir.glob("*.py") if p.stem != "__init__"], key=lambda p: p.stem)
        rows: list[dict[str, Any]] = []
        for file_path in protocol_files:
            try:
                metrics = _protocol_metrics_for_scene(
                    scene_name,
                    file_path.stem,
                    task_profile=task_profile,
                    num_tags=num_tags,
                    total_runs=total_runs,
                )
            except Exception:
                continue
            all_times.append(metrics["time"])
            rows.append(
                {
                    "name": file_path.stem,
                    "generation": gen_index,
                    "parent_A": previous_generation_protocols[0] if previous_generation_protocols else None,
                    "parent_B": previous_generation_protocols[1] if len(previous_generation_protocols) > 1 else None,
                    "correction_count": 0 if file_path.stem.startswith("ModeQuery") else 1,
                    "accuracy": metrics["accuracy"],
                    "time": metrics["time"],
                    "validator_passed": metrics["validator_passed"],
                    "information_isolation_passed": metrics["information_isolation_passed"],
                }
            )
        raw_generation_rows.append(rows)
        previous_generation_protocols = [row["name"] for row in sorted(rows, key=lambda item: item["time"])[:2]]

    best_time = min(all_times) if all_times else 0.0
    worst_time = max(all_times) if all_times else 1.0
    for gen_index, rows in enumerate(raw_generation_rows, start=1):
        individuals = []
        for row in rows:
            time_score = _normalise_score(row["time"], best_time, worst_time, higher_is_better=False)
            row["fitness"] = round(0.65 * row["accuracy"] + 0.35 * time_score, 4)
            individuals.append(row)
        individuals.sort(key=lambda item: (item["fitness"], -item["time"]), reverse=True)
        generations.append({"generation": gen_index, "individuals": individuals})

    payload_data = {
        scene_name: {
            "source": f"{scene_name}/generation*",
            "task_profile": task_profile,
            "num_tags": num_tags,
            "total_runs": total_runs,
            "base_algorithms": generations[0]["individuals"] if generations else [],
            "generations": generations,
        }
    }
    data_path.write_text(json.dumps(payload_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data_path, scene_name


def _final_comparison_csv_path(scene: str, payload: dict[str, Any]) -> Path:
    scene_name = _validate_scene(scene)
    task_profile = str(payload.get("task_profile") or "mode_query")
    if task_profile not in TASK_GUIDANCE:
        raise ValueError(f"未知任务画像: {task_profile}")
    output_dir = _safe_experiment_output_dir(payload.get("output_dir"))
    csv_path = output_dir / f"final_comparison_{scene_name}.csv"
    num_tags = _coerce_int(payload, "num_tags", 60, 20, 5000)
    total_runs = _coerce_int(payload, "total_runs", 2, 1, 20)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    preferred = [
        str(payload.get("protocols") or "").split(":", 1)[0],
        "ModeQuerySEM",
        "ModeQueryTCS",
        "ModeQueryTKQ",
        "ModeQueryEE",
    ]
    for name in preferred + discover_protocols(scene_name):
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            metrics = _protocol_metrics_for_scene(
                scene_name,
                name,
                task_profile=task_profile,
                num_tags=num_tags,
                total_runs=total_runs,
            )
        except Exception:
            continue
        rows.append({"protocol_name": name, **metrics})
        if len(rows) >= 8:
            break
    if not rows:
        raise ValueError(f"场景 {scene_name} 没有可执行协议，无法生成终稿图")
    import csv

    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["protocol_name", "accuracy", "time", "validator_passed", "information_isolation_passed"],
        )
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


def _run_command(command: list[str], *, timeout: int = 600, env_overrides: dict[str, str] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", **(env_overrides or {})},
        timeout=timeout,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "wall_clock_s": round(time.perf_counter() - started, 3),
    }


def run_experiment_task(payload: dict[str, Any]) -> dict[str, Any]:
    task = str(payload.get("task") or "robust").strip()
    task_profile = str(payload.get("task_profile") or "mode_query").strip()
    if task_profile not in TASK_GUIDANCE:
        raise ValueError(f"未知任务画像: {task_profile}")
    timeout = _coerce_int(payload, "timeout_seconds", 600, 30, 7200)
    env = {"MPLBACKEND": "Agg"}
    if task == "robust":
        protocols = str(payload.get("protocols") or "g10_4:single_mode,g8_3:multi_mode").strip()
        factors = str(payload.get("factors") or "density").strip()
        output = _safe_experiment_path(str(payload.get("output") or "Experiments/sensitivity_results_web.csv"), default_name="Experiments/sensitivity_results_web.csv")
        command = [
            sys.executable,
            "-u",
            "Experiments/Test_robust.py",
            "--protocols",
            protocols,
            "--factors",
            factors,
            "--num-tags",
            str(_coerce_int(payload, "num_tags", 2000, 20, 200000)),
            "--price-min",
            str(_coerce_int(payload, "price_min", 1, 1, 100000)),
            "--price-max",
            str(_coerce_int(payload, "price_max", 1000, 2, 1000000)),
            "--total-runs",
            str(_coerce_int(payload, "total_runs", 2, 1, 200)),
            "--point-count",
            str(_coerce_int(payload, "point_count", 3, 2, 100)),
            "--density-max",
            str(_coerce_int(payload, "density_max", 10000, 10, 1000000)),
            "--output",
            output,
        ]
    elif task == "plot_robust":
        csv_path = _safe_experiment_path(str(payload.get("csv") or "Experiments/sensitivity_results_web.csv"), default_name="Experiments/sensitivity_results_web.csv")
        output_dir = _safe_experiment_path(str(payload.get("output_dir") or "Experiments"), default_name="Experiments")
        command = [
            sys.executable,
            "-u",
            "Experiments/plot_robust.py",
            "--csv",
            csv_path,
            "--factors",
            str(payload.get("factors") or "density"),
            "--output-dir",
            output_dir,
            "--no-show",
        ]
    elif task == "plot_evolution":
        scenario = str(payload.get("scenario") or "single_peak")
        if scenario in discover_scenes():
            data_file, scenario = _scene_evolution_data_path(scenario, payload)
            data_path = _relative_to_project(data_file)
        else:
            data_path = _safe_experiment_path(str(payload.get("data") or "Experiments/evolution_data_combined.json"), default_name="Experiments/evolution_data_combined.json")
        output_dir = _safe_experiment_path(str(payload.get("output_dir") or "Experiments"), default_name="Experiments")
        command = [
            sys.executable,
            "-u",
            "Experiments/plot_evolution_results.py",
            "--scenario",
            scenario,
            "--charts",
            str(payload.get("charts") or "correction"),
            "--data",
            data_path,
            "--output-dir",
            output_dir,
            "--no-show",
        ]
    elif task == "plot_final":
        source_scene = str(payload.get("source_scene") or "").strip()
        if not source_scene:
            protocol_spec = str(payload.get("protocols") or "")
            source_scene = protocol_spec.split(":", 1)[1] if ":" in protocol_spec else build_catalog()["defaults"]["scene"]
        source_scene = _validate_scene(source_scene)
        csv_path = _final_comparison_csv_path(source_scene, payload)
        output_dir = _safe_experiment_path(str(payload.get("output_dir") or "Experiments"), default_name="Experiments")
        command = [
            sys.executable,
            "-u",
            "Experiments/plot_scene_final.py",
            "--csv",
            _relative_to_project(csv_path),
            "--scene",
            source_scene,
            "--output-dir",
            output_dir,
            "--no-show",
        ]
    else:
        raise ValueError(f"未知实验任务: {task}")

    result = _run_command(command, timeout=timeout, env_overrides=env)
    return result | {
        "task": task,
        "task_profile": task_profile,
        "task_title": TASK_GUIDANCE[task_profile]["title"],
        "artifacts": _experiment_artifact_rows(),
    }


def build_catalog() -> dict[str, Any]:
    scenes = discover_scenes()
    protocols = {scene: discover_protocols(scene) for scene in scenes}
    default_scene = "multi_mode" if "multi_mode" in scenes else (scenes[0] if scenes else "")
    default_protocol = "g2_2"
    if default_scene and default_protocol not in protocols.get(default_scene, []):
        default_protocol = protocols.get(default_scene, [""])[0]
    return {
        "scenes": scenes,
        "protocols": protocols,
        "defaults": {
            "scene": default_scene,
            "protocol": default_protocol,
            "devices": 4,
            "gateways": 2,
            "batches": 3,
            "num_tags": 80,
            "price_min": 1,
            "price_max": 200,
            "stream_window": 4,
        },
        "scenario_presets": SCENARIO_PRESETS,
        "task_library": TASK_LIBRARY,
        "optimization_priorities": OPTIMIZATION_PRIORITIES,
    }


def _coerce_int(payload: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(payload.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def normalize_run_config(payload: dict[str, Any]) -> dict[str, Any]:
    catalog = build_catalog()
    scene = str(payload.get("scene") or catalog["defaults"]["scene"])
    if scene not in catalog["scenes"]:
        raise ValueError(f"未知场景: {scene}")

    protocol = str(payload.get("protocol") or catalog["defaults"]["protocol"])
    if protocol not in catalog["protocols"].get(scene, []):
        raise ValueError(f"场景 {scene} 中找不到协议: {protocol}")

    price_min = _coerce_int(payload, "price_min", 1, 1, 100000)
    price_max = _coerce_int(payload, "price_max", 200, 2, 100000)
    if price_max <= price_min:
        price_max = price_min + 1

    gateways = _coerce_int(payload, "gateways", 2, 1, 12)
    devices = _coerce_int(payload, "devices", 4, 1, 48)

    return {
        "scene": scene,
        "protocol": protocol,
        "devices": devices,
        "gateways": min(gateways, devices),
        "batches": _coerce_int(payload, "batches", 3, 1, 20),
        "num_tags": _coerce_int(payload, "num_tags", 80, 20, 3000),
        "price_min": price_min,
        "price_max": price_max,
        "stream_window": _coerce_int(payload, "stream_window", 4, 1, 50),
        "shared_secret": str(payload.get("shared_secret") or "rfid-course-demo-secret"),
        "include_security": bool(payload.get("include_security", True)),
        "include_benchmark": bool(payload.get("include_benchmark", False)),
        "dashboard": bool(payload.get("dashboard", True)),
    }


def run_course_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    config = normalize_run_config(payload)
    started = time.perf_counter()
    output_dir = make_run_dir()

    result = run_distributed_demo(
        scene=config["scene"],
        protocol_name=config["protocol"],
        device_count=config["devices"],
        gateway_count=config["gateways"],
        batch_count=config["batches"],
        num_tags=config["num_tags"],
        price_min=config["price_min"],
        price_max=config["price_max"],
        stream_window_size=config["stream_window"],
        shared_secret=config["shared_secret"],
    )

    security_result = run_security_check(config["shared_secret"]) if config["include_security"] else None
    benchmark_rows = None
    if config["include_benchmark"]:
        benchmark_rows = run_scalability_benchmark(
            scene=config["scene"],
            protocol_name=config["protocol"],
            num_tags=max(20, min(config["num_tags"], 60)),
            price_min=config["price_min"],
            price_max=min(config["price_max"], max(config["price_min"] + 1, 160)),
            stream_window_size=config["stream_window"],
            shared_secret=config["shared_secret"],
        )

    paths = export_course_result(result, output_dir, security_result, benchmark_rows)
    dashboard_path = None
    if config["dashboard"]:
        dashboard_path = generate_dashboard(
            result,
            output_dir / "dashboard.html",
            security_result=security_result,
            benchmark_rows=benchmark_rows,
        )
        paths["dashboard"] = dashboard_path

    wall_clock_s = round(time.perf_counter() - started, 3)
    run_id = store_course_run(
        config=config,
        result=result,
        security_result=security_result,
        benchmark_rows=benchmark_rows,
        output_dir=output_dir,
        dashboard_path=dashboard_path,
        wall_clock_s=wall_clock_s,
    )
    detail = get_run_detail(run_id)
    return {
        "run_id": run_id,
        "config": {key: value for key, value in config.items() if key != "shared_secret"},
        "wall_clock_s": wall_clock_s,
        "paths": {name: str(path) for name, path in paths.items()},
        "detail": detail,
    }


def run_ai_dry_run(payload: dict[str, Any]) -> dict[str, Any]:
    catalog = build_catalog()
    scene = str(payload.get("scene") or catalog["defaults"]["scene"])
    parent_a = str(payload.get("parent_a") or "g2_1")
    parent_b = str(payload.get("parent_b") or "g2_2")
    protocol_name = str(payload.get("protocol_name") or "g_course_ai_demo")
    target_generation = str(payload.get("target_generation") or "next")

    if scene not in catalog["scenes"]:
        raise ValueError(f"未知场景: {scene}")
    for parent in (parent_a, parent_b):
        if parent not in catalog["protocols"].get(scene, []):
            raise ValueError(f"场景 {scene} 中找不到父代协议: {parent}")
    if not IDENTIFIER_RE.match(protocol_name):
        raise ValueError("输出协议名必须是合法 Python 标识符")

    logs: list[str] = []
    result = run_ai_optimization_workflow(
        scene_name=scene,
        parent_a=parent_a,
        parent_b=parent_b,
        protocol_name=protocol_name,
        target_generation=target_generation,
        base_url=None,
        model=None,
        endpoint_path=None,
        api_key_env="GEEK_API_KEY",
        api_key_value=None,
        temperature=0.2,
        max_tokens=2800,
        timeout_seconds=120,
        validate_num_tags=80,
        max_repair_rounds=0,
        dry_run=True,
        logger=logs.append,
    )
    request = {
        "scene": scene,
        "parent_a": parent_a,
        "parent_b": parent_b,
        "protocol_name": protocol_name,
        "target_generation": target_generation,
        "dry_run": True,
    }
    job_id = store_ai_job(request=request, result=result, logs=logs)
    return {"job_id": job_id, "request": request, "result": result, "logs": logs}


def analyze_design_case(payload: dict[str, Any]) -> dict[str, Any]:
    catalog = build_catalog()
    scene = str(payload.get("scene") or catalog["defaults"]["scene"])
    if scene not in catalog["scenes"]:
        raise ValueError(f"未知场景: {scene}")
    available_protocols = catalog["protocols"].get(scene, [])
    case = build_design_case(payload | {"scene": scene}, available_protocols)
    case_id = store_design_case(case)
    return {"case_id": case_id, "case": case}


def preview_design_case(payload: dict[str, Any]) -> dict[str, Any]:
    catalog = build_catalog()
    scene = str(payload.get("scene") or catalog["defaults"]["scene"])
    if scene not in catalog["scenes"]:
        raise ValueError(f"未知场景: {scene}")
    available_protocols = catalog["protocols"].get(scene, [])
    case = build_design_case(payload | {"scene": scene}, available_protocols)
    return {"case": case, "persisted": False}


def json_response(handler: BaseHTTPRequestHandler, data: Any, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler: BaseHTTPRequestHandler, text: str, content_type: str = "text/html; charset=utf-8") -> None:
    body = text.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def file_response(handler: BaseHTTPRequestHandler, path: Path) -> None:
    _assert_safe_raw_artifact(path)
    body = path.read_bytes()
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    if path.suffix.lower() in {".html", ".txt", ".md", ".csv", ".json", ".log", ".svg"}:
        content_type += "; charset=utf-8"
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


class CourseRequestHandler(BaseHTTPRequestHandler):
    server_version = "RFIDCourseConsole/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[web] {self.address_string()} - {format % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/":
                text_response(self, ADVANCED_INDEX_HTML)
            elif path == "/api/health":
                json_response(self, {"ok": True, "project_root": str(PROJECT_ROOT)})
            elif path == "/api/state":
                json_response(
                    self,
                    {
                        "catalog": build_catalog(),
                        "runs": list_runs(12),
                        "topology": list_topology(),
                        "database": database_stats(),
                    },
                )
            elif path == "/api/selfopt/state":
                json_response(self, selfopt_state())
            elif path == "/api/selfopt/artifacts":
                json_response(self, {"artifacts": _experiment_artifact_rows(), "outputs": _output_artifact_rows()})
            elif path == "/api/selfopt/job":
                job_id = query.get("id", [""])[0]
                json_response(self, {"job": get_background_job(job_id)})
            elif path == "/api/selfopt/raw":
                rel_path = query.get("path", [""])[0]
                file_response(self, _resolve_project_path(rel_path))
            elif path == "/api/selfopt/file":
                rel_path = query.get("path", [""])[0]
                json_response(self, read_project_file(rel_path))
            elif path == "/api/runs":
                limit = int(query.get("limit", ["20"])[0])
                json_response(self, {"runs": list_runs(limit)})
            elif path.startswith("/api/runs/"):
                run_id = int(path.rsplit("/", 1)[-1])
                detail = get_run_detail(run_id)
                if detail is None:
                    json_response(self, {"error": "run not found"}, HTTPStatus.NOT_FOUND)
                else:
                    json_response(self, {"run": detail})
            elif path == "/api/topology":
                json_response(self, list_topology())
            elif path == "/api/database":
                json_response(self, database_stats())
            elif path == "/api/design/cases":
                json_response(self, {"cases": list_design_cases(20)})
            elif path.startswith("/api/design/cases/"):
                case_id = int(path.rsplit("/", 1)[-1])
                detail = get_design_case(case_id)
                if detail is None:
                    json_response(self, {"error": "design case not found"}, HTTPStatus.NOT_FOUND)
                else:
                    json_response(self, {"design_case": detail})
            else:
                json_response(self, {"error": f"unknown path: {path}"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            json_response(
                self,
                {"error": str(exc), "traceback": traceback.format_exc()},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = read_json_body(self)
            if path == "/api/runs":
                json_response(self, run_course_from_payload(payload))
            elif path == "/api/security":
                shared_secret = str(payload.get("shared_secret") or "rfid-course-demo-secret")
                result = run_security_check(shared_secret)
                check_id = store_security_check(result)
                json_response(self, {"check_id": check_id, "result": result, "database": database_stats()})
            elif path == "/api/ai/dry-run":
                json_response(self, run_ai_dry_run(payload))
            elif path == "/api/selfopt/file":
                json_response(self, save_project_file(payload))
            elif path == "/api/selfopt/scene":
                result = create_selfopt_scene(payload)
                json_response(self, result | {"state": selfopt_state()})
            elif path == "/api/selfopt/prompts":
                json_response(self, generate_crossover_prompt_files(payload))
            elif path == "/api/selfopt/ai-run":
                json_response(self, run_ai_generation(payload))
            elif path == "/api/selfopt/ai-run-async":
                json_response(self, {"job": start_background_task("ai_generation", payload)})
            elif path == "/api/selfopt/ai-models":
                json_response(self, probe_ai_runtime(payload))
            elif path == "/api/selfopt/validate":
                json_response(self, validate_selfopt_protocol(payload))
            elif path == "/api/selfopt/baseline-copy":
                result = copy_baseline_to_scene(payload)
                json_response(self, result | {"state": selfopt_state()})
            elif path == "/api/selfopt/batch-test":
                json_response(self, run_selfopt_batch_test(payload))
            elif path == "/api/selfopt/auto-iterate":
                json_response(self, run_selfopt_auto_iterate(payload))
            elif path == "/api/selfopt/auto-iterate-async":
                json_response(self, {"job": start_background_task("auto_iterate", payload)})
            elif path == "/api/selfopt/experiments":
                json_response(self, run_experiment_task(payload))
            elif path == "/api/design/preview":
                json_response(self, preview_design_case(payload))
            elif path in ("/api/design/analyze", "/api/design/from-natural-language", "/api/design/prompt"):
                json_response(self, analyze_design_case(payload))
            else:
                json_response(self, {"error": f"unknown path: {path}"}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            json_response(
                self,
                {"error": str(exc), "traceback": traceback.format_exc()},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RFID 云边端协同控制台</title>
  <style>
    :root {
      --bg: #f5f7fb;
      --surface: #ffffff;
      --surface-2: #eef3f8;
      --text: #17202a;
      --muted: #667085;
      --line: #d6dee8;
      --blue: #2563eb;
      --teal: #0f766e;
      --green: #2f855a;
      --orange: #b45309;
      --red: #b42318;
      --violet: #6d28d9;
      --shadow: 0 10px 24px rgba(15, 23, 42, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Arial, "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    button, input, select { font: inherit; }
    .app {
      display: grid;
      grid-template-columns: 248px minmax(0, 1fr);
      min-height: 100vh;
    }
    aside {
      background: #111827;
      color: #f9fafb;
      padding: 18px 14px;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
    }
    .brand {
      padding: 8px 8px 18px;
      border-bottom: 1px solid rgba(255,255,255,0.16);
      margin-bottom: 12px;
    }
    .brand strong { display: block; font-size: 18px; line-height: 1.35; }
    .brand span { display: block; color: #cbd5e1; font-size: 12px; margin-top: 6px; line-height: 1.5; }
    nav button {
      width: 100%;
      height: 42px;
      display: flex;
      align-items: center;
      gap: 10px;
      border: 0;
      border-radius: 8px;
      padding: 0 10px;
      color: #dbeafe;
      background: transparent;
      cursor: pointer;
      text-align: left;
      margin: 4px 0;
    }
    nav button.active, nav button:hover { background: #1f2937; color: #ffffff; }
    nav svg { width: 18px; height: 18px; flex: 0 0 auto; }
    main { padding: 22px 28px 44px; min-width: 0; }
    header.top {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      margin-bottom: 18px;
    }
    h1 { margin: 0 0 6px; font-size: 24px; letter-spacing: 0; }
    h2 { margin: 0 0 14px; font-size: 18px; letter-spacing: 0; }
    h3 { margin: 0 0 10px; font-size: 15px; letter-spacing: 0; }
    .muted { color: var(--muted); font-size: 13px; line-height: 1.55; }
    .status {
      min-width: 180px;
      border: 1px solid var(--line);
      background: var(--surface);
      border-radius: 8px;
      padding: 10px 12px;
      box-shadow: var(--shadow);
      font-size: 13px;
    }
    .dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      display: inline-block;
      margin-right: 7px;
      background: var(--green);
    }
    .dot.busy { background: var(--orange); }
    .dot.error { background: var(--red); }
    .layout {
      display: grid;
      grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
      gap: 16px;
      align-items: start;
    }
    .panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      margin-bottom: 16px;
      overflow: hidden;
    }
    .panel.pad { padding: 16px; }
    .panel-head {
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .form-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    label { display: block; font-size: 12px; color: #475467; margin-bottom: 6px; }
    input, select {
      width: 100%;
      min-height: 38px;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      padding: 8px 10px;
      background: #fff;
      color: var(--text);
    }
    input[type="checkbox"] { width: 16px; min-height: 16px; margin: 0; }
    .check-row {
      display: flex;
      align-items: center;
      gap: 8px;
      padding-top: 20px;
      color: #344054;
      font-size: 13px;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 14px;
    }
    .btn {
      min-height: 38px;
      border: 1px solid transparent;
      border-radius: 8px;
      padding: 8px 12px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      background: #111827;
      color: #ffffff;
    }
    .btn.secondary { background: #ffffff; color: #17202a; border-color: var(--line); }
    .btn.blue { background: var(--blue); }
    .btn.teal { background: var(--teal); }
    .btn:disabled { opacity: 0.55; cursor: progress; }
    .metrics {
      display: grid;
      grid-template-columns: repeat(6, minmax(120px, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }
    .metric {
      background: var(--surface);
      border: 1px solid var(--line);
      border-left: 5px solid var(--blue);
      border-radius: 8px;
      padding: 12px 14px;
      min-height: 78px;
      box-shadow: var(--shadow);
    }
    .metric span { display: block; color: var(--muted); font-size: 12px; margin-bottom: 8px; }
    .metric strong { font-size: 22px; line-height: 1.2; word-break: break-word; }
    .metric.teal { border-left-color: var(--teal); }
    .metric.green { border-left-color: var(--green); }
    .metric.orange { border-left-color: var(--orange); }
    .metric.red { border-left-color: var(--red); }
    .metric.violet { border-left-color: var(--violet); }
    .tabs { display: none; }
    .tabs.active { display: block; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { border-bottom: 1px solid var(--line); padding: 9px 10px; text-align: left; vertical-align: top; }
    th { background: #f9fafb; color: #344054; font-weight: 700; }
    tbody tr:hover { background: #f8fbff; }
    .scroll { overflow: auto; max-height: 420px; }
    .run-list button {
      width: 100%;
      border: 0;
      border-bottom: 1px solid var(--line);
      background: #fff;
      text-align: left;
      padding: 12px 14px;
      cursor: pointer;
      color: var(--text);
    }
    .run-list button:hover { background: #f3f6fb; }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--surface-2);
      color: #344054;
      font-size: 12px;
      white-space: nowrap;
    }
    .topology {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
    }
    .node {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fff;
      min-height: 112px;
    }
    .node strong { display: block; margin-bottom: 8px; }
    .bar {
      height: 10px;
      background: #e5e7eb;
      border-radius: 999px;
      overflow: hidden;
      min-width: 90px;
    }
    .bar span { display: block; height: 100%; background: var(--teal); }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      background: #101828;
      color: #f9fafb;
      border-radius: 8px;
      padding: 12px;
      max-height: 340px;
      overflow: auto;
      font-size: 12px;
    }
    .notice {
      border-left: 4px solid var(--blue);
      background: #eff6ff;
      padding: 10px 12px;
      border-radius: 8px;
      color: #1d4ed8;
      font-size: 13px;
      line-height: 1.55;
      margin-bottom: 12px;
    }
    @media (max-width: 1040px) {
      .app { grid-template-columns: 1fr; }
      aside { position: static; height: auto; }
      .layout { grid-template-columns: 1fr; }
      .metrics { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
      main { padding: 16px; }
      header.top { flex-direction: column; }
      .status { width: 100%; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <div class="brand">
        <strong>RFID 云边端协同控制台</strong>
        <span>智慧仓储课程项目</span>
      </div>
      <nav id="nav"></nav>
    </aside>
    <main>
      <header class="top">
        <div>
          <h1>智慧仓储 RFID 物联网系统</h1>
          <div class="muted">端侧采集、边缘推理、云端流处理、数据库审计与大模型协议优化</div>
        </div>
        <div class="status"><span id="statusDot" class="dot"></span><span id="statusText">就绪</span></div>
      </header>

      <section id="tab-console" class="tabs active">
        <div class="layout">
          <div>
            <section class="panel pad">
              <h2>运行配置</h2>
              <form id="runForm">
                <div class="form-grid">
                  <div><label>场景</label><select id="scene"></select></div>
                  <div><label>边缘协议</label><select id="protocol"></select></div>
                  <div><label>终端读写器</label><input id="devices" type="number" min="1" max="48"></div>
                  <div><label>边缘网关</label><input id="gateways" type="number" min="1" max="12"></div>
                  <div><label>采集批次</label><input id="batches" type="number" min="1" max="20"></div>
                  <div><label>每批标签数</label><input id="numTags" type="number" min="20" max="3000"></div>
                  <div><label>价格下限</label><input id="priceMin" type="number" min="1"></div>
                  <div><label>价格上限</label><input id="priceMax" type="number" min="2"></div>
                  <div><label>流处理窗口</label><input id="streamWindow" type="number" min="1" max="50"></div>
                  <div><label>签名密钥</label><input id="sharedSecret" type="password" value="rfid-course-demo-secret"></div>
                  <div class="check-row"><input id="includeSecurity" type="checkbox" checked><span>运行安全测试</span></div>
                  <div class="check-row"><input id="includeBenchmark" type="checkbox"><span>运行基准实验</span></div>
                </div>
                <div class="actions">
                  <button class="btn blue" id="startRun" type="submit">▶ 开始运行</button>
                  <button class="btn secondary" id="refreshState" type="button">↻ 刷新状态</button>
                </div>
              </form>
            </section>
            <section class="panel">
              <div class="panel-head"><h2>历史运行</h2><span class="pill" id="runCount">0</span></div>
              <div class="run-list" id="runList"></div>
            </section>
          </div>
          <div>
            <div class="metrics" id="metrics"></div>
            <section class="panel">
              <div class="panel-head"><h2>实时流处理窗口</h2><span class="pill" id="currentRunLabel">未选择运行</span></div>
              <div class="scroll"><table><thead><tr><th>批次</th><th>网关</th><th>终端</th><th>识别价格</th><th>窗口命中率</th><th>平均耗时</th><th>告警</th></tr></thead><tbody id="streamRows"></tbody></table></div>
            </section>
            <section class="panel">
              <div class="panel-head"><h2>边缘网关汇总</h2></div>
              <div class="scroll"><table><thead><tr><th>网关</th><th>窗口数</th><th>平均协议耗时</th><th>平均帧长</th><th>命中率</th></tr></thead><tbody id="gatewayRows"></tbody></table></div>
            </section>
          </div>
        </div>
      </section>

      <section id="tab-topology" class="tabs">
        <section class="panel pad">
          <h2>设备拓扑</h2>
          <div class="notice">运行一次云边端流程后，数据库会更新终端读写器与边缘网关的在线状态。</div>
          <h3>边缘网关</h3>
          <div class="topology" id="gatewayNodes"></div>
          <h3 style="margin-top:18px">端侧设备</h3>
          <div class="topology" id="deviceNodes"></div>
        </section>
      </section>

      <section id="tab-results" class="tabs">
        <div class="layout">
          <section class="panel">
            <div class="panel-head"><h2>全局热点价格</h2></div>
            <div class="scroll"><table><thead><tr><th>价格</th><th>热度</th><th>次数</th></tr></thead><tbody id="hotRows"></tbody></table></div>
          </section>
          <section class="panel">
            <div class="panel-head"><h2>异常告警</h2></div>
            <div class="scroll"><table><thead><tr><th>网关</th><th>终端</th><th>批次</th><th>告警</th></tr></thead><tbody id="alertRows"></tbody></table></div>
          </section>
        </div>
        <section class="panel">
          <div class="panel-head"><h2>可扩展性基准实验</h2></div>
          <div class="scroll"><table><thead><tr><th>终端</th><th>网关</th><th>批次</th><th>标签</th><th>命中率</th><th>协议耗时</th><th>实测秒</th></tr></thead><tbody id="benchmarkRows"></tbody></table></div>
        </section>
        <section class="panel pad">
          <h2>输出文件</h2>
          <pre id="pathBlock">暂无输出</pre>
        </section>
      </section>

      <section id="tab-security" class="tabs">
        <section class="panel pad">
          <h2>通信安全测试</h2>
          <div class="form-grid">
            <div><label>签名密钥</label><input id="securitySecret" type="password" value="rfid-course-demo-secret"></div>
            <div class="actions" style="align-items:end"><button class="btn teal" id="runSecurity" type="button">✓ 运行校验</button></div>
          </div>
          <pre id="securityResult">暂无安全测试结果</pre>
        </section>
      </section>

      <section id="tab-ai" class="tabs">
        <section class="panel pad">
          <h2>大模型协议优化任务</h2>
          <div class="notice">此处默认执行 dry-run，生成提示词文件和目标协议路径，不会调用外部 API。</div>
          <div class="form-grid">
            <div><label>场景</label><select id="aiScene"></select></div>
            <div><label>父代协议 A</label><select id="parentA"></select></div>
            <div><label>父代协议 B</label><select id="parentB"></select></div>
            <div><label>输出协议名</label><input id="protocolName" value="g_course_ai_demo"></div>
            <div><label>目标世代</label><input id="targetGeneration" value="next"></div>
            <div class="actions" style="align-items:end"><button class="btn blue" id="runAi" type="button">◇ 生成任务</button></div>
          </div>
          <pre id="aiResult">暂无大模型优化任务</pre>
        </section>
      </section>

      <section id="tab-database" class="tabs">
        <section class="panel pad">
          <h2>数据库状态</h2>
          <div class="metrics" id="dbMetrics"></div>
          <pre id="dbPath"></pre>
        </section>
        <section class="panel">
          <div class="panel-head"><h2>系统事件</h2></div>
          <div class="scroll"><table><thead><tr><th>ID</th><th>时间</th><th>类型</th><th>对象</th><th>消息</th></tr></thead><tbody id="eventRows"></tbody></table></div>
        </section>
        <section class="panel">
          <div class="panel-head"><h2>AI 任务记录</h2></div>
          <div class="scroll"><table><thead><tr><th>ID</th><th>时间</th><th>场景</th><th>父代</th><th>输出协议</th><th>状态</th></tr></thead><tbody id="aiJobRows"></tbody></table></div>
        </section>
      </section>
    </main>
  </div>
  <script>
    const tabs = [
      ["console", "控制台", "M4 4h16v16H4z M8 8h8v4H8z M8 14h8v6H8z"],
      ["topology", "拓扑", "M12 3v6 M6 21v-6h12v6 M6 15l6-6 6 6"],
      ["results", "结果", "M4 19h16 M7 16V8 M12 16V5 M17 16v-4"],
      ["security", "安全", "M12 3l7 4v5c0 5-3 8-7 9-4-1-7-4-7-9V7z"],
      ["ai", "大模型", "M12 3l2.5 5 5.5.8-4 3.8 1 5.4-5-2.7-5 2.7 1-5.4-4-3.8 5.5-.8z"],
      ["database", "数据库", "M4 6c0-2 16-2 16 0v12c0 2-16 2-16 0z M4 6v6c0 2 16 2 16 0 M4 12v6"],
    ];
    const state = { catalog: null, runs: [], currentRun: null, topology: null, database: null };
    const $ = (id) => document.getElementById(id);

    function icon(path) {
      return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="${path}"></path></svg>`;
    }
    function setStatus(text, mode = "ready") {
      $("statusText").textContent = text;
      $("statusDot").className = "dot" + (mode === "busy" ? " busy" : mode === "error" ? " error" : "");
    }
    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
    }
    function tableEmpty(cols, text = "暂无数据") { return `<tr><td colspan="${cols}">${text}</td></tr>`; }
    async function api(path, options = {}) {
      const res = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "请求失败");
      return data;
    }
    function mountNav() {
      $("nav").innerHTML = tabs.map(([id, label, path], index) =>
        `<button class="${index === 0 ? "active" : ""}" data-tab="${id}">${icon(path)}<span>${label}</span></button>`
      ).join("");
      document.querySelectorAll("nav button").forEach((btn) => {
        btn.addEventListener("click", () => {
          document.querySelectorAll("nav button").forEach((item) => item.classList.remove("active"));
          document.querySelectorAll(".tabs").forEach((item) => item.classList.remove("active"));
          btn.classList.add("active");
          $("tab-" + btn.dataset.tab).classList.add("active");
        });
      });
    }
    function fillSelect(select, values, selected) {
      select.innerHTML = values.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
      if (selected && values.includes(selected)) select.value = selected;
    }
    function refreshProtocolSelects() {
      const scene = $("scene").value;
      const protocols = state.catalog.protocols[scene] || [];
      fillSelect($("protocol"), protocols, state.catalog.defaults.protocol);
      fillSelect($("aiScene"), state.catalog.scenes, scene);
      fillSelect($("parentA"), protocols, protocols.includes("g2_1") ? "g2_1" : protocols[0]);
      fillSelect($("parentB"), protocols, protocols.includes("g2_2") ? "g2_2" : protocols[1] || protocols[0]);
    }
    function renderMetrics(run) {
      const report = run?.result?.batch_report || {};
      const values = [
        ["终端", run?.devices ?? 0, "teal"],
        ["网关", run?.gateways ?? 0, "blue"],
        ["窗口", report.total_windows ?? run?.total_windows ?? 0, "green"],
        ["命中率", report.average_hit_rate ?? run?.average_hit_rate ?? 0, "orange"],
        ["协议耗时", run?.avg_gateway_protocol_time_ms ?? 0, "violet"],
        ["告警", report.alerts?.length ?? run?.alert_count ?? 0, "red"],
      ];
      $("metrics").innerHTML = values.map(([label, value, tone]) =>
        `<div class="metric ${tone}"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`
      ).join("");
    }
    function renderRuns() {
      $("runCount").textContent = state.runs.length;
      if (!state.runs.length) {
        $("runList").innerHTML = `<div class="muted" style="padding:14px">暂无历史运行</div>`;
        return;
      }
      $("runList").innerHTML = state.runs.map((run) => `
        <button data-run="${run.id}">
          <strong>#${run.id} ${escapeHtml(run.scene)} / ${escapeHtml(run.protocol)}</strong>
          <div class="muted">${escapeHtml(run.created_at)} · ${run.devices} 端 · ${run.gateways} 边 · 命中率 ${run.average_hit_rate}</div>
        </button>
      `).join("");
      document.querySelectorAll("[data-run]").forEach((btn) => {
        btn.addEventListener("click", () => loadRun(Number(btn.dataset.run)));
      });
    }
    function renderRunDetail(detail) {
      state.currentRun = detail;
      $("currentRunLabel").textContent = detail ? `运行 #${detail.id}` : "未选择运行";
      renderMetrics(detail);
      const streams = detail?.result?.stream_snapshots || [];
      $("streamRows").innerHTML = streams.length ? streams.map((item) => `
        <tr><td>${escapeHtml(item.latest_batch)}</td><td>${escapeHtml(item.latest_gateway)}</td><td>${escapeHtml(item.latest_device)}</td>
        <td>${escapeHtml((item.latest_modes || []).join(", "))}</td><td>${escapeHtml(item.recent_hit_rate)}</td>
        <td>${escapeHtml(item.recent_avg_protocol_time_ms)}</td><td>${escapeHtml(item.recent_alert_count)}</td></tr>
      `).join("") : tableEmpty(7);
      const gatewayRows = Object.entries(detail?.result?.batch_report?.gateway_summary || {});
      $("gatewayRows").innerHTML = gatewayRows.length ? gatewayRows.map(([id, item]) => `
        <tr><td>${escapeHtml(id)}</td><td>${escapeHtml(item.windows)}</td><td>${escapeHtml(item.avg_protocol_time_ms)}</td>
        <td>${escapeHtml(item.avg_frame_size)}</td><td>${escapeHtml(item.hit_rate)}</td></tr>
      `).join("") : tableEmpty(5);
      const hot = detail?.result?.batch_report?.global_hot_prices || [];
      const maxCount = Math.max(1, ...hot.map((item) => Number(item.count || 0)));
      $("hotRows").innerHTML = hot.length ? hot.map((item) => {
        const width = Math.max(8, Math.round(Number(item.count || 0) / maxCount * 100));
        return `<tr><td>${escapeHtml(item.price)}</td><td><div class="bar"><span style="width:${width}%"></span></div></td><td>${escapeHtml(item.count)}</td></tr>`;
      }).join("") : tableEmpty(3);
      const alerts = detail?.result?.batch_report?.alerts || [];
      $("alertRows").innerHTML = alerts.length ? alerts.map((item) => `
        <tr><td>${escapeHtml(item.gateway_id)}</td><td>${escapeHtml(item.device_id)}</td><td>${escapeHtml(item.batch_index)}</td><td>${escapeHtml(item.alert)}</td></tr>
      `).join("") : tableEmpty(4, "暂无异常告警");
      const benchmark = detail?.benchmark || [];
      $("benchmarkRows").innerHTML = benchmark.length ? benchmark.map((item) => `
        <tr><td>${escapeHtml(item.devices)}</td><td>${escapeHtml(item.gateways)}</td><td>${escapeHtml(item.batches)}</td>
        <td>${escapeHtml(item.num_tags)}</td><td>${escapeHtml(item.average_hit_rate)}</td>
        <td>${escapeHtml(item.avg_gateway_protocol_time_ms)}</td><td>${escapeHtml(item.wall_clock_s)}</td></tr>
      `).join("") : tableEmpty(7, "本次未运行基准实验");
      $("pathBlock").textContent = JSON.stringify({
        output_dir: detail?.output_dir,
        dashboard_path: detail?.dashboard_path,
      }, null, 2);
    }
    function renderTopology(topology) {
      const gateways = topology?.gateways || [];
      const devices = topology?.devices || [];
      $("gatewayNodes").innerHTML = gateways.length ? gateways.map((item) => `
        <div class="node"><strong>${escapeHtml(item.name)}</strong><div class="muted">${escapeHtml(item.gateway_id)} · ${escapeHtml(item.location)}</div><div class="pill" style="margin-top:10px">${escapeHtml(item.status)}</div></div>
      `).join("") : `<div class="muted">暂无网关记录</div>`;
      $("deviceNodes").innerHTML = devices.length ? devices.map((item) => `
        <div class="node"><strong>${escapeHtml(item.name)}</strong><div class="muted">${escapeHtml(item.device_id)} → ${escapeHtml(item.gateway_id)}</div><div class="muted">${escapeHtml(item.device_type)} · ${escapeHtml(item.location)}</div></div>
      `).join("") : `<div class="muted">暂无端侧设备记录</div>`;
    }
    function renderDatabase(db) {
      const counts = db?.counts || {};
      const keys = ["runs", "devices", "gateways", "stream_snapshots", "alerts", "ai_jobs"];
      $("dbMetrics").innerHTML = keys.map((key, index) => {
        const tones = ["blue", "teal", "green", "orange", "red", "violet"];
        return `<div class="metric ${tones[index]}"><span>${key}</span><strong>${escapeHtml(counts[key] ?? 0)}</strong></div>`;
      }).join("");
      $("dbPath").textContent = db?.db_path || "";
      const events = db?.recent_events || [];
      $("eventRows").innerHTML = events.length ? events.map((item) => `
        <tr><td>${item.id}</td><td>${escapeHtml(item.created_at)}</td><td>${escapeHtml(item.event_type)}</td><td>${escapeHtml(item.entity_type || "")} ${escapeHtml(item.entity_id || "")}</td><td>${escapeHtml(item.message)}</td></tr>
      `).join("") : tableEmpty(5);
      const jobs = db?.ai_jobs || [];
      $("aiJobRows").innerHTML = jobs.length ? jobs.map((item) => `
        <tr><td>${item.id}</td><td>${escapeHtml(item.created_at)}</td><td>${escapeHtml(item.scene)}</td><td>${escapeHtml(item.parent_a)} + ${escapeHtml(item.parent_b)}</td><td>${escapeHtml(item.protocol_name)}</td><td>${escapeHtml(item.status)}</td></tr>
      `).join("") : tableEmpty(6);
    }
    async function loadState() {
      setStatus("加载状态", "busy");
      const data = await api("/api/state");
      state.catalog = data.catalog;
      state.runs = data.runs || [];
      state.topology = data.topology;
      state.database = data.database;
      fillSelect($("scene"), state.catalog.scenes, state.catalog.defaults.scene);
      $("devices").value = state.catalog.defaults.devices;
      $("gateways").value = state.catalog.defaults.gateways;
      $("batches").value = state.catalog.defaults.batches;
      $("numTags").value = state.catalog.defaults.num_tags;
      $("priceMin").value = state.catalog.defaults.price_min;
      $("priceMax").value = state.catalog.defaults.price_max;
      $("streamWindow").value = state.catalog.defaults.stream_window;
      refreshProtocolSelects();
      renderRuns();
      renderTopology(state.topology);
      renderDatabase(state.database);
      if (state.runs[0]) await loadRun(state.runs[0].id);
      else renderMetrics(null);
      setStatus("就绪");
    }
    async function loadRun(id) {
      setStatus(`加载运行 #${id}`, "busy");
      const data = await api(`/api/runs/${id}`);
      renderRunDetail(data.run);
      setStatus("就绪");
    }
    async function startRun(event) {
      event.preventDefault();
      const payload = {
        scene: $("scene").value,
        protocol: $("protocol").value,
        devices: Number($("devices").value),
        gateways: Number($("gateways").value),
        batches: Number($("batches").value),
        num_tags: Number($("numTags").value),
        price_min: Number($("priceMin").value),
        price_max: Number($("priceMax").value),
        stream_window: Number($("streamWindow").value),
        shared_secret: $("sharedSecret").value,
        include_security: $("includeSecurity").checked,
        include_benchmark: $("includeBenchmark").checked,
        dashboard: true,
      };
      $("startRun").disabled = true;
      setStatus("云边端流程运行中", "busy");
      try {
        const data = await api("/api/runs", { method: "POST", body: JSON.stringify(payload) });
        state.runs.unshift(data.detail);
        await loadState();
        await loadRun(data.run_id);
        setStatus(`完成运行 #${data.run_id}`);
      } catch (err) {
        setStatus(err.message, "error");
        alert(err.message);
      } finally {
        $("startRun").disabled = false;
      }
    }
    async function runSecurityOnly() {
      setStatus("安全校验运行中", "busy");
      try {
        const data = await api("/api/security", { method: "POST", body: JSON.stringify({ shared_secret: $("securitySecret").value }) });
        $("securityResult").textContent = JSON.stringify(data.result, null, 2);
        state.database = data.database;
        renderDatabase(state.database);
        setStatus("安全校验完成");
      } catch (err) {
        setStatus(err.message, "error");
      }
    }
    async function runAiDry() {
      setStatus("生成大模型任务", "busy");
      try {
        const payload = {
          scene: $("aiScene").value,
          parent_a: $("parentA").value,
          parent_b: $("parentB").value,
          protocol_name: $("protocolName").value,
          target_generation: $("targetGeneration").value,
        };
        const data = await api("/api/ai/dry-run", { method: "POST", body: JSON.stringify(payload) });
        $("aiResult").textContent = JSON.stringify(data, null, 2);
        const db = await api("/api/database");
        renderDatabase(db);
        setStatus("大模型任务已生成");
      } catch (err) {
        setStatus(err.message, "error");
        alert(err.message);
      }
    }
    function bindEvents() {
      $("scene").addEventListener("change", refreshProtocolSelects);
      $("aiScene").addEventListener("change", () => {
        const protocols = state.catalog.protocols[$("aiScene").value] || [];
        fillSelect($("parentA"), protocols, protocols[0]);
        fillSelect($("parentB"), protocols, protocols[1] || protocols[0]);
      });
      $("runForm").addEventListener("submit", startRun);
      $("refreshState").addEventListener("click", loadState);
      $("runSecurity").addEventListener("click", runSecurityOnly);
      $("runAi").addEventListener("click", runAiDry);
    }
    mountNav();
    bindEvents();
    loadState().catch((err) => {
      console.error(err);
      setStatus(err.message, "error");
    });
  </script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the interactive RFID cloud-edge-device course console.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    init_database()
    server = ThreadingHTTPServer((args.host, args.port), CourseRequestHandler)
    print(f"RFID course console: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down RFID course console.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

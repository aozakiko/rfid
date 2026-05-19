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
from prompt_generation import create_crossover_prompts
from Validator import ProtocolValidator
from course_project.database import (
    database_stats,
    init_database,
    store_ai_job,
)
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
    output_roots = [COURSE_ROOT / "outputs"]
    output_roots.extend(PROJECT_ROOT / scene / "outputs" for scene in discover_scenes())
    suffixes = {".csv", ".json", ".html", ".md", ".txt", ".log", ".pdf", ".png", ".svg"}
    rows: list[dict[str, Any]] = []
    for output_root in output_roots:
        if not output_root.exists():
            continue
        for file_path in output_root.rglob("*"):
            if not file_path.is_file() or file_path.suffix.lower() not in suffixes:
                continue
            try:
                rel = _relative_to_project(file_path)
            except ValueError:
                continue
            run_name = file_path.parent.name if file_path.parent != output_root else output_root.parent.name
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
    scene_protocols = [row["name"] for row in _protocol_file_rows(scene_name)]
    selected = str(payload.get("protocols") or "").split(":", 1)[0]
    candidate_names = ([selected] if selected else []) + scene_protocols
    if not scene_protocols:
        candidate_names.extend(discover_protocols(scene_name))
    for name in candidate_names:
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
            "num_tags": 80,
            "price_min": 1,
            "price_max": 200,
        },
    }


def _coerce_int(payload: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(payload.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


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
            elif path == "/api/database":
                json_response(self, database_stats())
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
            if path == "/api/selfopt/file":
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the interactive RFID protocol self-optimization Web platform.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    init_database()
    server = ThreadingHTTPServer((args.host, args.port), CourseRequestHandler)
    print(f"RFID self-optimization platform: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down RFID self-optimization platform.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


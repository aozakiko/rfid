from __future__ import annotations

import argparse
import hashlib
import fnmatch
import importlib.util
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_protocol_optimizer import (
    CUSTOM_TASK_PROFILE,
    TASK_GUIDANCE,
    run_ai_optimization_workflow,
    task_guidance,
    validate_task_profile,
)
from backend_tools.openai_compatible import LLMClientError, LLMConfig, OpenAICompatibleClient
from prompt_generation import create_crossover_prompts
from course_project.database import (
    database_stats,
    get_validation_report,
    init_database,
    list_validation_reports,
    store_ai_job,
    store_validation_report,
)
from course_project.selfopt_ui import INDEX_HTML as ADVANCED_INDEX_HTML
from course_project.http_support import (
    APIError, json_response, text_response, read_json_body, send_bytes, request_error as _request_error,
)
from course_project.jobs import JobManager, JobCancelled
from course_project.frontend_assets import serve_frontend
from course_project.validation import bounded_int as _coerce_int, bounded_float, boolean_value
from course_project.assets import import_file, preview_file
from course_project.candidate_evaluation import evaluate_protocol, select_population, feedback_for, save_json, independent_validation
from course_project.optimization_control import OptimizationControl, OptimizationStopped
from course_project.evolution_selection import OBJECTIVES, select as selection_report, signature as pareto_signature
from generic_protocol.security import analyze_file
from generic_protocol.subprocess_runner import run_protocol_file
from generic_tasks import list_profiles
from course_project.task_process import check_cancelled


COURSE_ROOT = Path(__file__).resolve().parent
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
GENERATION_RE = re.compile(r"^generation_?(\d+)$")
TEXT_FILE_SUFFIXES = {".py", ".txt", ".json", ".csv", ".md", ".log", ".html"}
MAX_WEB_FILE_BYTES = 900_000
JOB_MANAGER = JobManager(history_path=COURSE_ROOT / "outputs" / "background_jobs.json")
STATIC_ROOT = COURSE_ROOT / "static"
STATIC_FILES = {"app.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8"}
STATIC_FILES.update({"asset_ui.js": "text/javascript; charset=utf-8", "assets.css": "text/css; charset=utf-8"})
STATIC_FILES["statistics.js"] = "text/javascript; charset=utf-8"
FILE_WRITE_LOCK = threading.Lock()


def _web_llm_config(payload: dict[str, Any], *, probe: bool = False) -> LLMConfig:
    """Resolve each Web request exclusively from the submitted form."""
    def text_value(name: str, label: str, *, optional: bool = False) -> str:
        value = payload.get(name)
        if optional and (value is None or isinstance(value, str) and not value.strip()):
            return ""
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"请在模型配置中填写{label}。")
        return value.strip()

    base_url = text_value("base_url", " URL").rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL 必须是有效的 http 或 https 地址。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("请填写不含账号、密钥、查询参数或片段的 API 基础 URL。")
    # Accept either the gateway base URL or a copied complete API URL.
    for suffix in ("/chat/completions", "/responses"):
        if base_url.endswith(suffix):
            base_url = base_url[:-len(suffix)].rstrip("/")
            break
    model = text_value("model", "模型名", optional=probe and payload.get("mode") == "list")
    api_key = text_value("api_key_value" if "api_key_value" in payload else "api_key", " API Key")
    if any(character.isspace() for character in api_key):
        raise ValueError("API Key 中不能包含空格或换行。")
    return LLMConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        endpoint_path="auto",
        temperature=0.0 if probe else bounded_float(payload, "temperature", 0.2, 0.0, 2.0),
        max_tokens=_coerce_int(payload, "max_tokens", 64 if probe else 2800, 16 if probe else 256, 512 if probe else 32000),
        timeout_seconds=_coerce_int(payload, "timeout_seconds", 60 if probe else 120, 10 if probe else 20, 300 if probe else 3600),
        retry_attempts=_coerce_int(payload, "retry_attempts", 3, 1, 6),
    )


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


def _assert_editable_project_file(path: Path, *, check_size: bool = True) -> None:
    if check_size:
        _assert_text_file(path)
    elif path.suffix.lower() not in TEXT_FILE_SUFFIXES:
        raise ValueError("不支持该文本资产类型")
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


def _prompt_rows(scene: str, *, limit: int | None = None) -> list[dict[str, Any]]:
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


def _experiment_artifact_rows(*, limit: int | None = None) -> list[dict[str, Any]]:
    roots = [PROJECT_ROOT / "Experiments"]
    rows: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for file_path in root.rglob("*"):
            if not file_path.is_file() or file_path.suffix.lower() in {".py", ".pyc"} or any(part.startswith(".") or part == "__pycache__" for part in file_path.relative_to(root).parts):
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
    return discover_scenes()


def _output_artifact_rows(*, limit: int | None = None) -> list[dict[str, Any]]:
    output_roots = [COURSE_ROOT / "outputs"]
    output_roots.extend(PROJECT_ROOT / scene / "outputs" for scene in discover_scenes())
    rows: list[dict[str, Any]] = []
    for output_root in output_roots:
        if not output_root.exists():
            continue
        for file_path in output_root.rglob("*"):
            if not file_path.is_file() or any(part.startswith(".") or part == "__pycache__" for part in file_path.relative_to(output_root).parts):
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


def _statistical_benchmark_state() -> dict[str, Any]:
    from course_project.statistics_service import dashboard
    return dashboard()


def _assert_safe_raw_artifact(path: Path) -> None:
    resolved = path.resolve()
    allowed_roots = [(COURSE_ROOT / "outputs").resolve(), (PROJECT_ROOT / "Experiments").resolve()]
    allowed_roots.extend((PROJECT_ROOT / scene / "outputs").resolve() for scene in discover_scenes())
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
        "prompts": _prompt_rows(scene),
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
        "task_profiles": {profile.profile_id: task_guidance(profile.profile_id, "用户将在生成前填写自定义任务说明")
                          for profile in list_profiles()},
        "project_vision": {
            "name": "基于大模型的 RFID 射频识别通讯协议自优化研究平台",
            "source": "2025 年大学生创新创业训练计划项目申报书",
            "summary": "平台目标是把 RFID 协议设计从人工推导升级为智能生成，支持自然语言需求、GRDL 建模、扩展父代协议基因库、LLM 进化生成、在线仿真沙箱与三重正确性验证。",
            "statistics": ["mode_query", "mean_estimation", "variance_estimation", "median_quantile", "cardinality_estimation", "missing_stocktaking", CUSTOM_TASK_PROFILE],
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
            "configuration_mode": "page",
            "interface_mode": "auto",
        },
        "ai_jobs": db.get("ai_jobs", [])[:20],
        "database_counts": db.get("counts", {}),
    }


def get_background_job(job_id: str) -> dict[str, Any]:
    return JOB_MANAGER.get(job_id)


def retry_background_task(payload):
    previous = JOB_MANAGER.get(str(payload.get("id") or ""))
    if previous["status"] not in {"failed", "cancelled", "interrupted"}:
        raise APIError("仅可重新运行失败、已停止或已中断的任务", HTTPStatus.CONFLICT)
    if previous["kind"] not in {"ai_generation", "auto_iterate"}:
        raise ValueError("该任务请从原实验页面重新提交配置")
    replay = dict(previous["payload"])
    for key in ("api_key_value", "base_url", "model"):
        if payload.get(key):
            replay[key] = payload[key]
    suffix = "_retry_" + uuid4().hex[:8]
    key = "prefix" if previous["kind"] == "auto_iterate" else "protocol_name"
    replay[key] = str(replay.get(key) or "web_candidate") + suffix
    replay["retry_of"] = previous["id"]
    # Full rerun with fresh names, not silent replay of a partially written phase.
    return start_background_task(previous["kind"], replay)


def start_background_task(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    workflows = {"ai_generation": run_ai_generation, "auto_iterate": run_selfopt_auto_iterate}
    if kind not in workflows:
        raise ValueError(f"Unknown background task: {kind}")
    preview = boolean_value(payload, "dry_run", kind == "auto_iterate") or payload.get("run_mode") == "preview"
    if not preview:
        _web_llm_config(payload)

    def work(update):
        def progress_logger(message, progress=None, stage=None):
            update(stage=stage, progress=progress, log=str(message))
        progress_logger.check_cancelled = update.check_cancelled
        try:
            return workflows[kind](payload, progress_logger=progress_logger)
        except OptimizationStopped as exc:
            if exc.reason == "cancelled":
                raise JobCancelled("cancelled_by_user") from None
            if exc.reason == "time_budget":
                raise TimeoutError("model_generation_time_budget") from None
            raise APIError("模型请求预算已用尽。", 409, code="model_request_budget", stage="generation",
                suggestion="减少重试与修复轮数，或增加请求预算。") from None

    return JOB_MANAGER.submit(kind, payload, work)


def read_project_file(relative_path: str) -> dict[str, Any]:
    path = _resolve_project_path(relative_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"文件不存在: {relative_path}")
    _assert_text_file(path)
    content = path.read_bytes()
    return {
        "path": _relative_to_project(path),
        "name": path.name,
        "content": content.decode("utf-8-sig", errors="replace"),
        "revision": hashlib.sha256(content).hexdigest(),
        "size": path.stat().st_size,
        "mtime": int(path.stat().st_mtime),
    }


def save_project_file(payload: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_project_path(str(payload.get("path") or ""))
    _assert_editable_project_file(path)
    content = str(payload.get("content") or "")
    if len(content.encode("utf-8")) > MAX_WEB_FILE_BYTES:
        raise ValueError("保存内容过大")
    with FILE_WRITE_LOCK:
        expected_revision = payload.get("revision")
        if expected_revision is not None:
            current_revision = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
            if current_revision != expected_revision:
                raise APIError("文件已被其他操作修改，请重新读取后再保存", HTTPStatus.CONFLICT)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            # Replace atomically so concurrent readers never observe a partial file.
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as handle:
                temporary_path = Path(handle.name)
                handle.write(content.encode("utf-8"))
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    return {"saved": True, "path": _relative_to_project(path), "size": path.stat().st_size,
            "revision": hashlib.sha256(content.encode("utf-8")).hexdigest()}


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


def _scan_information_isolation(protocol_file: Path, *, strict: bool = False) -> dict[str, Any]:
    """Run the shared AST scanner in legacy-compatible or strict mode."""
    return analyze_file(protocol_file, strict=strict)


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


def validate_selfopt_protocol(payload: dict[str, Any], progress_logger=None) -> dict[str, Any]:
    from generic_protocol.validation import validate_candidate
    from generic_tasks import get_profile

    scene = _validate_scene(str(payload.get("scene") or build_catalog()["defaults"]["scene"]))
    protocol = _validate_identifier(str(payload.get("protocol") or ""), "协议名称")
    profile = validate_task_profile(str(payload.get("task_profile") or "mode_query"), payload.get("custom_task_text"))
    mode = str(payload.get("security_mode") or "legacy").strip().lower()
    if mode not in {"strict", "legacy"}:
        raise ValueError("security_mode 必须是 strict 或 legacy")
    if payload.get("candidate_id"):
        from course_project.research_service import candidate
        registered = candidate(payload["candidate_id"])
        if registered["family"] != "python" or registered["scene"] != scene or registered["task_profile"] != profile:
            raise ValueError("候选类型、场景或任务与本次验证不一致")
        path = _resolve_project_path(registered["snapshot_path"])
        protocol = registered["name"]
    else:
        path = _find_protocol_file(scene, protocol, include_base=True)
    generator, config = _load_scene_task(scene)
    price_min = _coerce_int(payload, "price_min", int(config.get("PRICE_MIN", 1)), 1, 100000)
    price_max = _coerce_int(payload, "price_max", int(config.get("PRICE_MAX", 1000)), price_min + 1, 1000000)
    partial_rows = []
    partial_path = COURSE_ROOT / "outputs" / "validation_tasks" / (uuid4().hex + ".json")
    def record_run(sample, done, total):
        partial_rows.append(sample)
        save_json(partial_path, {"partial": True, "protocol": protocol, "scene": scene, "runs": partial_rows,
            "completed_runs": done, "total_runs": total})
        progress_logger(stage="validation", progress=int(done/total*95), log=f"验证 {done}/{total}: Seed {sample['seed']}",
            result={"partial": True, "protocol": protocol, "completed_runs": done, "evidence_path": _relative_to_project(partial_path)})
    result = validate_candidate(path, protocol, generator, scene=scene, task_profile=profile,
        security_mode=mode, seed=_coerce_int(payload, "seed", 0, 0, 2**31 - 201),
        num_tags=_coerce_int(payload, "num_tags", 120, 20, 200000),
        total_runs=_coerce_int(payload, "total_runs", 3, 1, 200), price_min=price_min, price_max=price_max,
        relative_tolerance=bounded_float(payload, "relative_tolerance", .15, 0., 1.),
        absolute_tolerance=bounded_float(payload, "absolute_tolerance", 2., 0., 1e12),
        quantile=bounded_float(payload, "quantile", .5, 0., 1.),
        cancel_check=lambda: check_cancelled(progress_logger),
        on_run=record_run if progress_logger else None)
    result["task_title"] = TASK_GUIDANCE.get(profile, {}).get("title", get_profile(profile).title)
    result["custom_task_text"] = str(payload.get("custom_task_text") or "").strip() if profile == CUSTOM_TASK_PROFILE else ""
    result["protocol_path"] = _relative_to_project(path)
    if payload.get("candidate_id"):
        result["candidate_id"] = payload["candidate_id"]
    elif profile in {"mode_query", "mean_estimation", "variance_estimation", "median_quantile", "cardinality_estimation", "top_k"} and hashlib.sha256(path.read_bytes()).hexdigest() == result["source_sha256"]:
        from course_project.research_service import register
        result["candidate_id"] = register({"path": str(path), "scene": scene, "task_profile": profile,
            "expected_sha256": result["source_sha256"]})["id"]
    if result.get("candidate_id"):
        from course_project.research_service import record_validation_usage
        record_validation_usage(result["candidate_id"], result)
    result["validation_report_id"] = store_validation_report(result)
    if progress_logger:
        result["evidence_path"] = _relative_to_project(partial_path)
        save_json(partial_path, result)
    return result


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
    custom_task_text = str(payload.get("custom_task_text") or "").strip()
    validate_task_profile(task_profile, custom_task_text)
    protocol_name = _default_protocol_name_for_task(payload.get("protocol_name") or "web_ai_candidate", task_profile)
    target_generation = str(payload.get("target_generation") or "next")
    run_mode = str(payload.get("run_mode") or "generate").strip()
    if run_mode not in {"preview", "generate"}:
        raise ValueError("run_mode 必须是 preview 或 generate")
    dry_run = run_mode == "preview" or boolean_value(payload, "dry_run", False)
    requirement_text = str(payload.get("requirement_text") or "")
    retry_attempts = _coerce_int(payload, "retry_attempts", 3, 1, 6)
    llm_config = None if dry_run else _web_llm_config(payload)

    available = set(catalog["protocols"].get(scene, []))
    for parent in (parent_a, parent_b):
        if parent not in available:
            raise ValueError(f"场景 {scene} 中找不到父代协议: {parent}")

    logs: list[str] = []

    def emit(message: str, progress: int | None = None, stage: str | None = None) -> None:
        check_cancelled(progress_logger)
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

    single_control = OptimizationControl(max_requests=100, max_seconds=_coerce_int(payload, "max_wall_seconds", 3600, 1, 86400),
        cancel_check=getattr(progress_logger, "check_cancelled", None))
    result = run_ai_optimization_workflow(
        scene_name=scene,
        parent_a=parent_a,
        parent_b=parent_b,
        protocol_name=protocol_name,
        target_generation=target_generation,
        llm_config=llm_config,
        temperature=bounded_float(payload, "temperature", 0.2, 0.0, 2.0),
        max_tokens=_coerce_int(payload, "max_tokens", 2800, 256, 32000),
        timeout_seconds=_coerce_int(payload, "timeout_seconds", 120, 20, 3600),
        validate_num_tags=_coerce_int(payload, "validate_num_tags", 120, 20, 5000),
        max_repair_rounds=_coerce_int(payload, "max_repair_rounds", 0 if dry_run else 1, 0, 8),
        dry_run=dry_run,
        retry_attempts=retry_attempts,
        task_profile=task_profile,
        requirement_text=requirement_text,
            custom_task_text=custom_task_text,
        logger=lambda message: emit(
            str(message),
            progress=workflow_progress(str(message))[0],
            stage=workflow_progress(str(message))[1],
        ),
        **({"request_guard": single_control.before_request, "evaluation_deadline": single_control.started + single_control.max_seconds} if progress_logger and not dry_run else {}),
    )
    emit(f"AI 任务返回状态: {result.get('status')}", progress=96, stage="persisting")
    result["task_profile"] = task_profile
    result["task_title"] = task_guidance(task_profile, custom_task_text)["title"]
    if not dry_run and result.get("target_file") and Path(result["target_file"]).is_file():
        from course_project.research_service import register_generated
        registered = register_generated(result["target_file"], scene, task_profile, report=result.get("validation"))
        result["candidate_id"] = registered["id"]
    request = {
        "scene": scene,
        "parent_a": parent_a,
        "parent_b": parent_b,
        "protocol_name": protocol_name,
        "target_generation": target_generation,
        "dry_run": dry_run,
        "task_profile": task_profile,
        "custom_task_text": custom_task_text if task_profile == CUSTOM_TASK_PROFILE else "",
        "run_mode": "preview" if dry_run else "generate",
    }
    job_id = store_ai_job(request=request, result=result, logs=logs)
    return {"job_id": job_id, "request": request, "result": result, "logs": logs, "state": selfopt_state()}


def probe_ai_runtime(payload: dict[str, Any]) -> dict[str, Any]:
    config = _web_llm_config(payload, probe=True)
    client = OpenAICompatibleClient(config)
    mode = str(payload.get("mode") or "probe")
    if mode not in {"list", "probe"}:
        raise ValueError("模型检查模式必须是 list 或 probe。")
    try:
        if mode == "list":
            return {"mode": "list", "models": client.list_models(), "base_url": config.base_url}
        text = client.generate_text(system_prompt="You are a health check endpoint.", user_prompt="Reply with exactly OK.")
    except LLMClientError as exc:
        raise APIError(str(exc), HTTPStatus.BAD_GATEWAY) from exc
    protocol = "Responses" if client.resolved_endpoint == "/v1/responses" else "Chat Completions"
    return {"mode": "probe", "reply": text.strip(), "model": config.model, "base_url": config.base_url, "protocol": protocol}


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


def run_selfopt_batch_test(payload: dict[str, Any], progress_logger=None) -> dict[str, Any]:
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
    profile = validate_task_profile(str(payload.get("task_profile") or "mode_query"), payload.get("custom_task_text"))
    mode = str(payload.get("security_mode") or "strict")
    if mode not in {"strict", "legacy"}:
        raise ValueError("security_mode 必须是 strict 或 legacy")
    available_names = set(discover_protocols(scene)) | {p.stem for p in (PROJECT_ROOT / "base_protocol").glob("*.py") if p.stem != "__init__"}
    names = [name for name in sorted(available_names)
             if any(pattern == "all" or fnmatch.fnmatchcase(name, pattern) for pattern in protocols)]
    if not names:
        raise ValueError("没有匹配的协议")
    generator, _ = _load_scene_task(scene)
    config = {"scene": scene, "task_profile": profile, "security_mode": mode,
              "seed": _coerce_int(payload, "seed", 0, 0, 2**31 - 501),
              "num_tags": num_tags, "total_runs": total_runs,
              "price_min": price_min, "price_max": price_max,
              "relative_tolerance": bounded_float(payload, "relative_tolerance", .15, 0., 1.),
              "absolute_tolerance": bounded_float(payload, "absolute_tolerance", 2., 0., 1e12),
              "timeout_seconds": min(timeout, 30)}
    started = time.perf_counter()
    evaluations = {}
    for name in names:
        check_cancelled(progress_logger)
        if progress_logger:
            progress_logger(log=f"统一评价 {name}", stage="evaluation", progress=int(len(evaluations) / len(names) * 90))
        if time.perf_counter() - started >= timeout:
            raise TimeoutError("批量评价已达到总时间预算")
        remaining = timeout - (time.perf_counter() - started)
        evaluations[name] = evaluate_protocol(_find_protocol_file(scene, name), name, generator,
            {**config, "timeout_seconds": min(config["timeout_seconds"], remaining / total_runs),
             "cancel_check": lambda: check_cancelled(progress_logger)})
    folder = _scene_path(scene) / "outputs" / "candidate_comparisons" / uuid4().hex
    identities = {tuple(item["summary"]["dataset_hashes"]) for item in evaluations.values()
                  if item["summary"]["selection_eligible"]}
    comparable = len(identities) <= 1
    if not comparable:
        for item in evaluations.values():
            item["summary"]["selection_eligible"] = False
            item["summary"]["exclusion_reasons"] = [*item["summary"]["exclusion_reasons"], "candidate_datasets_differ"]
    save_json(folder / "comparison.json", {"scope": "python_quality_comparison", "config": config,
              "datasets_comparable": comparable, "evaluations": evaluations})
    rows = [{"name": name, **item["summary"],
             "pass_rate": item["validation"]["semantic_pass_rate"],
             "avg_time": item["validation"]["avg_time_ms"],
             "timing_scope": "candidate_self_reported"} for name, item in evaluations.items()]
    rows.sort(key=lambda row: (not row["selection_eligible"], -(row["quality_pass_rate"] or 0), -(row["quality_score"] or 0)))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank if row["selection_eligible"] else None
    wall_clock_s = round(time.perf_counter() - started, 3)
    return {
        "scene": scene,
        "protocols": protocols,
        "scope": "python_quality_comparison",
        "datasets_comparable": comparable,
        "config": config,
        "evidence_path": _relative_to_project(folder / "comparison.json"),
        "returncode": 0,
        "stdout": "统一验证已完成；自报时间不参与排名。",
        "stderr": "",
        "wall_clock_s": wall_clock_s,
        "results": rows,
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
    started = time.perf_counter()
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
    custom_task_text = str(payload.get("custom_task_text") or "").strip()
    validate_task_profile(task_profile, custom_task_text)
    prefix = _default_iteration_prefix_for_task(payload.get("prefix") or "web_auto_iter", task_profile)
    iterations = _coerce_int(payload, "iterations", 3, 1, 12)
    dry_run = boolean_value(payload, "dry_run", True)
    if not dry_run and any(f"{prefix}_{i + 1:02d}" in available for i in range(iterations)):
        raise ValueError("该前缀已有协议文件，请更换协议前缀，避免覆盖历史候选")
    llm_config = None if dry_run else _web_llm_config(payload)
    max_repair_rounds = _coerce_int(payload, "max_repair_rounds", 0 if dry_run else 1, 0, 5)
    validate_num_tags = _coerce_int(payload, "validate_num_tags", 80, 20, 5000)
    retry_attempts = _coerce_int(payload, "retry_attempts", 3, 1, 6)
    target_generation = str(payload.get("target_generation") or "next")
    requirement_text = str(payload.get("requirement_text") or "")

    selection_enabled = boolean_value(payload, "quality_selection", True) and task_profile != CUSTOM_TASK_PROFILE
    objective = str(payload.get("selection_objective") or "quality_only")
    if objective not in OBJECTIVES:
        raise ValueError("不支持的选择目标")
    minimum_pass_rate = bounded_float(payload, "minimum_pass_rate", 1., 0., 1.)
    patience = _coerce_int(payload, "no_improvement_rounds", 0, 0, 12)
    target_cost = bounded_float(payload, "target_communication_cost_ms", 0., 0., 1e12)
    control = OptimizationControl(_coerce_int(payload, "max_model_requests", 30, 1, 500),
                                  _coerce_int(payload, "max_wall_seconds", 1800, 1, 86400),
                                  getattr(progress_logger, "check_cancelled", None))
    independent_enabled = boolean_value(payload, "independent_validation", True)
    stop_reason, stagnant = "iterations_completed", 0
    holdout_result = None
    evaluation_config = {"scene": scene, "task_profile": task_profile, "security_mode": "strict",
        "seed": _coerce_int(payload, "evaluation_seed", 1000, 0, 2**31 - 21),
        "total_runs": _coerce_int(payload, "evaluation_runs", 3, 1, 20),
        "num_tags": validate_num_tags,
        "price_min": _coerce_int(payload, "evaluation_price_min", 1, 1, 100000),
        "relative_tolerance": bounded_float(payload, "relative_tolerance", .15, 0., 1.),
        "absolute_tolerance": bounded_float(payload, "absolute_tolerance", 2., 0., 1e12),
        "timeout_seconds": _coerce_int(payload, "evaluation_timeout", 10, 1, 60)}
    evaluation_config["price_max"] = _coerce_int(payload, "evaluation_price_max", 1000, evaluation_config["price_min"] + 1, 1000000)
    if selection_enabled and objective != "quality_only" and evaluation_config["price_max"] - evaluation_config["price_min"] + 1 > 10000:
        raise ValueError("当前匿名信道支持最多 10000 个属性桶，请缩小评价范围")
    population = list(dict.fromkeys(parents))
    evaluations = {}
    evidence_dir = None
    run_record = None
    if not dry_run and selection_enabled:
        generator, _ = _load_scene_task(scene)
        task_source = (_scene_path(scene) / "Task.py").read_bytes()
        evidence_dir = _scene_path(scene) / "outputs" / "optimization_runs" / uuid4().hex
        evidence_dir.mkdir(parents=True)
        run_record = {"schema_version": "1.0", "status": "running", "scene": scene,
            "task_profile": task_profile, "selection_objective": objective,
            "minimum_pass_rate": minimum_pass_rate, "no_improvement_rounds": patience,
            "target_communication_cost_ms": target_cost,
            "evaluation_split": "development", "evaluation_config": evaluation_config,
            "initial_population": list(population), "rounds": [], "artifact_hashes": {},
            "cost_note": "Only worker-owned anonymous channel events can compete on simulated communication cost; not hardware RF time."}
        (evidence_dir / "Task.source.txt").write_bytes(task_source)
        run_record["artifact_hashes"]["Task.source.txt"] = hashlib.sha256(task_source).hexdigest()
        save_json(evidence_dir / "run.json", run_record)

        def evaluate_name(name):
            if (_scene_path(scene) / "Task.py").read_bytes() != task_source:
                run_record.update(status="failed", error_type="GeneratorChanged")
                save_json(evidence_dir / "run.json", run_record)
                raise ValueError("评价期间 Task.py 发生变化，请使用固定场景重新运行")
            path = _find_protocol_file(scene, name)
            current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            if name in evaluations and evaluations[name]["summary"]["source_sha256"] != current_hash:
                run_record.update(status="failed", error_type="SourceChanged")
                save_json(evidence_dir / "run.json", run_record)
                raise ValueError(f"评价期间协议源码发生变化: {name}")
            if name not in evaluations:
                if progress_logger:
                    progress_logger(f"固定开发集评价：{name}", stage="evaluation")
                try:
                    evaluations[name] = evaluate_protocol(path, name, generator, evaluation_config,
                        deadline=control.started + control.max_seconds)
                except Exception as exc:
                    run_record.update(status="failed", error_type=type(exc).__name__)
                    save_json(evidence_dir / "run.json", run_record)
                    raise
                run_record["artifact_hashes"][f"{name}.evaluation.json"] = save_json(evidence_dir / f"{name}.evaluation.json", evaluations[name])
                snapshot_path = evidence_dir / f"{name}.source.txt"
                snapshot_path.write_bytes(path.read_bytes())
                snapshot_hash = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
                if snapshot_hash != evaluations[name]["summary"]["source_sha256"]:
                    run_record.update(status="failed", error_type="SourceChanged")
                    save_json(evidence_dir / "run.json", run_record)
                    raise ValueError(f"评价期间协议源码发生变化: {name}")
                run_record["artifact_hashes"][snapshot_path.name] = snapshot_hash
                save_json(evidence_dir / "run.json", run_record)
            return evaluations[name]

        try:
            for name in population:
                evaluate_name(name)
        except Exception as exc:
            run_record.update(status="failed", error_type=type(exc).__name__)
            save_json(evidence_dir / "run.json", run_record)
            raise

        initial_selection = selection_report(population, evaluations, len(parents), objective=objective, minimum_pass_rate=minimum_pass_rate)
        population = initial_selection["population"]
        run_record["initial_selection"] = initial_selection
        declared_baseline = payload.get("baseline_protocol")
        if declared_baseline and declared_baseline not in parents:
            raise ValueError("指定基线必须来自初始父代")
        baseline_name = declared_baseline or select_population(parents, evaluations, 1, objective=objective, minimum_pass_rate=minimum_pass_rate)[0]

    def best_score():
        if not evaluations:
            return None
        ranked = select_population(list(population), evaluations, 1, objective=objective, minimum_pass_rate=minimum_pass_rate)
        row = evaluations[ranked[0]]["summary"]
        if objective != "quality_only" and row.get("communication_eligible") and row["quality_pass_rate"] >= minimum_pass_rate:
            return (1, -row["mean_communication_cost_ms"], row["quality_score"])
        return (0, row["quality_pass_rate"] or 0., row["quality_score"] or 0.)

    pairs = []
    for i, parent_a in enumerate(parents):
        for parent_b in parents[i + 1 :]:
            pairs.append((parent_a, parent_b))

    def invoke_workflow(**kwargs):
        try:
            return run_ai_optimization_workflow(**kwargs)
        except OptimizationStopped:
            raise
        except Exception as exc:
            if run_record is not None:
                run_record.update(status="failed", error_type=type(exc).__name__)
                save_json(evidence_dir / "run.json", run_record)
            raise

    results: list[dict[str, Any]] = []
    if progress_logger:
        progress_logger(
            f"自动迭代已准备 {len(pairs)} 个父代组合，将执行 {iterations} 轮；模式：{'预览' if dry_run else '真实生成'}。",
            progress=5,
            stage="planning",
        )
    for index in range(iterations):
        try:
            control.check()
        except OptimizationStopped as exc:
            stop_reason = exc.reason
            break
        previous_score = best_score()
        previous_front = pareto_signature(population, evaluations, minimum_pass_rate) if evaluations and objective == "pareto_diversity" else None
        if not dry_run and target_cost > 0 and objective != "quality_only" and previous_score is not None and previous_score[0] == 1 and -previous_score[1] <= target_cost:
            stop_reason = "target_communication_cost"
            break
        if not dry_run and selection_enabled:
            for name in population:
                evaluate_name(name)
            pairs = [(a, b) for i, a in enumerate(population) for b in population[i + 1:]]
            if not pairs and population:
                pairs = [(population[0], population[0])]
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

        workflow_args = dict(
            scene_name=scene,
            parent_a=parent_a,
            parent_b=parent_b,
            protocol_name=protocol_name,
            target_generation=target_generation,
            llm_config=llm_config,
            temperature=bounded_float(payload, "temperature", 0.2, 0.0, 2.0),
            max_tokens=_coerce_int(payload, "max_tokens", 2800, 256, 16000),
            timeout_seconds=_coerce_int(payload, "timeout_seconds", 120, 20, 1800),
            validate_num_tags=validate_num_tags,
            max_repair_rounds=max_repair_rounds,
            dry_run=dry_run,
            retry_attempts=retry_attempts,
            task_profile=task_profile,
            requirement_text=requirement_text + ("\n" + feedback_for(population, evaluations) if not dry_run and selection_enabled else ""),
            custom_task_text=custom_task_text,
            logger=iter_log,
        )
        if not dry_run:
            workflow_args["request_guard"] = control.before_request
            workflow_args["evaluation_deadline"] = control.started + control.max_seconds
        if not dry_run and selection_enabled and objective != "quality_only":
            workflow_args["requirement_text"] += (
                "\n通信优化必须实现 execute_channel(self, channel, price_min, price_max)，返回 estimated_values 列表。"
                "channel.observe(round_index=0, slots_per_value=256) 返回 counts、variance 列表及 saturated_bins。"
                "列表索引对应 price_min+i。只处理匿名聚合观测，不接收 tags，不访问信道内部状态。"
                "该接口仅允许导入 math、from base import IProtocol、__future__；使用内置列表与循环，不导入 numpy。"
                "每次 observe（包括重复调用）计费，最多128次，slots_per_value 为2..32768。"
                "通信模型 histogram_or_v1_slot001：reader_bit_ms=.025、tag_bit_ms=.4、slot_ms=.001、frame_ms=30。"
                "execute 方法可作为兼容入口，但平台优先调用 execute_channel；在精度达标前先修复精度。")
            workflow_args["requirement_text"] += (
                f"最低语义通过率={minimum_pass_rate}，相对误差门槛={evaluation_config['relative_tolerance']}，"
                f"绝对误差门槛={evaluation_config['absolute_tolerance']}；达标后再最小化通信成本。")
        try:
            result = invoke_workflow(**workflow_args)
        except OptimizationStopped as exc:
            stop_reason = exc.reason
            if run_record is not None:
                run_record["stopped_attempt"] = {"protocol_name": protocol_name, "parent_a": parent_a,
                    "parent_b": parent_b, "reason": exc.reason, "used_for_selection": False}
            break
        result["task_profile"] = task_profile
        result["task_title"] = task_guidance(task_profile, custom_task_text)["title"]
        request = {
            "scene": scene,
            "parent_a": parent_a,
            "parent_b": parent_b,
            "protocol_name": protocol_name,
            "target_generation": target_generation,
            "dry_run": dry_run,
            "task_profile": task_profile,
            "custom_task_text": custom_task_text if task_profile == CUSTOM_TASK_PROFILE else "",
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
        if not dry_run and selection_enabled:
            before = list(population)
            for name in population:
                evaluate_name(name)
            target_exists = result.get("target_file") and Path(result["target_file"]).is_file()
            if target_exists:
                evaluate_name(protocol_name)
                combined = [*population, protocol_name]
                try:
                    details = selection_report(combined, evaluations, len(parents), objective=objective, minimum_pass_rate=minimum_pass_rate, incumbents=before)
                    population = details["population"]
                except ValueError:
                    run_record.update(status="failed", error_type="IncomparableCandidates")
                    save_json(evidence_dir / "run.json", run_record)
                    raise
                item["evaluation"] = evaluations[protocol_name]["summary"]
                item["selection_details"] = details
            item["selection"] = {"objective": objective, "population_before": before,
                "population_after": list(population), "retained": protocol_name in population,
                "reason": next((row["reason"] for row in details["decisions"] if row["name"] == protocol_name), "not_selected") if target_exists else "candidate_file_unavailable"}
            run_record["rounds"].append({key: item.get(key) for key in ("iteration", "parent_a", "parent_b", "protocol_name", "status", "evaluation", "selection", "selection_details")})
            run_record["population"] = list(population)
            run_record["budget"] = control.snapshot()
            save_json(evidence_dir / "run.json", run_record)
        results.append(item)
        if progress_logger:
            progress_logger(
                f"第 {index + 1}/{iterations} 轮完成：{protocol_name} -> {result.get('status')}",
                progress=8 + int((index + 1) / max(1, iterations) * 88),
                stage=f"iteration_{index + 1}_done",
            )
        if not dry_run and result.get("status") == "success":
            available.add(protocol_name)
        current_score = best_score()
        if not dry_run and target_cost > 0 and objective != "quality_only" and current_score is not None and current_score[0] == 1 and -current_score[1] <= target_cost:
            stop_reason = "target_communication_cost"
            break
        improved = previous_score is not None and current_score > previous_score
        if previous_front is not None:
            improved = improved or pareto_signature(population, evaluations, minimum_pass_rate) != previous_front
        stagnant = 0 if improved else stagnant + 1
        if patience and selection_enabled and not dry_run and stagnant >= patience:
            stop_reason = "no_improvement"
            break
    if run_record is not None:
        # Freeze a winner even when the optional immediate holdout is disabled.
        winner = select_population(population, evaluations, 1, objective=objective, minimum_pass_rate=minimum_pass_rate)[0]
        run_record.update(winner=winner, baseline=baseline_name,
            final_selection=selection_report(population, evaluations, len(parents), objective=objective, minimum_pass_rate=minimum_pass_rate, incumbents=population))
        if independent_enabled:
            try:
                control.check()
                # Search is over. Freeze selection before exposing held-out results.
                winner = select_population(population, evaluations, 1, objective=objective, minimum_pass_rate=minimum_pass_rate)[0]
                run_record.update(status="validating_holdout", winner=winner, baseline=baseline_name, stop_reason=stop_reason)
                save_json(evidence_dir / "run.json", run_record)
                if progress_logger:
                    progress_logger("搜索结束，正在执行冻结候选的独立验证。", stage="holdout")
                holdout_result = independent_validation(evidence_dir, winner, baseline_name, evaluations, evaluation_config,
                    deadline=control.started + control.max_seconds)
                holdout_result["evidence_path"] = _relative_to_project(evidence_dir / "holdout" / "comparison.json")
                for path in (evidence_dir / "holdout").iterdir():
                    if path.is_file():
                        run_record["artifact_hashes"]["holdout/" + path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OptimizationStopped as exc:
                holdout_result = {"status": "skipped_" + exc.reason, "independent": False}
            except Exception as exc:
                holdout_result = {"status": "failed", "independent": False, "error_type": type(exc).__name__}
        else:
            holdout_result = {"status": "disabled", "independent": False}
        run_record.update(status="cancelled" if stop_reason == "cancelled" else "success", population=list(population),
            stop_reason=stop_reason, budget=control.snapshot(), independent_validation=holdout_result,
            unranked_parents=[name for name in population if not evaluations[name]["summary"]["selection_eligible"]])
        save_json(evidence_dir / "run.json", run_record)
    registered_winner = registered_baseline = None
    registered_ids = []
    if run_record is not None and run_record.get("winner"):
        from course_project.research_service import register_generated
        # Use the evaluated source snapshots, never a later same-name edit.
        for name in evaluations:
            original = _find_protocol_file(scene, name)
            frozen_bytes = (evidence_dir / (name + ".source.txt")).read_bytes()
            if original.read_bytes() != frozen_bytes:
                continue
            registered = register_generated(original, scene, task_profile, evidence=_relative_to_project(evidence_dir / "run.json"))
            registered_ids.append(registered["id"])
            if name == run_record["winner"]: registered_winner = registered["id"]
            if name == baseline_name: registered_baseline = registered["id"]
    return {
        "scene": scene,
        "parents": parents,
        "winner_candidate_id": registered_winner, "baseline_candidate_id": registered_baseline,
        "candidate_ids": registered_ids,
        "initial_selection": run_record.get("initial_selection") if run_record else None,
        "final_selection": run_record.get("final_selection") if run_record else None,
        "prefix": prefix,
        "dry_run": dry_run,
        "task_profile": task_profile,
        "custom_task_text": custom_task_text if task_profile == CUSTOM_TASK_PROFILE else "",
        "iterations": results,
        "selection_mode": objective if selection_enabled else "generation_only",
        "stop_reason": stop_reason, "budget": control.snapshot(), "independent_validation": holdout_result,
        "population": population,
        "unranked_parents": run_record.get("unranked_parents", []) if run_record else [],
        "evaluation_config": evaluation_config if selection_enabled else None,
        "evidence_path": _relative_to_project(evidence_dir / "run.json") if evidence_dir else None,
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
    custom_task_text: str = "",
    num_tags: int,
    total_runs: int = 2,
    protocol_file: Path | None = None,
    progress_logger=None,
    deadline=None,
) -> dict[str, Any]:
    generator, _ = _load_scene_task(scene)
    item = evaluate_protocol(protocol_file or _find_protocol_file(scene, protocol), protocol, generator,
        {"scene": scene, "task_profile": task_profile, "security_mode": "strict", "seed": 1000,
         "num_tags": num_tags, "total_runs": total_runs, "relative_tolerance": .2,
         "cancel_check": lambda: check_cancelled(progress_logger)}, deadline=deadline)
    result = item["validation"]
    return {
        "accuracy": item["summary"]["quality_pass_rate"],
        "time": result["avg_measured_wall_time_ms"] if result["completed_runs"] else None,
        "timing_scope": "worker_measured_wall_excluding_startup_not_RF",
        "quality_score": item["summary"]["quality_score"],
        "communication_cost_ms": item["summary"]["mean_communication_cost_ms"],
        "source_sha256": result["source_sha256"],
        "final_decision": result["final_decision"],
        "validator_passed": bool(result.get("validator_passed")),
        "information_isolation_passed": bool(result.get("information_isolation_passed")),
    }


def _scene_evolution_data_path(scene: str, payload: dict[str, Any], progress_logger=None, deadline=None) -> tuple[Path, str]:
    scene_name = _validate_scene(scene)
    task_profile = str(payload.get("task_profile") or "mode_query")
    custom_task_text = str(payload.get("custom_task_text") or "").strip()
    validate_task_profile(task_profile, custom_task_text)
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

    generations: list[dict[str, Any]] = []
    raw_generation_rows: list[list[dict[str, Any]]] = []
    for generation_dir in generation_dirs:
        gen_index = _generation_sort_key(generation_dir)[0]
        protocol_files = sorted([p for p in generation_dir.glob("*.py") if p.stem != "__init__"], key=lambda p: p.stem)
        rows: list[dict[str, Any]] = []
        for file_path in protocol_files:
            check_cancelled(progress_logger)
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("experiment_preparation_time_budget")
            try:
                metrics = _protocol_metrics_for_scene(
                    scene_name,
                    file_path.stem,
                    task_profile=task_profile,
                    custom_task_text=custom_task_text,
                    num_tags=num_tags,
                    total_runs=total_runs,
                    protocol_file=file_path,
                    progress_logger=progress_logger, deadline=deadline,
                )
            except (JobCancelled, TimeoutError):
                raise
            except Exception as exc:
                rows.append({"name": file_path.stem, "generation": gen_index,
                    "parent_A": None, "parent_B": None, "correction_count": None,
                    "accuracy": None, "time": None, "quality_score": None,
                    "provenance_status": "unavailable", "error_type": type(exc).__name__})
                continue
            if progress_logger:
                progress_logger(stage="preparing", log=f"已评测 {generation_dir.name}/{file_path.stem}")
            provenance = {}
            record_path = file_path.with_suffix(".candidate.json")
            if record_path.is_file():
                try:
                    candidate = json.loads(record_path.read_text(encoding="utf-8"))
                    if candidate.get("source_hash") == metrics["source_sha256"]:
                        provenance = candidate
                except (OSError, ValueError):
                    pass
            actual_parents = provenance.get("parents", [])
            attempt = (provenance.get("validation_result") or {}).get("attempt")
            rows.append(
                {
                    "name": file_path.stem,
                    "generation": gen_index,
                    "parent_A": actual_parents[0] if actual_parents else None,
                    "parent_B": actual_parents[1] if len(actual_parents) > 1 else None,
                    "correction_count": attempt - 1 if type(attempt) is int and attempt >= 1 else None,
                    "provenance_status": "source_hash_verified" if provenance else "unavailable",
                    **metrics,
                }
            )
        raw_generation_rows.append((gen_index, rows))

    for gen_index, rows in raw_generation_rows:
        individuals = []
        for row in rows:
            row["fitness"] = row.get("quality_score")
            individuals.append(row)
        individuals.sort(key=lambda item: (item.get("accuracy") or 0., item.get("fitness") or 0.), reverse=True)
        generations.append({"generation": gen_index, "individuals": individuals})

    payload_data = {
        scene_name: {
            "source": f"{scene_name}/generation*",
            "schema_version": "2.0", "timing_scope": "worker_measured_wall_excluding_startup_not_RF",
            "provenance_policy": "candidate_record_with_matching_source_hash_only",
            "task_profile": task_profile,
            "num_tags": num_tags,
            "total_runs": total_runs,
            "base_algorithms": [],
            "generations": generations,
        }
    }
    data_path.write_text(json.dumps(payload_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data_path, scene_name


def _final_comparison_csv_path(scene: str, payload: dict[str, Any], progress_logger=None, deadline=None) -> Path:
    scene_name = _validate_scene(scene)
    task_profile = str(payload.get("task_profile") or "mode_query")
    custom_task_text = str(payload.get("custom_task_text") or "").strip()
    validate_task_profile(task_profile, custom_task_text)
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
        check_cancelled(progress_logger)
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("experiment_preparation_time_budget")
        seen.add(name)
        try:
            metrics = _protocol_metrics_for_scene(
                scene_name,
                name,
                task_profile=task_profile,
                custom_task_text=custom_task_text,
                num_tags=num_tags,
                total_runs=total_runs,
                progress_logger=progress_logger, deadline=deadline,
            )
        except (JobCancelled, TimeoutError):
            raise
        except Exception as exc:
            metrics = {"accuracy": None, "time": None, "timing_scope": "worker_measured_wall_excluding_startup_not_RF",
                "quality_score": None, "communication_cost_ms": None, "source_sha256": None,
                "final_decision": "failed:" + type(exc).__name__, "validator_passed": False,
                "information_isolation_passed": False}
        rows.append({"protocol_name": name, **metrics})
        if progress_logger:
            progress_logger(stage="preparing", log=f"已评测 {len(rows)}/{len(set(candidate_names))}: {name}")
    if not rows:
        raise ValueError(f"场景 {scene_name} 没有可执行协议，无法生成终稿图")
    import csv

    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(rows[0]),
        )
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


def _run_command(command: list[str], *, timeout: int = 600, env_overrides: dict[str, str] | None = None, progress_logger=None) -> dict[str, Any]:
    if progress_logger:
        from course_project.task_process import run_process
        return run_process(command, cwd=PROJECT_ROOT, timeout=timeout, update=progress_logger, env=env_overrides)
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


def run_experiment_task(payload: dict[str, Any], progress_logger=None) -> dict[str, Any]:
    check_cancelled(progress_logger)
    task = str(payload.get("task") or "robust").strip()
    task_profile = str(payload.get("task_profile") or "mode_query").strip()
    custom_task_text = str(payload.get("custom_task_text") or "").strip()
    validate_task_profile(task_profile, custom_task_text)
    timeout = _coerce_int(payload, "timeout_seconds", 600, 30, 7200)
    deadline = time.monotonic() + timeout
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
            "--task-profile", task_profile,
            "--security-mode", str(payload.get("security_mode") or "strict"),
            "--seed", str(_coerce_int(payload, "seed", 2000, 0, 2**31 - 1)),
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
        scenario = str(payload.get("scenario") or discover_scenes()[0])
        if scenario in discover_scenes():
            data_file, scenario = _scene_evolution_data_path(scenario, payload, **({"progress_logger": progress_logger, "deadline": deadline} if progress_logger else {}))
            data_path = _relative_to_project(data_file)
        else:
            if not payload.get("data"):
                raise ValueError("请选择项目场景，或明确提供含计量范围和来源信息的进化数据")
            data_path = _safe_experiment_path(str(payload["data"]), default_name="Experiments/evolution_data_combined.json")
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
        csv_path = _final_comparison_csv_path(source_scene, payload, **({"progress_logger": progress_logger, "deadline": deadline} if progress_logger else {}))
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

    check_cancelled(progress_logger)
    remaining = deadline - time.monotonic()
    if progress_logger and remaining <= 0:
        raise TimeoutError("experiment_time_budget")
    result = _run_command(command, timeout=remaining if progress_logger else timeout, env_overrides=env, **({"progress_logger": progress_logger} if progress_logger else {}))
    if progress_logger and result["returncode"]:
        raise APIError("实验脚本执行失败，请查看任务日志。", 500, code="experiment_failed", stage="experiment",
            suggestion="检查任务、协议与输入数据是否兼容，核对日志中的脚本错误。")
    return result | {
        "task": task,
        "task_profile": task_profile,
        "task_title": task_guidance(task_profile, custom_task_text)["title"],
        "custom_task_text": custom_task_text if task_profile == CUSTOM_TASK_PROFILE else "",
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


def file_response(handler: BaseHTTPRequestHandler, path: Path) -> None:
    _assert_safe_raw_artifact(path)
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    if path.suffix.lower() in {".html", ".txt", ".md", ".csv", ".json", ".log", ".svg"}:
        content_type += "; charset=utf-8"
    send_bytes(handler, path.read_bytes(), content_type, sandbox=path.suffix.lower() in {".html", ".svg"})


def _asset_path(relative: str) -> Path:
    path = _resolve_project_path(relative)
    try:
        _assert_safe_raw_artifact(path)
    except ValueError:
        _assert_editable_project_file(path, check_size=False)
    if not path.is_file():
        raise FileNotFoundError("找不到该资产文件")
    return path


class CourseRequestHandler(BaseHTTPRequestHandler):
    server_version = "RFIDCourseConsole/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[web] {self.address_string()} - {format % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if serve_frontend(self, path):
                return
            if path in {"/", "/legacy"}:
                text_response(self, ADVANCED_INDEX_HTML)
            elif path.startswith("/static/") and path.removeprefix("/static/") in STATIC_FILES:
                name = path.removeprefix("/static/")
                send_bytes(self, (STATIC_ROOT / name).read_bytes(), STATIC_FILES[name])
            elif path == "/api/health":
                json_response(self, {"ok": True, "project_root": str(PROJECT_ROOT)})
            elif path == "/api/selfopt/state":
                json_response(self, selfopt_state())
            elif path == "/api/workspaces":
                from course_project import workspace_service
                wid = parse_qs(parsed.query).get("id", [""])[0]
                json_response(self, {"workspace": workspace_service.detail(wid)} if wid else workspace_service.listing())
            elif path == "/api/workspaces/export":
                from course_project import workspace_service
                wid = parse_qs(parsed.query).get("id", [""])[0]
                data = workspace_service.detail(wid)
                send_bytes(self, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"),
                    "application/json; charset=utf-8", download_name=f"research-task-{wid}.json")
            elif path == "/api/research/capabilities":
                from course_project.research_service import capabilities
                json_response(self, capabilities())
            elif path == "/api/research/candidates":
                from course_project.research_service import list_candidates
                json_response(self, list_candidates(query))
            elif path == "/api/research/runs":
                from course_project.research_service import runs
                json_response(self, runs(query))
            elif path == "/api/selfopt/artifacts":
                json_response(self, {"artifacts": _experiment_artifact_rows(), "outputs": _output_artifact_rows()})
            elif path == "/api/statistics/benchmark":
                json_response(self, _statistical_benchmark_state())
            elif path == "/api/statistics/details":
                from course_project.statistics_service import details
                json_response(self, details(query))
            elif path == "/api/statistics/comparison":
                from course_project.statistics_service import comparison
                json_response(self, comparison(query))
            elif path == "/api/statistics/config-runs":
                from course_project.engineering_service import config_run
                json_response(self, config_run(query))
            elif path == "/api/statistics/pipelines":
                from course_project.engineering_service import pipeline_run
                json_response(self, pipeline_run(query))
            elif path == "/api/selfopt/candidates":
                from course_project.engineering_service import candidate_lineage
                json_response(self, candidate_lineage(query))
            elif path == "/api/selfopt/job":
                job_id = query.get("id", [""])[0]
                json_response(self, {"job": get_background_job(job_id)})
            elif path == "/api/selfopt/jobs":
                json_response(self, {"jobs": JOB_MANAGER.list()})
            elif path == "/api/selfopt/validation-reports":
                limit = _coerce_int({"limit": query.get("limit", ["20"])[0]}, "limit", 20, 1, 200)
                scene = query.get("scene", [""])[0].strip() or None
                protocol = query.get("protocol", [""])[0].strip() or None
                json_response(self, {"reports": list_validation_reports(limit, scene=scene, protocol=protocol)})
            elif path == "/api/selfopt/validation-report":
                report_id = _coerce_int({"id": query.get("id", [""])[0]}, "id", 0, 1, 2_147_483_647)
                report = get_validation_report(report_id)
                if report is None:
                    raise FileNotFoundError(f"找不到验证报告: {report_id}")
                json_response(self, report)
            elif path == "/api/selfopt/raw":
                rel_path = query.get("path", [""])[0]
                file_response(self, _resolve_project_path(rel_path))
            elif path == "/api/selfopt/preview":
                asset = _asset_path(query.get("path", [""])[0])
                page = _coerce_int({"page": query.get("page", ["1"])[0]}, "page", 1, 1, 1000000)
                json_response(self, preview_file(asset, _relative_to_project(asset), page))
            elif path == "/api/selfopt/download":
                asset = _asset_path(query.get("path", [""])[0])
                send_bytes(self, asset.read_bytes(), "application/octet-stream", download_name=asset.name)
            elif path == "/api/selfopt/file":
                rel_path = query.get("path", [""])[0]
                json_response(self, read_project_file(rel_path))
            elif path == "/api/database":
                json_response(self, database_stats())
            else:
                json_response(self, {"error": f"unknown path: {path}"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            _request_error(self, exc)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/selfopt/upload":
                result = import_file(self, PROJECT_ROOT, discover_scenes(), parse_qs(parsed.query))
                json_response(self, result, HTTPStatus.CREATED)
                return
            payload = read_json_body(self)
            if path == "/api/workspaces":
                from course_project.workspace_service import save
                json_response(self, {"workspace": save(payload)}, HTTPStatus.CREATED)
            elif path == "/api/workspaces/update":
                from course_project.workspace_service import save, identifier
                json_response(self, {"workspace": save(payload, workspace_id=identifier(payload.get("id")))})
            elif path == "/api/workspaces/attach":
                from course_project.workspace_service import attach
                json_response(self, {"workspace": attach(payload)})
            elif path == "/api/workspaces/run":
                from course_project.workspace_service import start
                json_response(self, start(JOB_MANAGER, payload), HTTPStatus.ACCEPTED)
            elif path == "/api/research/register":
                from course_project.research_service import register
                if not payload.get("path") and payload.get("family", "python") == "python":
                    scene = _validate_scene(str(payload.get("scene") or ""))
                    protocol = _validate_identifier(str(payload.get("protocol") or ""), "协议名称")
                    payload = {**payload, "path": str(_find_protocol_file(scene, protocol))}
                json_response(self, {"candidate": register(payload)}, HTTPStatus.CREATED)
            elif path == "/api/research/evaluate-async":
                from course_project.research_service import start_evaluation
                json_response(self, {"job": start_evaluation(JOB_MANAGER, payload)}, HTTPStatus.ACCEPTED)
            elif path == "/api/statistics/run-config":
                from course_project.engineering_service import start_config
                json_response(self, {"job": start_config(JOB_MANAGER, payload)}, HTTPStatus.ACCEPTED)
            elif path == "/api/statistics/run-pipeline":
                from course_project.engineering_service import start_pipeline
                json_response(self, {"job": start_pipeline(JOB_MANAGER, payload)}, HTTPStatus.ACCEPTED)
            elif path == "/api/statistics/rebuild":
                from course_project.engineering_service import start_rebuild
                json_response(self, {"job": start_rebuild(JOB_MANAGER, payload)}, HTTPStatus.ACCEPTED)
            elif path == "/api/statistics/run":
                from course_project.statistics_service import start_suite
                json_response(self, {"job": start_suite(JOB_MANAGER, payload)}, HTTPStatus.ACCEPTED)
            elif path == "/api/statistics/generate-baseline":
                from course_project.statistics_service import generate_baseline
                config = _web_llm_config(payload)
                def work(update):
                    check_cancelled(update)
                    result = generate_baseline(config)
                    if result.get("valid"):
                        from course_project.research_service import register
                        result["registry_id"] = register({"family": "statistics", "path": result["path"]})["id"]
                    return result
                json_response(self, {"job": JOB_MANAGER.submit("statistics_llm", payload, work)}, HTTPStatus.ACCEPTED)
            elif path == "/api/statistics/import-candidate":
                from course_project.statistics_service import save_candidate
                from course_project.research_service import register
                result = save_candidate(payload.get("record"))
                result["registry_id"] = register({"family": "statistics", "path": result["path"]})["id"]
                json_response(self, result, HTTPStatus.CREATED)
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
                json_response(self, {"job": start_background_task("ai_generation", payload)}, HTTPStatus.ACCEPTED)
            elif path == "/api/selfopt/ai-models":
                json_response(self, probe_ai_runtime(payload))
            elif path == "/api/selfopt/validate":
                json_response(self, validate_selfopt_protocol(payload))
            elif path == "/api/selfopt/validate-async":
                json_response(self, {"job": JOB_MANAGER.submit("protocol_validation", payload,
                    lambda update: validate_selfopt_protocol(payload, update))}, HTTPStatus.ACCEPTED)
            elif path == "/api/selfopt/baseline-copy":
                result = copy_baseline_to_scene(payload)
                json_response(self, result | {"state": selfopt_state()})
            elif path == "/api/selfopt/batch-test":
                json_response(self, run_selfopt_batch_test(payload))
            elif path == "/api/selfopt/batch-test-async":
                json_response(self, {"job": JOB_MANAGER.submit("candidate_comparison", payload,
                    lambda update: run_selfopt_batch_test(payload, update))}, HTTPStatus.ACCEPTED)
            elif path == "/api/selfopt/auto-iterate":
                json_response(self, run_selfopt_auto_iterate(payload))
            elif path == "/api/selfopt/auto-iterate-async":
                json_response(self, {"job": start_background_task("auto_iterate", payload)}, HTTPStatus.ACCEPTED)
            elif path == "/api/selfopt/job/cancel":
                json_response(self, {"job": JOB_MANAGER.cancel(str(payload.get("id") or ""))})
            elif path == "/api/selfopt/job/retry":
                json_response(self, {"job": retry_background_task(payload)}, HTTPStatus.ACCEPTED)
            elif path == "/api/selfopt/experiments":
                json_response(self, run_experiment_task(payload))
            elif path == "/api/selfopt/experiments-async":
                json_response(self, {"job": JOB_MANAGER.submit("scene_experiment", payload,
                    lambda update: run_experiment_task(payload, update))}, HTTPStatus.ACCEPTED)
            else:
                json_response(self, {"error": f"unknown path: {path}"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            _request_error(self, exc)


class CourseHTTPServer(ThreadingHTTPServer):
    """Small server policy wrapper used by the local console."""

    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the interactive RFID protocol self-optimization Web platform.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    init_database()
    from course_project.workspace_service import recover
    recover(JOB_MANAGER)
    server = CourseHTTPServer((args.host, args.port), CourseRequestHandler)
    print(f"RFID self-optimization platform: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down RFID self-optimization platform.")
    finally:
        server.server_close()
        JOB_MANAGER.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


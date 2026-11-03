"""Durable research workspaces, revisioned inputs and linked background runs."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
from uuid import uuid4

from course_project import research_service as research
from course_project.candidate_evaluation import save_json
from course_project.evolution_selection import OBJECTIVES
from course_project.http_support import APIError
from course_project.jobs import TERMINAL_STATES, _sanitize, _credentials
from course_project.validation import bounded_int, bounded_float, boolean_value

ROOT = Path(__file__).resolve().parents[1]


def root():
    return ROOT / "course_project/outputs/workspaces"


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


@contextmanager
def database():
    root().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root() / "workspaces.sqlite3", timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
            title TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            data_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workspace_runs(id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            revision INTEGER NOT NULL, action TEXT NOT NULL, created_at TEXT NOT NULL, job_id TEXT,
            status TEXT NOT NULL, snapshot_json TEXT NOT NULL, config_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workspace_candidates(workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            candidate_id TEXT NOT NULL, roles_json TEXT NOT NULL, source_run_id TEXT,
            PRIMARY KEY(workspace_id,candidate_id));
        CREATE TABLE IF NOT EXISTS workspace_revisions(workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            revision INTEGER NOT NULL, created_at TEXT NOT NULL, data_json TEXT NOT NULL,
            PRIMARY KEY(workspace_id,revision));
    """)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("研究任务 ID 无效")
    return value


def get(workspace_id, conn=None):
    identifier(workspace_id)
    if conn is None:
        with database() as db:
            return get(workspace_id, db)
    row = conn.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
    if row is None:
        raise FileNotFoundError("研究任务不存在")
    return {**json.loads(row["data_json"]), **{key: row[key] for key in ("id", "revision", "title", "status", "created_at", "updated_at")}}


def listing():
    with database() as db:
        rows = db.execute("SELECT id FROM workspaces ORDER BY updated_at DESC LIMIT 200").fetchall()
        return {"workspaces": [{key: item.get(key) for key in ("id", "revision", "title", "status", "scene", "task_profile", "updated_at")}
            for item in (get(row["id"], db) for row in rows)]}


def normalize(payload):
    title = payload.get("title", "")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
        raise ValueError("研究任务名称需为 1..120 个字符")
    description = payload.get("description", "")
    if not isinstance(description, str) or len(description) > 20000:
        raise ValueError("研究说明需为不超过 20000 字符的文本")
    scene, profile = payload.get("scene"), payload.get("task_profile")
    if not isinstance(scene, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", scene) or not (ROOT / scene / "Task.py").is_file():
        raise ValueError("请选择已有场景")
    if profile not in research.PYTHON_PROFILES:
        raise ValueError("工作区当前支持六种可执行 Python 任务；请选择有完整语义判定的任务")
    raw = payload.get("optimization", {})
    if not isinstance(raw, dict):
        raise ValueError("演化配置必须为对象")
    integers = {"iterations": (3, 1, 12), "validate_num_tags": (80, 20, 5000), "evaluation_runs": (3, 1, 20),
        "evaluation_seed": (1000, 0, 2**31-21), "evaluation_price_min": (1, 1, 100000),
        "evaluation_price_max": (64, 3, 1000000), "max_repair_rounds": (1, 0, 5),
        "max_model_requests": (30, 1, 500), "max_wall_seconds": (1800, 1, 86400), "no_improvement_rounds": (3, 0, 12)}
    floats = {"relative_tolerance": (.15, 0, 1), "absolute_tolerance": (2, 0, 1e12),
        "minimum_pass_rate": (1, 0, 1), "target_communication_cost_ms": (0, 0, 1e12)}
    allowed = set(integers) | set(floats) | {"parents", "baseline_protocol", "selection_objective", "requirement_text", "independent_validation"}
    if set(raw) - allowed:
        raise ValueError("演化配置包含不支持的字段")
    config = {key: bounded_int(raw, key, *bounds) for key, bounds in integers.items()}
    config.update({key: bounded_float(raw, key, *bounds) for key, bounds in floats.items()})
    if not 2 <= config["evaluation_price_max"] - config["evaluation_price_min"] <= 9999:
        raise ValueError("评价属性范围需要 3..10000 个桶")
    parents = raw.get("parents")
    if not isinstance(parents, list) or not 2 <= len(parents) <= 20 or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in parents) or len(set(parents)) != len(parents):
        raise ValueError("初始父代需为 2..20 个不同的协议名称")
    baseline = raw.get("baseline_protocol") or parents[0]
    if baseline not in parents:
        raise ValueError("预设基线必须属于初始父代")
    objective = raw.get("selection_objective", "pareto_diversity")
    if objective not in OBJECTIVES:
        raise ValueError("选择目标无效")
    requirement = raw.get("requirement_text", "")
    if not isinstance(requirement, str) or len(requirement) > 20000:
        raise ValueError("需求文本过长或类型无效")
    config.update(parents=parents, baseline_protocol=baseline, selection_objective=objective, requirement_text=requirement,
        independent_validation=boolean_value(raw, "independent_validation", True))
    model = payload.get("model_config", {})
    if not isinstance(model, dict):
        raise ValueError("模型元信息必须为对象")
    if set(model) - {"base_url", "model", "temperature", "max_tokens", "timeout_seconds", "retry_attempts"}:
        raise ValueError("研究任务只保存模型元信息，密钥应在运行时提交")
    for key in ("base_url", "model"):
        if key in model and (not isinstance(model[key], str) or len(model[key]) > 500):
            raise ValueError("模型 URL 或名称无效")
    if model.get("base_url"):
        url = urlsplit(model["base_url"])
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("模型地址应为不含凭据、查询参数的 HTTP(S) URL")
    clean_model = {key: model.get(key, "") for key in ("base_url", "model")}
    clean_model.update(temperature=bounded_float(model, "temperature", .2, 0, 2), max_tokens=bounded_int(model, "max_tokens", 2800, 256, 16000),
        timeout_seconds=bounded_int(model, "timeout_seconds", 120, 20, 1800), retry_attempts=bounded_int(model, "retry_attempts", 3, 1, 6))
    matrix = payload.get("evaluation", {})
    if not isinstance(matrix, dict) or set(matrix) - {"distributions", "sizes", "validation_seeds", "blind_seeds", "perturbations", "max_seconds"}:
        raise ValueError("跨场景评测配置字段无效")
    # Candidate-aware bounds and split checks run after initial parents are frozen.
    return {"title": title.strip(), "description": description, "scene": scene, "task_profile": profile,
        "optimization": config, "model_config": clean_model, "evaluation": matrix}


def link(db, wid, cid, role, run_id=None):
    record = research.candidate(cid)
    task = get(wid, db)
    if task["task_profile"] not in record["supported_profiles"] or record["family"] == "python" and record["scene"] != task["scene"]:
        raise ValueError("候选任务或场景与研究任务不一致")
    previous = db.execute("SELECT roles_json FROM workspace_candidates WHERE workspace_id=? AND candidate_id=?", (wid, cid)).fetchone()
    roles = set(json.loads(previous[0])) if previous else set()
    roles.add(role)
    db.execute("INSERT INTO workspace_candidates VALUES(?,?,?,?) ON CONFLICT(workspace_id,candidate_id) DO UPDATE SET roles_json=excluded.roles_json",
        (wid, cid, dump(sorted(roles)), run_id))


def save(payload, *, workspace_id=None):
    data = normalize(payload)
    wid = identifier(workspace_id) if workspace_id else uuid4().hex
    # Resolve current existing assets using the same lookup as the Web workflow.
    from course_project.web_app import _find_protocol_file
    parents = []
    for name in data["optimization"]["parents"]:
        path = _find_protocol_file(data["scene"], name)
        parents.append(research.register({"path": str(path), "scene": data["scene"], "task_profile": data["task_profile"]}))
    data["initial_candidate_ids"] = [item["id"] for item in parents]
    data["baseline_candidate_id"] = next(item["id"] for item in parents if item["name"] == data["optimization"]["baseline_protocol"])
    low, high = (data["optimization"][key] for key in ("evaluation_price_min", "evaluation_price_max"))
    research.normalize({**data["evaluation"], "candidate_ids": data["initial_candidate_ids"][:6], "baseline_id": data["initial_candidate_ids"][0],
        "task_profile": data["task_profile"], "price_min": low, "price_max": high})
    task_bytes = (ROOT / data["scene"] / "Task.py").read_bytes()
    data["task_source_sha256"] = hashlib.sha256(task_bytes).hexdigest()
    data["parent_sources"] = [{key: item[key] for key in ("id", "name", "source_path", "source_sha256")} for item in parents]
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = get(wid, db) if workspace_id else None
        if previous and payload.get("revision") != previous["revision"]:
            raise APIError("任务配置已被修改，请刷新后再保存。", 409, code="workspace_revision_conflict")
        if previous and (previous["scene"] != data["scene"] or previous["task_profile"] != data["task_profile"]):
            raise ValueError("已有研究任务的场景与目标任务固定；请为不同目标新建研究任务")
        if previous and db.execute("SELECT 1 FROM workspace_runs WHERE workspace_id=? AND status NOT IN ('success','failed','cancelled','interrupted')", (wid,)).fetchone():
            raise APIError("当前任务正在运行，请结束后再保存新的配置版本。", 409, code="workspace_busy")
        revision = previous["revision"] + 1 if previous else 1
        status = payload.get("status", previous["status"] if previous else "active")
        if status not in {"active", "archived"}:
            raise ValueError("任务状态必须为 active 或 archived")
        folder = root() / wid / ("revision_" + str(revision))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "Task.py").write_bytes(task_bytes)
        data["configuration_path"] = (folder / "configuration.json").relative_to(ROOT).as_posix()
        data["configuration_sha256"] = save_json(folder / "configuration.json", data)
        db.execute("INSERT INTO workspaces VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,title=excluded.title,status=excluded.status,updated_at=excluded.updated_at,data_json=excluded.data_json",
            (wid, revision, data["title"], status, previous["created_at"] if previous else now(), now(), dump(data)))
        db.execute("INSERT INTO workspace_revisions VALUES(?,?,?,?)", (wid, revision, now(), dump({**data, "status": status})))
        for item in parents:
            link(db, wid, item["id"], "initial_parent")
        link(db, wid, data["baseline_candidate_id"], "declared_baseline")
    return detail(wid)


def detail(wid):
    with database() as db:
        task = get(wid, db)
        task["revisions"] = [{"revision": row["revision"], "created_at": row["created_at"], "configuration": json.loads(row["data_json"])}
            for row in db.execute("SELECT * FROM workspace_revisions WHERE workspace_id=? ORDER BY revision DESC", (wid,))]
        runs = db.execute("SELECT * FROM workspace_runs WHERE workspace_id=? ORDER BY created_at DESC", (wid,)).fetchall()
        links = db.execute("SELECT * FROM workspace_candidates WHERE workspace_id=?", (wid,)).fetchall()
        task["runs"] = [{key: row[key] for key in ("id", "revision", "action", "created_at", "job_id", "status")} |
            {"job": json.loads(row["snapshot_json"]), "config": json.loads(row["config_json"])} for row in runs]
        task["candidates"], task["candidate_problems"] = [], []
        for row in links:
            try:
                record = research.candidate(row["candidate_id"])
                task["candidates"].append({**record, "roles": json.loads(row["roles_json"]), "source_run_id": row["source_run_id"]})
            except (ValueError, OSError, APIError):
                task["candidate_problems"].append(row["candidate_id"])
        return task


def attach(payload):
    wid = identifier(payload.get("id"))
    ids = payload.get("candidate_ids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 20 or any(not isinstance(cid, str) for cid in ids):
        raise ValueError("请选择 1..20 个候选版本")
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        task = get(wid, db)
        if payload.get("revision") != task["revision"]:
            raise APIError("任务配置版本已变化，请刷新。", 409)
        for cid in ids:
            link(db, wid, cid, "attached")
    return detail(wid)


def verify_inputs(task):
    config_path = ROOT / task["configuration_path"]
    if not config_path.is_file() or hashlib.sha256(config_path.read_bytes()).hexdigest() != task["configuration_sha256"]:
        raise APIError("保存的任务配置快照已变化，请保存新版本。", 409, code="workspace_source_changed", stage="freeze")
    path = ROOT / task["scene"] / "Task.py"
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != task["task_source_sha256"]:
        raise APIError("场景生成器已变化，请保存新的任务配置版本后运行。", 409, code="workspace_source_changed", stage="freeze")
    for item in task["parent_sources"]:
        research.candidate(item["id"])
        source = ROOT / item["source_path"]
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != item["source_sha256"]:
            raise APIError("初始父代源码已变化，请保存新的任务配置版本后运行。", 409, code="workspace_source_changed", stage="freeze")


def save_snapshot(run_id, job):
    snapshot = deepcopy(job)
    if isinstance(snapshot.get("result"), dict):
        snapshot["result"].pop("state", None)
    # JobManager sends an already sanitized snapshot, including echoed credentials.
    with database() as db:
        row = db.execute("SELECT workspace_id FROM workspace_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return
        db.execute("UPDATE workspace_runs SET job_id=?,status=?,snapshot_json=? WHERE id=?",
            (snapshot["id"], snapshot["status"], dump(snapshot), run_id))
        result = snapshot.get("result") or {}
        ids = result.get("candidate_ids", []) if snapshot["status"] in TERMINAL_STATES else []
        for key in ("candidate_id", "winner_candidate_id", "baseline_candidate_id"):
            if result.get(key) and snapshot["status"] in TERMINAL_STATES:
                ids = [*ids, result[key]]
        for cid in dict.fromkeys(ids):
            role = "winner" if cid == result.get("winner_candidate_id") else "run_baseline" if cid == result.get("baseline_candidate_id") else "evaluated"
            link(db, row["workspace_id"], cid, role, run_id)
        db.execute("UPDATE workspaces SET updated_at=? WHERE id=?", (now(), row["workspace_id"]))


class TrackingManager:
    def __init__(self, manager, run_id, task, *, verify=False):
        self.manager, self.run_id, self.task, self.verify = manager, run_id, task, verify

    def submit(self, kind, payload, work, **options):
        original = options.pop("on_update", None)
        def observe(job):
            save_snapshot(self.run_id, job)
            if original:
                original(job)
        def guarded(update):
            if self.verify:
                verify_inputs(self.task)
            return work(update)
        return self.manager.submit(kind, {**payload, "workspace_id": self.task["id"], "workspace_run_id": self.run_id}, guarded,
            on_update=observe, **options)


def start(manager, payload):
    wid = identifier(payload.get("id"))
    task = detail(wid)
    if payload.get("revision") != task["revision"]:
        raise APIError("配置版本已变化，请刷新研究任务。", 409, code="workspace_revision_conflict")
    if task["status"] != "active":
        raise APIError("任务已归档，请先恢复为活动状态。", 409)
    action = payload.get("action")
    if action not in {"optimization", "validation", "evaluation"}:
        raise ValueError("不支持的研究操作")
    preview = boolean_value(payload, "preview", False)
    run_id = uuid4().hex
    opt = task["optimization"]
    params = {**task["model_config"], **opt, "scene": task["scene"], "task_profile": task["task_profile"],
        "prefix": "ws_" + wid[:8] + "_" + run_id[:8], "dry_run": preview}
    runtime = payload.get("model_config", {})
    if not isinstance(runtime, dict) or set(runtime) - {"base_url", "model", "temperature", "max_tokens", "timeout_seconds", "retry_attempts", "api_key_value"}:
        raise ValueError("运行模型配置字段无效")
    if not preview and action == "optimization":
        for key in ("base_url", "model", "temperature", "max_tokens", "timeout_seconds", "retry_attempts"):
            if key in runtime and runtime[key] != task["model_config"][key]:
                raise APIError("当前模型设置与保存配置不同，请先保存新的任务版本。", 409, code="workspace_model_changed")
        params["api_key_value"] = runtime.get("api_key_value", "")
        from course_project.web_app import _web_llm_config
        _web_llm_config(params)
    verify_inputs(task)
    linked = {row["id"]: row for row in task["candidates"]}
    # A previous revision's winner is never silently reused after a configuration edit.
    previous = next((row["job"].get("result") or {} for row in task["runs"] if row["revision"] == task["revision"] and row["action"] == "optimization" and row["status"] == "success" and (row["job"].get("result") or {}).get("winner_candidate_id")), {})
    winner = previous.get("winner_candidate_id") or task["baseline_candidate_id"]
    if action == "validation":
        cid = payload.get("candidate_id") or winner
        if cid not in linked or linked[cid]["family"] != "python":
            raise ValueError("请选择本研究任务中的 Python 候选版本")
        params = {"candidate_id": cid, "protocol": linked[cid]["name"], "scene": task["scene"], "task_profile": task["task_profile"], "security_mode": "strict",
            "seed": opt["evaluation_seed"] + 50000, "total_runs": opt["evaluation_runs"], "num_tags": opt["validate_num_tags"],
            "price_min": opt["evaluation_price_min"], "price_max": opt["evaluation_price_max"],
            "relative_tolerance": opt["relative_tolerance"], "absolute_tolerance": opt["absolute_tolerance"]}
    elif action == "evaluation":
        ids = payload.get("candidate_ids") or list(dict.fromkeys((winner, previous.get("baseline_candidate_id") or task["baseline_candidate_id"])))
        if not isinstance(ids, list) or any(not isinstance(cid, str) or cid not in linked for cid in ids):
            raise ValueError("评测候选必须来自本研究任务")
        if ids and all(linked[cid]["family"] == "python" for cid in ids):
            ids = list(dict.fromkeys([*ids, task["baseline_candidate_id"]]))
        params = {**task["evaluation"], "candidate_ids": ids, "baseline_id": task["baseline_candidate_id"] if task["baseline_candidate_id"] in ids else ids[-1],
            "task_profile": task["task_profile"], "price_min": opt["evaluation_price_min"], "price_max": opt["evaluation_price_max"],
            "minimum_pass_rate": opt["minimum_pass_rate"], "relative_tolerance": opt["relative_tolerance"], "absolute_tolerance": opt["absolute_tolerance"]}
        research.normalize(params)
    clean_params = _sanitize(params, tuple(_credentials(params)))
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        current = get(wid, db)
        if current["revision"] != task["revision"] or current["status"] != "active":
            raise APIError("任务配置发生变化，请刷新。", 409)
        if db.execute("SELECT 1 FROM workspace_runs WHERE workspace_id=? AND status NOT IN ('success','failed','cancelled','interrupted')", (wid,)).fetchone():
            raise APIError("该研究任务已有运行中的操作，请等待或停止当前任务。", 409, code="workspace_busy")
        db.execute("INSERT INTO workspace_runs VALUES(?,?,?,?,?,?,?,?,?)", (run_id, wid, task["revision"], action, now(), None, "queued", "{}", dump(clean_params)))
    tracker = TrackingManager(manager, run_id, task, verify=action != "evaluation")
    try:
        if action == "evaluation":
            job = research.start_evaluation(tracker, params)
        else:
            from course_project import web_app
            from course_project.optimization_control import OptimizationStopped
            from course_project.jobs import JobCancelled
            def work(update):
                if action == "validation":
                    return web_app.validate_selfopt_protocol(params, update)
                def logger(message, progress=None, stage=None):
                    update(log=message, progress=progress, stage=stage)
                logger.check_cancelled = update.check_cancelled
                try:
                    return web_app.run_selfopt_auto_iterate(params, logger)
                except OptimizationStopped as exc:
                    if exc.reason == "cancelled":
                        raise JobCancelled("cancelled_by_user") from None
                    raise TimeoutError("workspace_optimization_budget") from None
            job = tracker.submit("auto_iterate" if action == "optimization" else "protocol_validation", params, work)
    except Exception as exc:
        from course_project.http_support import error_details
        with database() as db:
            db.execute("UPDATE workspace_runs SET status='failed',snapshot_json=? WHERE id=?",
                (dump({"error_info": error_details(exc), "status": "failed", "result": None}), run_id))
        raise
    return {"job": job, "workspace_run_id": run_id}


def recover(manager):
    """Reconcile durable links after service restart without reissuing requests."""
    with database() as db:
        active = db.execute("SELECT id,job_id FROM workspace_runs WHERE status NOT IN ('success','failed','cancelled','interrupted')").fetchall()
    for row in active:
        try:
            snapshot = manager.get(row["job_id"])
        except APIError:
            snapshot = {"id": row["job_id"], "status": "interrupted", "stage": "interrupted", "logs": [],
                "error": "服务重启导致任务中断；已完成证据仍保留。", "result": None}
        save_snapshot(row["id"], snapshot)

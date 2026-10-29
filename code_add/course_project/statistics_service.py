"""Statistics dashboard/report queries, independent of the HTTP handler."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from course_project.http_support import APIError
from stat_protocols.genotype import BASELINE_PROMPT, compile_candidate

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ROOT = ROOT / "Experiments" / "stat_protocols"
SUITE_LOCK = threading.Lock()
MODES = ("main", "ablation", "composite", "sensitivity", "robustness", "blind")


def _relative(path):
    return path.relative_to(ROOT).as_posix()


def current_root():
    latest = EXPERIMENT_ROOT / "latest.json"
    if latest.exists():
        data = json.loads(latest.read_text(encoding="utf-8"))
        path = (ROOT / data["root"]).resolve()
        if EXPERIMENT_ROOT.resolve() not in path.parents:
            raise ValueError("invalid statistics output directory")
        return path
    return EXPERIMENT_ROOT


@lru_cache(maxsize=4)
def _read(path, mtime_ns, size):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_json(path):
    stat = path.stat()
    return _read(str(path), stat.st_mtime_ns, stat.st_size)


def dashboard():
    path = current_root() / "dashboard.json"
    if not path.exists():
        return {"available": False, "modes": {}, "coverage": [], "message": "尚未生成统计实验，请运行 python -m benchmark.suite"}
    data = dict(read_json(path))
    manifest = data.get("manifest", {})
    llm_record = manifest.get("llm_record") or {}
    llm_id = llm_record.get("candidate_id", "")
    candidate_folder = EXPERIMENT_ROOT / "llm" / llm_id if re.fullmatch(r"[0-9a-f]{32}", llm_id) else None
    llm_evidence = "可一键生成或导入真实候选；缺少记录时保留 missing_real_llm_candidate，不用随机扰动冒充"
    if manifest.get("llm_status") == "record_supplied":
        llm_evidence = f"{llm_record.get('model', '导入模型记录')}；来源 {llm_record.get('provenance', '未声明')}；一份冻结候选跨数据seed评价，Prompt/响应/候选记录可预览"
    modes = data.get("modes", {})
    dimensions = {"tasks": ["mean", "variance", "cardinality", "top_k"], "distributions": manifest.get("distributions", []), "scales": manifest.get("sizes", []), "seeds": manifest.get("seeds", [])}
    protocols = {row["protocol"] for mode in modes.values() for row in mode.get("rows", [])}
    dimensions["protocols"] = sorted(protocols)
    data["dimensions"] = dimensions
    inventory = data.get("inventory", {})
    data["coverage"] = [
        dict(key="mechanisms", title="5.3.1 / W1：历史协议机制基因表", status="complete" if inventory.get("mechanisms") else "pending", evidence=f"{inventory.get('generation_count', 0)} 个世代文件索引；6 类机制，源文件/行号/SHA-256"),
        dict(key="shared_run_result", title="5.3.2 / W3–4：共享采集与四任务", status="complete", evidence="run_many 一次采集；均值、方差、在场基数、Top-k 共享 collection_id"),
        dict(key="communication", title="5.3.3 / W2：通信成本模型", status="complete", evidence="每帧广播/标签响应/预留空槽事件计数；reader/tag/slots/frames/rounds 全部保留"),
        dict(key="baselines", title="5.3.4：Polling / 全识别 / 单任务基线", status="complete" if {"polling", "full_identification", "single_serial"}.issubset(protocols) else "pending", evidence="完整识别为无碰撞乐观下界；单任务串联是明确的无共享消融"),
        dict(key="llm", title="5.3.4：真实 LLM 随机生成基线", status="measured" if manifest.get("llm_status") == "record_supplied" else "needs_model", evidence=llm_evidence),
        dict(key="recheck", title="W6：固定 / 自适应复核与阈值", status="complete" if "sensitivity" in modes else "pending", evidence="1/2/3/4 帧、阈值 0.5/1/2；逐 seed 配对 H2；负结果保留"),
        dict(key="robustness", title="W7–8：规模、缺失、长尾和正式实验", status="complete" if len(dimensions["seeds"]) >= 20 and len(dimensions["scales"]) >= 3 else "partial", evidence=f"{len(dimensions['seeds'])} 个 seed；5 分布；缺失/噪声/极端值；独立盲测；95% CI/P95/最坏值"),
        dict(key="experiments", title="5.3.5 / W8：主实验、消融与 Pareto 图", status="complete" if all(k in modes for k in MODES) else "partial", evidence="同任务内计算 Pareto；失败/不可用候选排除出前沿并单独展示"),
        dict(key="paper", title="W9–10：算法章节、图表校对、交付", status="complete", evidence="自动报告、机制表、H1/H2 验收、复现命令、原始 CSV/JSON 与四组 PNG/PDF"),
        dict(key="integration", title="扩展验证接口", status="out_of_scope", evidence="演化选择、运行时沙箱和物理隔离需要独立模块验证；统计白名单编译器和评价接口已提供"),
    ]
    root = current_root()
    data["delivery_artifacts"] = [dict(name=p.name, path=_relative(p), size=p.stat().st_size, kind=p.suffix[1:]) for p in sorted(root.glob("*")) if p.is_file() and p.name != "dashboard.json"]
    if candidate_folder:
        data["delivery_artifacts"].extend(dict(name="LLM · " + p.name, path=_relative(p), size=p.stat().st_size, kind=p.suffix[1:]) for p in sorted(candidate_folder.glob("*")) if p.is_file() and p.name in ("prompt.txt", "response.txt", "candidate.json"))
    data["commands"] = ["cd code_add", "python -m benchmark.suite --output-dir Experiments/stat_protocols", "# 接入真实 LLM 候选后重跑：", "python -m benchmark.suite --llm-candidate Experiments/stat_protocols/llm/<候选ID>/candidate.json --output-dir Experiments/stat_protocols"]
    if candidate_folder and (candidate_folder / "candidate.json").is_file():
        data["commands"] = ["cd code_add", f"python -m benchmark.suite --llm-candidate {_relative(candidate_folder / 'candidate.json')} --output-dir Experiments/stat_protocols"]
    data["root"] = _relative(root)
    return data


def details(query):
    mode = query.get("mode", ["main"])[0]
    if mode not in MODES:
        raise ValueError("invalid statistics mode")
    path = current_root() / mode / f"{mode}_reports.json"
    reports = read_json(path).get("reports", [])
    for key in ("task", "protocol", "seed"):
        wanted = query.get(key, ["all"])[0]
        if wanted != "all":
            reports = [r for r in reports if str(r.get(key)) == wanted]
    for key in ("distribution", "num_tags", "scenario"):
        wanted = query.get(key, ["all"])[0]
        if wanted == "all":
            continue
        def matches(report):
            m = report["run_result"]["metadata"]
            if key == "scenario":
                try:
                    expected = tuple(float(x) for x in wanted.split("|"))
                except ValueError:
                    raise ValueError("invalid perturbation scenario") from None
                return tuple(m.get(k, 0) for k in ("missing_rate", "noise_rate", "extreme_rate")) == expected
            return str(m.get(key)) == wanted
        reports = [r for r in reports if matches(r)]
    page = int(query.get("page", ["1"])[0])
    if not 1 <= page <= 100000:
        raise ValueError("invalid page")
    start = (page - 1) * 20
    return {"total": len(reports), "page": page, "page_size": 20, "rows": reports[start:start + 20]}


def save_candidate(record):
    if not isinstance(record, dict):
        raise ValueError("candidate record must be a JSON object")
    # Compiling validates only a tiny declarative operator space; no import/exec.
    compile_candidate(record)
    candidate_id = uuid4().hex
    clean = {k: record.get(k) for k in ("model", "prompt_hash", "response_hash", "genotype", "usage")}
    clean.update(candidate_id=candidate_id, provenance="user_imported_not_independently_verified")
    path = EXPERIMENT_ROOT / "llm" / candidate_id / "candidate.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"candidate_id": candidate_id, "path": _relative(path), "record": clean, "valid": True}


def generate_baseline(config):
    from backend_tools.openai_compatible import OpenAICompatibleClient
    class EvidenceClient(OpenAICompatibleClient):
        usage = None
        successful_requests = 0

        def _request_json(self, method, path, payload=None):
            response = super()._request_json(method, path, payload)
            self.usage = response.get("usage")
            self.successful_requests += 1
            return response
    client = EvidenceClient(config)
    system = "You generate a JSON protocol genotype using only the specified allowed operators."
    raw = client.generate_text(system_prompt=system, user_prompt=BASELINE_PROMPT)
    candidate_id = uuid4().hex
    folder = EXPERIMENT_ROOT / "llm" / candidate_id
    folder.mkdir(parents=True)
    # Keys are never included in prompts, manifests or error reports.
    # Byte writes prevent Windows newline translation invalidating evidence hashes.
    (folder / "prompt.txt").write_bytes((system + "\n\n" + BASELINE_PROMPT).encode("utf-8"))
    (folder / "response.txt").write_bytes(raw.encode("utf-8"))
    record = dict(candidate_id=candidate_id, model=config.model, prompt_hash=hashlib.sha256((system + "\n\n" + BASELINE_PROMPT).encode()).hexdigest(), response_hash=hashlib.sha256(raw.encode()).hexdigest(), usage=client.usage, successful_requests=client.successful_requests, retry_attempts="not_exposed_by_client", created_at=datetime.now(timezone.utc).isoformat(), provenance="real_gateway_response", genotype=None)
    validation_error = ""
    try:
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        record["genotype"] = json.loads(text)
        compile_candidate(record)
    except (ValueError, TypeError) as exc:
        validation_error = str(exc)
        record["validation_error"] = validation_error
    path = folder / "candidate.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"candidate_id": candidate_id, "path": _relative(path), "record": record, "valid": not validation_error, "validation_error": validation_error}


def start_suite(manager, payload):
    seed_count = payload.get("seed_count", 20)
    if type(seed_count) is not int or not 2 <= seed_count <= 30:
        raise ValueError("seed_count must be an integer in 2..30")
    sizes = payload.get("sizes", [1000, 10000, 100000])
    if not isinstance(sizes, list) or not sizes or len(sizes) > 3 or any(type(n) is not int or n not in (1000, 10000, 100000) for n in sizes):
        raise ValueError("sizes must contain 1000/10000/100000")
    candidate_id = payload.get("candidate_id")
    if candidate_id and not re.fullmatch(r"[0-9a-f]{32}", str(candidate_id)):
        raise ValueError("invalid candidate ID")
    if candidate_id and not (EXPERIMENT_ROOT / "llm" / candidate_id / "candidate.json").is_file():
        raise FileNotFoundError("candidate not found")
    if not SUITE_LOCK.acquire(blocking=False):
        raise APIError("统计实验已经运行，请等待当前任务完成", 409)
    run_id = uuid4().hex
    output = EXPERIMENT_ROOT / "runs" / run_id
    command = [sys.executable, "-u", "-m", "benchmark.suite", "--seed-count", str(seed_count), "--sizes", ",".join(map(str, sizes)), "--output-dir", str(output)]
    if candidate_id:
        command.extend(["--llm-candidate", str(EXPERIMENT_ROOT / "llm" / candidate_id / "candidate.json")])

    def work(update):
        try:
            update(log="统计基准在独立子进程中运行，超时 30 分钟。", stage="benchmark", progress=5)
            logs = []
            timed_out = threading.Event()
            with subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace") as process:
                def terminate_timeout():
                    timed_out.set()
                    process.kill()
                timer = threading.Timer(1800, terminate_timeout)
                timer.daemon = True
                timer.start()
                try:
                    stage_count = 0
                    for line in process.stdout:
                        logs.append(line.rstrip())
                        if line.startswith("Running "):
                            stage_count += 1
                        update(log=line.rstrip(), progress=min(90, 5 + stage_count * 12), stage="benchmark")
                    returncode = process.wait()
                finally:
                    timer.cancel()
            if timed_out.is_set():
                raise RuntimeError("statistics suite exceeded 30 minute timeout")
            if returncode:
                raise RuntimeError("statistics suite failed; details in the task log")
            # Publish only after the full suite/report is successful.
            pointer = EXPERIMENT_ROOT / "latest.json"
            temporary = pointer.with_suffix(".tmp")
            temporary.write_text(json.dumps({"root": _relative(output)}, ensure_ascii=False), encoding="utf-8")
            temporary.replace(pointer)
            return {"root": _relative(output), "command": command, "stdout": "\n".join(logs)}
        finally:
            SUITE_LOCK.release()
    try:
        return manager.submit("statistics_suite", {"seed_count": seed_count, "sizes": sizes, "candidate_id": candidate_id}, work)
    except Exception:
        SUITE_LOCK.release()
        raise

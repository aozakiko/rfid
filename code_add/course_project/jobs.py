"""Bounded background jobs with consistent progress snapshots."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from http import HTTPStatus
import logging
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable
from uuid import uuid4

from course_project.http_support import APIError, error_details

LOGGER = logging.getLogger(__name__)
TERMINAL_STATES = {"success", "failed", "cancelled", "interrupted"}


class JobCancelled(RuntimeError):
    pass


class JobProgress:
    def __init__(self, manager, job_id):
        self.manager, self.job_id = manager, job_id

    def check_cancelled(self):
        if self.manager.get(self.job_id).get("cancel_requested"):
            raise JobCancelled("cancelled_by_user")

    def __call__(self, **changes):
        self.manager.update(self.job_id, **changes)


def _sanitize(value, secrets=()):
    if isinstance(value, dict):
        return {k: _sanitize(v, secrets) for k, v in value.items()
                if k.lower() not in {"api_key", "api_key_value", "authorization", "password", "access_token"}}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[redacted]")
    return value


def _credentials(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"api_key", "api_key_value", "authorization", "password", "access_token"} and isinstance(item, str) and item:
                yield item
            else:
                yield from _credentials(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _credentials(item)


class JobManager:
    def __init__(self, *, max_workers: int = 2, max_pending: int = 12, history_limit: int = 80, history_path=None):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="selfopt")
        self._capacity = threading.BoundedSemaphore(max_pending)
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._history_limit = history_limit
        self._history_path = Path(history_path) if history_path else None
        self._secrets = {}
        self._observers = {}
        if self._history_path and self._history_path.is_file():
            try:
                rows = json.loads(self._history_path.read_text(encoding="utf-8"))
                for row in rows[-history_limit:]:
                    elapsed = max(0., float(row.pop("elapsed_s", 0.)))
                    row["started_perf"] = time.perf_counter() - elapsed
                    row["finished_perf"] = time.perf_counter()
                    if row["status"] not in TERMINAL_STATES:
                        row.update(status="interrupted", stage="interrupted", error="服务重启导致任务中断；可更换协议名称后重新运行。")
                    self._jobs[row["id"]] = row
            except (ValueError, TypeError, KeyError, OSError):
                LOGGER.exception("Cannot recover background job history")

    def submit(self, kind: str, payload: dict[str, Any], work: Callable[[Callable[..., None]], Any], *, on_skipped=None, on_update=None) -> dict[str, Any]:
        if not self._capacity.acquire(blocking=False):
            raise APIError("后台队列已满，请等待已有任务完成后重试", HTTPStatus.SERVICE_UNAVAILABLE)
        job_id = uuid4().hex
        with self._lock:
            self._secrets[job_id] = tuple(_credentials(payload))
            if on_update:
                self._observers[job_id] = on_update
            self._jobs[job_id] = {
                "id": job_id, "kind": kind, "status": "queued", "stage": "queued", "progress": 0,
                "created_at": self._now(), "updated_at": self._now(), "started_perf": time.perf_counter(),
                "payload": deepcopy(_sanitize(payload, self._secrets[job_id])), "cancel_requested": False,
                "logs": ["任务已进入后台队列。"], "result": None, "error": "",
            }
            self._prune()
            self._persist()
            self._notify(job_id)

        def run() -> None:
            work_started = False
            try:
                progress = JobProgress(self, job_id)
                progress.check_cancelled()
                self.update(job_id, status="running", stage="initializing", progress=3, log="正在读取配置与父代协议。")
                work_started = True
                result = work(progress)
                if self.get(job_id).get("cancel_requested"):
                    self.update(job_id, status="cancelled", stage="cancelled", result=result, log="任务已停止，已完成的结果已保留。")
                    return
                self.update(job_id, status="success", stage="done", progress=100, result=result, log="任务完成。")
            except JobCancelled:
                self.update(job_id, status="cancelled", stage="cancelled", log="任务已在阶段边界停止。")
            except Exception as exc:
                LOGGER.error("Background job %s failed", job_id, exc_info=True)
                detail = error_details(exc, stage=self.get(job_id).get("stage", "running"), request_id=job_id)
                self.update(job_id, status="failed", stage="failed", progress=100,
                    error=detail["message"], error_info=detail, log="任务失败：" + detail["code"])
            finally:
                try:
                    if not work_started and on_skipped:
                        on_skipped()
                finally:
                    self._capacity.release()

        try:
            self._executor.submit(run)
        except RuntimeError:
            with self._lock:
                self._jobs.pop(job_id, None)
            self._capacity.release()
            raise APIError("服务正在关闭", HTTPStatus.SERVICE_UNAVAILABLE) from None
        return self.get(job_id)

    def update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key in ("status", "stage", "result", "error", "error_info"):
                if key in changes and changes[key] is not None:
                    job[key] = changes[key]
            if changes.get("progress") is not None:
                job["progress"] = max(job["progress"], min(100, max(0, int(changes["progress"]))))
            if changes.get("log"):
                job["logs"] = (job["logs"] + [f"[{datetime.now():%H:%M:%S}] {changes['log']}"])[-300:]
            if job["status"] in TERMINAL_STATES:
                job.setdefault("finished_perf", time.perf_counter())
            job["updated_at"] = self._now()
            self._jobs[job_id] = _sanitize(job, self._secrets.get(job_id, ()))
            self._persist()
            self._notify(job_id)

    def _notify(self, job_id):
        callback = self._observers.get(job_id)
        if callback:
            try:
                callback(self.get(job_id))
            except Exception:
                LOGGER.exception("Cannot persist linked workspace snapshot for job %s", job_id)

    def cancel(self, job_id):
        with self._lock:
            if job_id not in self._jobs:
                raise APIError("后台任务不存在或已过期", HTTPStatus.NOT_FOUND)
            job = self._jobs[job_id]
            if job["status"] not in TERMINAL_STATES:
                job["cancel_requested"] = True
                if job["status"] == "queued":
                    self.update(job_id, status="cancelled", stage="cancelled")
                else:
                    self.update(job_id, stage="cancelling", log="已请求停止，当前操作结束后生效。")
        return self.get(job_id)

    def list(self):
        with self._lock:
            return [self.get(key) for key in reversed(self._jobs)]

    def _persist(self):
        if self._history_path is not None:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._history_path.with_suffix(".tmp")
            temporary.write_text(json.dumps([self.get(key) for key in self._jobs], ensure_ascii=False, allow_nan=False), encoding="utf-8")
            temporary.replace(self._history_path)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise APIError("后台任务不存在或已过期", HTTPStatus.NOT_FOUND)
            snapshot = deepcopy(self._jobs[job_id])
        started = snapshot.pop("started_perf")
        finished = snapshot.pop("finished_perf", time.perf_counter())
        snapshot["elapsed_s"] = round(finished - started, 3)
        return snapshot

    def _prune(self) -> None:
        # Active and queued work must remain pollable even when history is full.
        for job_id in list(self._jobs):
            if len(self._jobs) <= self._history_limit:
                break
            if self._jobs[job_id]["status"] in TERMINAL_STATES:
                del self._jobs[job_id]
                self._secrets.pop(job_id, None)
                self._observers.pop(job_id, None)

    def shutdown(self) -> None:
        # Accepted jobs finish before process exit, with no abandoned queued state.
        self._executor.shutdown(wait=True)

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

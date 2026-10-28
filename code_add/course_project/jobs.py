"""Bounded background jobs with consistent progress snapshots."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from http import HTTPStatus
import logging
import threading
import time
from typing import Any, Callable
from uuid import uuid4

from course_project.http_support import APIError

LOGGER = logging.getLogger(__name__)
TERMINAL_STATES = {"success", "failed"}


class JobManager:
    def __init__(self, *, max_workers: int = 2, max_pending: int = 12, history_limit: int = 80):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="selfopt")
        self._capacity = threading.BoundedSemaphore(max_pending)
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._history_limit = history_limit

    def submit(self, kind: str, payload: dict[str, Any], work: Callable[[Callable[..., None]], Any]) -> dict[str, Any]:
        if not self._capacity.acquire(blocking=False):
            raise APIError("后台队列已满，请等待已有任务完成后重试", HTTPStatus.SERVICE_UNAVAILABLE)
        job_id = uuid4().hex
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id, "kind": kind, "status": "queued", "stage": "queued", "progress": 0,
                "created_at": self._now(), "updated_at": self._now(), "started_perf": time.perf_counter(),
                "payload": deepcopy({k: v for k, v in payload.items() if k not in {"api_key", "api_key_value"}}),
                "logs": ["任务已进入后台队列。"], "result": None, "error": "",
            }
            self._prune()

        def run() -> None:
            try:
                self.update(job_id, status="running", stage="initializing", progress=3, log="正在读取配置与父代协议。")
                result = work(lambda **changes: self.update(job_id, **changes))
                self.update(job_id, status="success", stage="done", progress=100, result=result, log="任务完成。")
            except Exception as exc:
                LOGGER.error("Background job %s failed", job_id, exc_info=True)
                # Tracebacks stay in server logs. Client snapshots contain no credentials.
                self.update(job_id, status="failed", stage="failed", progress=100, error="任务执行失败，请查看服务日志。", log="任务执行失败。")
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
            for key in ("status", "stage", "result", "error"):
                if key in changes and changes[key] is not None:
                    job[key] = changes[key]
            if changes.get("progress") is not None:
                job["progress"] = max(job["progress"], min(100, max(0, int(changes["progress"]))))
            if changes.get("log"):
                job["logs"] = (job["logs"] + [f"[{datetime.now():%H:%M:%S}] {changes['log']}"])[-300:]
            if job["status"] in TERMINAL_STATES:
                job["finished_perf"] = time.perf_counter()
            job["updated_at"] = self._now()

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

    def shutdown(self) -> None:
        # Accepted jobs finish before process exit, with no abandoned queued state.
        self._executor.shutdown(wait=True)

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

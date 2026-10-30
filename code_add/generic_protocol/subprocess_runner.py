"""Execute free-form legacy protocol files behind a process boundary."""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from stat_protocols.contracts import RunResult

from .base import get_spec_value, nonnegative_int, requested_tasks
from .legacy_adapter import LegacyProtocolAdapter


def _tag_payload(tags: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tag in tags or []:
        if isinstance(tag, dict):
            row = dict(tag)
        else:
            row = {
                "id": getattr(tag, "id", None),
                "price": getattr(tag, "price", None),
            }
        rows.append(row)
    return rows


def _spec_payload(spec: Any) -> dict[str, Any]:
    if hasattr(spec, "to_dict"):
        data = spec.to_dict()
    elif isinstance(spec, dict):
        data = dict(spec)
    else:
        data = dict(vars(spec))
    return data


def _failure_results(spec: Any, protocol_name: str, reason: str, metadata: dict[str, Any] | None = None) -> dict[str, RunResult]:
    base_metadata = {"cost_status": "missing", "failure_stage": "subprocess"}
    if metadata:
        base_metadata.update(metadata)
    return {
        task: RunResult(
            protocol=str(protocol_name),
            task=task,
            truth=None,
            estimate=None,
            reader_bits=0,
            tag_bits=0,
            slots=0,
            frames=0,
            rounds=0,
            seed=nonnegative_int(get_spec_value(spec, "seed", 0)),
            metadata=dict(base_metadata),
            failure_reason=reason,
        )
        for task in requested_tasks(spec, default_task="legacy_mode_query", explicit_wins=True)
    }


class _RemoteLegacyProtocol:
    def __init__(self, payload: Mapping[str, Any]):
        self.payload = payload

    def execute(self, tags: Any, price_min: int, price_max: int) -> tuple[Any, Any, Any, Any]:
        return (
            self.payload.get("estimated_values"),
            self.payload.get("metric"),
            self.payload.get("execution_time"),
            self.payload.get("verification_data", {}),
        )


def run_protocol_file(
    protocol_file: str | os.PathLike[str],
    protocol_name: str,
    tags: Any,
    spec: Any,
    timeout_seconds: float = 10,
) -> dict[str, RunResult]:
    """Run one protocol file in a fresh Python process.

    The child communicates using one JSON request on stdin and one JSON
    response on stdout.  All load/runtime/timeout failures are represented by
    ordinary ``RunResult`` rows with ``failure_reason`` so a bad candidate
    cannot crash or mutate the parent process.
    """
    if not isinstance(protocol_name, str) or not protocol_name:
        raise ValueError("protocol_name must be a non-empty string")
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be a positive number") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_seconds must be a positive number")

    path = Path(protocol_file).expanduser().resolve()
    if not path.is_file():
        return _failure_results(spec, protocol_name, f"protocol_file_not_found: {path}")

    request = {
        "protocol_file": str(path),
        "protocol_name": protocol_name,
        "tags": _tag_payload(tags),
        "spec": _spec_payload(spec),
        "price_min": get_spec_value(spec, "price_min", 0),
        "price_max": get_spec_value(spec, "price_max", 0),
    }
    worker = Path(__file__).with_name("subprocess_worker.py")
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    code_add_root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join([code_add_root, str(path.parent), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)

    try:
        with tempfile.TemporaryDirectory(prefix="generic-protocol-") as temp_dir:
            completed = subprocess.run(
                [sys.executable, "-I", "-B", str(worker)],
                input=json.dumps(request, ensure_ascii=False, separators=(",", ":"), default=_json_default),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=temp_dir,
                env=env,
                timeout=timeout,
                check=False,
            )
    except subprocess.TimeoutExpired:
        return _failure_results(
            spec,
            protocol_name,
            f"subprocess_timeout: exceeded {timeout:g} seconds",
            {"timeout_seconds": timeout},
        )
    except OSError as exc:
        return _failure_results(spec, protocol_name, f"subprocess_start_failed: {type(exc).__name__}: {exc}")

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-2000:]
        return _failure_results(
            spec,
            protocol_name,
            f"subprocess_exit_{completed.returncode}: {detail}",
            {"returncode": completed.returncode, "stderr": detail},
        )
    try:
        response = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        detail = (completed.stdout or "").strip()[-2000:]
        return _failure_results(
            spec,
            protocol_name,
            f"invalid_subprocess_json: {type(exc).__name__}: {exc}",
            {"stdout": detail, "stderr": (completed.stderr or "").strip()[-2000:]},
        )
    if not isinstance(response, dict) or response.get("ok") is not True:
        error = response.get("error", "unknown subprocess error") if isinstance(response, dict) else "unknown subprocess error"
        error_type = response.get("error_type", "") if isinstance(response, dict) else ""
        detail = f"{error_type}: {error}" if error_type else str(error)
        return _failure_results(spec, protocol_name, f"subprocess_protocol_error: {detail}", {"response": response})
    raw = response.get("result")
    if not isinstance(raw, Mapping):
        return _failure_results(spec, protocol_name, "subprocess_protocol_error: result payload is not an object")
    remote = _RemoteLegacyProtocol(raw)
    return LegacyProtocolAdapter(remote, protocol_name=protocol_name).run_many(tags, spec)


def _json_default(value: Any) -> Any:
    if isinstance(value, set):
        return list(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dict__"):
        return vars(value)
    return str(value)



"""Child-process entry point for ``generic_protocol.subprocess_runner``."""
from __future__ import annotations

import contextlib
import importlib.util
import json
from pathlib import Path
import sys
import time
from typing import Any


_CODE_ADD_ROOT = Path(__file__).resolve().parent.parent
if str(_CODE_ADD_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ADD_ROOT))


class _FunctionProtocol:
    def __init__(self, function):
        self.function = function

    def execute(self, tags, price_min, price_max):
        return self.function(tags, price_min, price_max)


def _load_protocol(protocol_file: str, protocol_name: str) -> Any:
    path = Path(protocol_file)
    protocol_root = str(path.parent)
    if protocol_root not in sys.path:
        sys.path.insert(0, protocol_root)
    module_name = f"_generic_protocol_candidate_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load protocol file: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    candidate = getattr(module, protocol_name)
    if isinstance(candidate, type):
        return candidate()
    if callable(candidate) and not hasattr(candidate, "execute"):
        return _FunctionProtocol(candidate)
    return candidate


def _tags_from_payload(rows: Any) -> list[Any]:
    from base import Tag

    tags = []
    for row in rows or []:
        if not isinstance(row, dict):
            raise TypeError("tags must be a JSON list of objects")
        tag = Tag(int(row.get("id", 0)), int(row.get("price", 0)))
        for key, value in row.items():
            if key not in {"id", "price"}:
                setattr(tag, key, value)
        tags.append(tag)
    return tags


def _json_default(value: Any) -> Any:
    if isinstance(value, set):
        return list(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dict__"):
        return vars(value)
    return str(value)


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise TypeError("request must be a JSON object")
        protocol_file = request["protocol_file"]
        protocol_name = request["protocol_name"]
        tags = _tags_from_payload(request.get("tags"))
        price_min = int(request.get("price_min", 0))
        price_max = int(request.get("price_max", 0))
        # Candidate imports and prints are kept away from the JSON stdout
        # protocol channel; stderr is captured by the parent runner.
        with contextlib.redirect_stdout(sys.stderr):
            protocol = _load_protocol(protocol_file, protocol_name)
            execute = getattr(protocol, "execute", None)
            if not callable(execute):
                raise TypeError(f"{protocol_name} has no callable execute(tags, price_min, price_max)")
            payload = execute(tags, price_min, price_max)
        if isinstance(payload, dict):
            result = payload
        elif isinstance(payload, (tuple, list)) and len(payload) == 4:
            estimated_values, metric, execution_time, verification_data = payload
            result = {
                "estimated_values": estimated_values,
                "metric": metric,
                "execution_time": execution_time,
                "verification_data": verification_data,
            }
        else:
            raise TypeError("execute must return a 4-item tuple or mapping")
        response = {"ok": True, "result": result}
    except Exception as exc:
        response = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":"), default=_json_default, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


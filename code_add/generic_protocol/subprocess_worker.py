"""Child-process entry point for ``generic_protocol.subprocess_runner``."""
from __future__ import annotations

import contextlib
import importlib.util
import importlib
import builtins
import ast
from types import ModuleType
import random
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


class _BoundedOutput:
    def __init__(self, stream, limit=2_000_000):
        self.stream, self.limit, self.written = stream, limit, 0

    def write(self, value):
        self.written += len(value.encode("utf-8", errors="replace"))
        if self.written > self.limit:
            raise RuntimeError("subprocess_output_limit: exceeded 2 MB")
        return self.stream.write(value)

    def flush(self):
        self.stream.flush()


def _load_protocol(protocol_file: str, protocol_name: str, source=None, strict=False) -> Any:
    path = Path(protocol_file)
    protocol_root = str(path.parent)
    if protocol_root not in sys.path:
        sys.path.insert(0, protocol_root)
    module_name = f"_generic_protocol_candidate_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load protocol file: {path}")
    module = importlib.util.module_from_spec(spec)
    if strict:
        from generic_protocol.security import ALLOWED_IMPORT_ROOTS, BLOCKED_CALLS, BLOCKED_MEMBERS, analyze_source
        report = analyze_source(source, strict=True)
        if not report["passed"]:
            raise ValueError("strict AST check failed in worker")
        modules = {name: importlib.import_module(name) for name in ALLOWED_IMPORT_ROOTS}
        measured_mode = any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "execute_channel"
                            for node in ast.walk(ast.parse(source)))
        if measured_mode:
            # Do not share numpy/base module globals with a measured reader:
            # replacing numpy.asarray could otherwise capture private channel IDs.
            safe_math = ModuleType("math")
            for name in dir(modules["math"]):
                if not name.startswith("_"):
                    setattr(safe_math, name, getattr(modules["math"], name))
            safe_base = ModuleType("base")
            safe_base.IProtocol = type("IProtocol", (), {})
            modules = {"math": safe_math, "base": safe_base, "__future__": modules["__future__"]}
        def allowed_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level or name.split(".")[0] not in modules or any(member in BLOCKED_MEMBERS or member == "*" for member in fromlist or ()):
                raise PermissionError(f"import_not_allowed: {name}")
            if measured_mode:
                if "." in name:
                    raise PermissionError("measured_reader_import_not_allowed")
                return modules[name]
            return builtins.__import__(name, globals, locals, fromlist, level)
        def guarded_getattr(obj, name, *default):
            if name in BLOCKED_MEMBERS or (measured_mode and name in {"format", "format_map"}) or (name.startswith("__") and name not in {"__init__", "__name__"}):
                raise PermissionError(f"runtime_operation_blocked: attribute {name}")
            return builtins.getattr(obj, name, *default)
        allowed = {key: value for key, value in vars(builtins).items() if key not in BLOCKED_CALLS}
        allowed["__import__"] = allowed_import
        allowed["getattr"] = guarded_getattr
        module.__dict__["__builtins__"] = allowed
        def audit(event, args):
            if event == "open" or event.startswith(("socket.", "subprocess.", "os.system", "os.exec", "os.spawn", "ctypes.")):
                raise PermissionError(f"runtime_operation_blocked: {event}")
        sys.addaudithook(audit)
    exec(compile(source, str(path), "exec"), module.__dict__)
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
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    limits = {}
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise TypeError("request must be a JSON object")
        protocol_file = request["protocol_file"]
        protocol_name = request["protocol_name"]
        tags = _tags_from_payload(request.get("tags"))
        raw_tags = tags
        security_mode = str(request.get("security_mode") or "legacy")
        if security_mode not in {"legacy", "strict"}:
            raise ValueError("security_mode must be 'legacy' or 'strict'")
        if security_mode == "strict":
            from generic_protocol.security import wrap_tags
            from generic_protocol.runtime_limits import limit_memory

            limits = limit_memory(int(request.get("memory_limit_mb", 768)))

            tags = wrap_tags(tags, protocol_file=str(Path(protocol_file).resolve()), strict=True)
        price_min = int(request.get("price_min", 0))
        price_max = int(request.get("price_max", 0))
        seed = int((request.get("spec") or {}).get("seed", 0))
        random.seed(seed)
        import numpy as np
        np.random.seed(seed % (2 ** 32))
        from generic_protocol.measured_channel import create_channel
        # Construct before executing untrusted code or installing its audit hook.
        # Candidate imports and prints are kept away from the JSON stdout
        # protocol channel; stderr is captured by the parent runner.
        bounded = _BoundedOutput(sys.stderr)
        with contextlib.redirect_stdout(bounded), contextlib.redirect_stderr(bounded):
            protocol = _load_protocol(protocol_file, protocol_name, source=request.get("source"), strict=security_mode == "strict")
            execute = getattr(protocol, "execute", None)
            execute_channel = getattr(protocol, "execute_channel", None) if security_mode == "strict" else None
            instrumented = callable(execute_channel)
            if instrumented and not any(isinstance(node, ast.FunctionDef) and node.name == "execute_channel"
                                        for node in ast.walk(ast.parse(request["source"]))):
                raise PermissionError("measured_entrypoint_must_be_declared")
            if not instrumented and not callable(execute):
                raise TypeError(f"{protocol_name} has no callable execute(tags, price_min, price_max)")
            started, cpu_started = time.perf_counter(), time.process_time()
            if instrumented:
                channel, channel_evidence = create_channel(raw_tags, price_min, price_max, seed)
                estimated = execute_channel(channel, price_min, price_max)
                if not isinstance(estimated, list):
                    raise TypeError("execute_channel must return a list")
                measured = channel_evidence()
                payload = {"estimated_values": estimated, "metric": 0, "execution_time": 0,
                           "verification_data": {"frame_size": measured["frame_size"], "bits_per_slot": 1}}
            else:
                payload = execute(tags, price_min, price_max)
            measured_wall_ms = (time.perf_counter() - started) * 1000
            measured_cpu_ms = (time.process_time() - cpu_started) * 1000
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
        response = {"ok": True, "result": result, "measured_cpu_time_ms": measured_cpu_ms,
                    "instrumented_channel": measured if instrumented else None,
                    "measured_wall_time_ms": measured_wall_ms,
                    "resource_limits": {k: v for k, v in limits.items() if not k.startswith("_")}}
    except Exception as exc:
        response = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":"), default=_json_default, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


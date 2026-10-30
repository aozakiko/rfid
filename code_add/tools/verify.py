"""Standard-library one-command verification for the benchmark package."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
COMPILE_FILES = (
    "ai_protocol_optimizer.py",
    "benchmark/run_config.py",
    "benchmark/runner.py",
    "benchmark/experiments.py",
    "generic_tasks/__init__.py",
    "generic_tasks/registry.py",
    "generic_tasks/profiles.py",
    "generic_tasks/schemas.py",
    "generic_protocol/__init__.py",
    "generic_protocol/base.py",
    "generic_protocol/candidate.py",
    "generic_protocol/legacy_adapter.py",
    "generic_protocol/statistics_adapter.py",
    "generic_protocol/subprocess_runner.py",
    "generic_protocol/subprocess_worker.py",
    "stat_protocols/contracts.py",
    "stat_protocols/protocols.py",
    "tools/verify.py",
    "course_project/test_generic_benchmark.py",
    "course_project/test_generic_contracts.py",
    "course_project/test_generic_protocol.py",
    "course_project/test_run_config.py",
)

def _run(command: Sequence[str], *, timeout: int = 180) -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        result = subprocess.run(
            list(command),
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"verification command failed: {exc}", file=sys.stderr)
        return 1
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


def verify(*, run_unittests: bool = True) -> int:
    """Run compile checks, unit tests, and a smoke config in a temporary dir.

    The return value is a process-style exit code: zero means every step
    passed, while any failed step returns non-zero immediately.
    """
    compile_command = [sys.executable, "-m", "py_compile", *COMPILE_FILES]
    code = _run(compile_command)
    if code != 0:
        return code

    if run_unittests:
        code = _run([sys.executable, "-m", "unittest", "discover", "-s", "course_project", "-p", "test*.py", "-v"])
        if code != 0:
            return code

    with tempfile.TemporaryDirectory(prefix="rfid-verify-smoke-") as temp_dir:
        code = _run([
            sys.executable,
            "-m",
            "benchmark.run_config",
            "--config",
            "benchmark/configs/smoke.json",
            "--output-dir",
            temp_dir,
        ])
        if code != 0:
            return code
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    # argv is accepted for testability; verification intentionally has no options.
    return verify()


if __name__ == "__main__":
    raise SystemExit(main())

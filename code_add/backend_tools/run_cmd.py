from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from backend_tools import artifacts
from backend_tools.run_store import DEFAULT_DB_PATH, PROJECT_ROOT, create_run, finish_run


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a command under code_add project root and store stdout + artifacts into SQLite."
    )
    parser.add_argument("--kind", type=str, required=True, help="run kind, e.g. batch_test|robust|plot")
    parser.add_argument("--scene", type=str, default=None, help="optional scene name")
    parser.add_argument("--db", type=str, default=str(DEFAULT_DB_PATH), help="sqlite db path")
    parser.add_argument("--no-artifacts", action="store_true", help="do not scan artifacts")

    # Everything after -- is treated as the command
    parser.add_argument("cmd", nargs=argparse.REMAINDER, help="command to run, prefix with --")
    args = parser.parse_args()

    if not args.cmd or args.cmd[0] != "--":
        raise SystemExit("Usage: python -m backend_tools.run_cmd --kind ... -- <command...>")

    args.cmd = args.cmd[1:]
    if not args.cmd:
        raise SystemExit("command is empty")

    return args


def main() -> int:
    args = parse_args()

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = (PROJECT_ROOT / db_path).resolve()

    cmd: list[str] = list(args.cmd)

    before = set()
    if not args.no_artifacts:
        before = artifacts.snapshot(PROJECT_ROOT)

    run = create_run(kind=args.kind, scene=args.scene, command=cmd, params={}, db_path=db_path)
    print(f"[run_cmd] run_id={run.id} db={db_path}")
    print("[run_cmd] $ " + " ".join(cmd))

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    output_lines: list[str] = []

    try:
        process = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip("\n")
            output_lines.append(line)
            print(line)
        exit_code = process.wait()
    except Exception as e:
        exit_code = None
        output_lines.append(f"FATAL: {e}")
        print(f"FATAL: {e}")
    finally:
        after = set()
        new_artifacts: list[Path] = []
        if not args.no_artifacts:
            after = artifacts.snapshot(PROJECT_ROOT)
            new_artifacts = artifacts.diff(before, after, PROJECT_ROOT)

        status = "success" if (exit_code == 0) else "failed"
        finish_run(
            run_id=run.id,
            status=status,
            exit_code=exit_code,
            output="\n".join(output_lines),
            artifacts=new_artifacts,
            db_path=db_path,
        )

        print(f"[run_cmd] finished run_id={run.id} status={status} exit_code={exit_code}")
        if new_artifacts:
            print("[run_cmd] new artifacts:")
            for p in new_artifacts:
                print("  - " + str(p).replace('\\', '/'))

    return 0 if exit_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

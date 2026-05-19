from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "runs.sqlite3"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: Path = DEFAULT_DB_PATH) -> Path:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                scene TEXT,
                command TEXT NOT NULL,
                params_json TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                exit_code INTEGER,
                output TEXT
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_kind ON runs(kind);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_run_id ON artifacts(run_id);")
    return db_path


@dataclass(frozen=True)
class Run:
    id: int
    kind: str
    scene: Optional[str]
    status: str
    started_at: str


def create_run(
    *,
    kind: str,
    scene: Optional[str],
    command: list[str],
    params: Optional[dict[str, Any]] = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> Run:
    init_db(db_path)
    started_at = utc_now_iso()
    cmd_json = json.dumps(command, ensure_ascii=False)
    params_json = json.dumps(params or {}, ensure_ascii=False)

    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO runs(kind, scene, command, params_json, status, started_at) VALUES (?, ?, ?, ?, ?, ?)",
            (kind, scene, cmd_json, params_json, "running", started_at),
        )
        run_id = int(cur.lastrowid)

    return Run(id=run_id, kind=kind, scene=scene, status="running", started_at=started_at)


def finish_run(
    *,
    run_id: int,
    status: str,
    exit_code: Optional[int],
    output: str,
    artifacts: Iterable[Path] = (),
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    init_db(db_path)
    finished_at = utc_now_iso()

    with connect(db_path) as conn:
        conn.execute(
            "UPDATE runs SET status=?, finished_at=?, exit_code=?, output=? WHERE id=?",
            (status, finished_at, exit_code, output, run_id),
        )
        for p in artifacts:
            conn.execute(
                "INSERT INTO artifacts(run_id, path) VALUES (?, ?)",
                (run_id, p.as_posix()),
            )


def list_runs(*, limit: int = 50, db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, kind, scene, status, started_at, finished_at, exit_code FROM runs ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()

    results: list[dict[str, Any]] = []
    for r in rows:
        results.append(
            {
                "id": r[0],
                "kind": r[1],
                "scene": r[2],
                "status": r[3],
                "started_at": r[4],
                "finished_at": r[5],
                "exit_code": r[6],
            }
        )
    return results


def get_run(*, run_id: int, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, kind, scene, command, params_json, status, started_at, finished_at, exit_code, output FROM runs WHERE id=?",
            (int(run_id),),
        ).fetchone()
        if row is None:
            raise KeyError(f"run_id not found: {run_id}")
        artifacts = conn.execute(
            "SELECT path FROM artifacts WHERE run_id=? ORDER BY id ASC",
            (int(run_id),),
        ).fetchall()

    return {
        "id": row[0],
        "kind": row[1],
        "scene": row[2],
        "command": json.loads(row[3]),
        "params": json.loads(row[4] or "{}"),
        "status": row[5],
        "started_at": row[6],
        "finished_at": row[7],
        "exit_code": row[8],
        "output": row[9] or "",
        "artifacts": [a[0] for a in artifacts],
    }

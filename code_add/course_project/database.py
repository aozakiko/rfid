from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from course_project.artifacts import alert_rows, gateway_summary_rows, stream_rows


COURSE_ROOT = Path(__file__).resolve().parent
DB_PATH = COURSE_ROOT / "course_project.sqlite3"


def utc_now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def open_database(db_path: Path | str = DB_PATH):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_database(db_path: Path | str = DB_PATH) -> Path:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open_database(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS gateways (
                gateway_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                location TEXT NOT NULL,
                status TEXT NOT NULL,
                last_run_id INTEGER,
                last_seen_at TEXT
            );

            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                gateway_id TEXT NOT NULL,
                name TEXT NOT NULL,
                device_type TEXT NOT NULL,
                location TEXT NOT NULL,
                status TEXT NOT NULL,
                last_run_id INTEGER,
                last_seen_at TEXT
            );

            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                scene TEXT NOT NULL,
                protocol TEXT NOT NULL,
                protocol_source TEXT,
                devices INTEGER NOT NULL,
                gateways INTEGER NOT NULL,
                batches INTEGER NOT NULL,
                num_tags INTEGER NOT NULL,
                price_min INTEGER NOT NULL,
                price_max INTEGER NOT NULL,
                stream_window INTEGER NOT NULL,
                shared_secret_hint TEXT NOT NULL,
                output_dir TEXT,
                dashboard_path TEXT,
                total_windows INTEGER NOT NULL DEFAULT 0,
                average_hit_rate REAL NOT NULL DEFAULT 0,
                avg_gateway_protocol_time_ms REAL NOT NULL DEFAULT 0,
                alert_count INTEGER NOT NULL DEFAULT 0,
                security_passed INTEGER,
                benchmark_count INTEGER NOT NULL DEFAULT 0,
                wall_clock_s REAL NOT NULL DEFAULT 0,
                result_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS stream_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                latest_batch INTEGER,
                latest_gateway TEXT,
                latest_device TEXT,
                latest_modes_json TEXT NOT NULL,
                recent_hot_prices_json TEXT NOT NULL,
                recent_hit_rate REAL,
                recent_avg_protocol_time_ms REAL,
                recent_alert_count INTEGER
            );

            CREATE TABLE IF NOT EXISTS gateway_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                gateway_id TEXT NOT NULL,
                windows INTEGER,
                avg_protocol_time_ms REAL,
                avg_frame_size REAL,
                hit_rate REAL
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                gateway_id TEXT,
                device_id TEXT,
                batch_index INTEGER,
                alert TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS security_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                algorithm TEXT NOT NULL,
                valid_message_accepted INTEGER NOT NULL,
                tampered_payload_rejected INTEGER NOT NULL,
                wrong_secret_rejected INTEGER NOT NULL,
                missing_signature_rejected INTEGER NOT NULL,
                passed INTEGER NOT NULL,
                result_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS benchmark_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                devices INTEGER NOT NULL,
                gateways INTEGER NOT NULL,
                batches INTEGER NOT NULL,
                num_tags INTEGER NOT NULL,
                price_min INTEGER NOT NULL,
                price_max INTEGER NOT NULL,
                total_windows INTEGER NOT NULL,
                average_hit_rate REAL NOT NULL,
                avg_gateway_protocol_time_ms REAL NOT NULL,
                alert_count INTEGER NOT NULL,
                wall_clock_s REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                scene TEXT NOT NULL,
                parent_a TEXT NOT NULL,
                parent_b TEXT NOT NULL,
                protocol_name TEXT NOT NULL,
                target_generation TEXT NOT NULL,
                dry_run INTEGER NOT NULL,
                status TEXT NOT NULL,
                prompt_file TEXT,
                target_file TEXT,
                logs TEXT NOT NULL,
                result_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS design_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                title TEXT NOT NULL,
                preset TEXT NOT NULL,
                requirement_text TEXT NOT NULL,
                recommended_protocol TEXT,
                recommended_score REAL,
                grdl_json TEXT NOT NULL,
                ahp_json TEXT NOT NULL,
                playbook_json TEXT NOT NULL,
                validation_json TEXT NOT NULL,
                deployment_json TEXT NOT NULL,
                full_case_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS deployment_manifests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                design_case_id INTEGER REFERENCES design_cases(id) ON DELETE CASCADE,
                run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                selected_protocol TEXT NOT NULL,
                fallback_protocol TEXT,
                manifest_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS system_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                entity_type TEXT,
                entity_id TEXT,
                message TEXT NOT NULL,
                detail_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_stream_run_id ON stream_snapshots(run_id, position);
            CREATE INDEX IF NOT EXISTS idx_alerts_run_id ON alerts(run_id);
            CREATE INDEX IF NOT EXISTS idx_benchmark_run_id ON benchmark_results(run_id);
            CREATE INDEX IF NOT EXISTS idx_ai_jobs_created_at ON ai_jobs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_design_cases_created_at ON design_cases(created_at DESC);
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES(?, ?)",
            ("schema_version", "2"),
        )
    return path


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def _avg_gateway_latency(batch_report: dict[str, Any]) -> float:
    values = [
        float(item.get("avg_protocol_time_ms", 0.0))
        for item in batch_report.get("gateway_summary", {}).values()
    ]
    return round(mean(values), 2) if values else 0.0


def _secret_hint(shared_secret: str) -> str:
    if not shared_secret:
        return "empty"
    if len(shared_secret) <= 4:
        return "*" * len(shared_secret)
    return f"{shared_secret[:2]}***{shared_secret[-2:]}"


def _store_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    message: str,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO system_events(created_at, event_type, entity_type, entity_id, message, detail_json)
        VALUES(?, ?, ?, ?, ?, ?)
        """,
        (utc_now_text(), event_type, entity_type, str(entity_id) if entity_id is not None else None, message, _json(detail or {})),
    )


def _upsert_topology(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    device_count: int,
    gateway_count: int,
) -> None:
    now = utc_now_text()
    for index in range(gateway_count):
        gateway_id = f"edge-{index + 1}"
        conn.execute(
            """
            INSERT INTO gateways(gateway_id, name, location, status, last_run_id, last_seen_at)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(gateway_id) DO UPDATE SET
                status=excluded.status,
                last_run_id=excluded.last_run_id,
                last_seen_at=excluded.last_seen_at
            """,
            (gateway_id, f"边缘网关 {index + 1}", f"仓储分区 {index + 1}", "online", run_id, now),
        )

    for index in range(device_count):
        gateway_id = f"edge-{(index % gateway_count) + 1}"
        device_id = f"reader-{index + 1}"
        conn.execute(
            """
            INSERT INTO devices(device_id, gateway_id, name, device_type, location, status, last_run_id, last_seen_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                gateway_id=excluded.gateway_id,
                status=excluded.status,
                last_run_id=excluded.last_run_id,
                last_seen_at=excluded.last_seen_at
            """,
            (
                device_id,
                gateway_id,
                f"RFID 读写器 {index + 1}",
                "RFID+TempHumidity",
                f"货架通道 {index + 1}",
                "online",
                run_id,
                now,
            ),
        )


def store_course_run(
    *,
    config: dict[str, Any],
    result: dict[str, Any],
    security_result: dict[str, Any] | None,
    benchmark_rows: list[dict[str, Any]] | None,
    output_dir: Path,
    dashboard_path: Path | None,
    wall_clock_s: float,
    db_path: Path | str = DB_PATH,
) -> int:
    init_database(db_path)
    scenario = result["scenario"]
    batch_report = result["batch_report"]
    security_passed = None if security_result is None else int(bool(security_result.get("passed")))
    benchmark_count = len(benchmark_rows or [])

    with open_database(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO runs(
                created_at, status, scene, protocol, protocol_source, devices, gateways, batches,
                num_tags, price_min, price_max, stream_window, shared_secret_hint, output_dir,
                dashboard_path, total_windows, average_hit_rate, avg_gateway_protocol_time_ms,
                alert_count, security_passed, benchmark_count, wall_clock_s, result_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now_text(),
                "completed",
                scenario.get("scene"),
                scenario.get("protocol"),
                scenario.get("protocol_source"),
                int(scenario.get("device_count", 0)),
                int(scenario.get("gateway_count", 0)),
                int(scenario.get("batch_count", 0)),
                int(scenario.get("num_tags_per_batch", 0)),
                int(scenario.get("price_min", 0)),
                int(scenario.get("price_max", 0)),
                int(scenario.get("stream_window_size", 0)),
                _secret_hint(str(config.get("shared_secret", ""))),
                str(output_dir),
                str(dashboard_path) if dashboard_path else None,
                int(batch_report.get("total_windows", 0)),
                float(batch_report.get("average_hit_rate", 0.0)),
                _avg_gateway_latency(batch_report),
                len(batch_report.get("alerts", [])),
                security_passed,
                benchmark_count,
                float(wall_clock_s),
                _json(result),
            ),
        )
        run_id = int(cursor.lastrowid)

        _upsert_topology(
            conn,
            run_id=run_id,
            device_count=int(scenario.get("device_count", 0)),
            gateway_count=int(scenario.get("gateway_count", 0)),
        )

        for row in stream_rows(result):
            conn.execute(
                """
                INSERT INTO stream_snapshots(
                    run_id, position, latest_batch, latest_gateway, latest_device, latest_modes_json,
                    recent_hot_prices_json, recent_hit_rate, recent_avg_protocol_time_ms, recent_alert_count
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    int(row.get("index", 0)),
                    row.get("latest_batch"),
                    row.get("latest_gateway"),
                    row.get("latest_device"),
                    row.get("latest_modes"),
                    row.get("recent_hot_prices"),
                    row.get("recent_hit_rate"),
                    row.get("recent_avg_protocol_time_ms"),
                    row.get("recent_alert_count"),
                ),
            )

        for row in gateway_summary_rows(batch_report):
            conn.execute(
                """
                INSERT INTO gateway_summaries(run_id, gateway_id, windows, avg_protocol_time_ms, avg_frame_size, hit_rate)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    row.get("gateway_id"),
                    row.get("windows"),
                    row.get("avg_protocol_time_ms"),
                    row.get("avg_frame_size"),
                    row.get("hit_rate"),
                ),
            )

        for row in alert_rows(batch_report):
            conn.execute(
                """
                INSERT INTO alerts(run_id, gateway_id, device_id, batch_index, alert)
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    row.get("gateway_id"),
                    row.get("device_id"),
                    row.get("batch_index"),
                    row.get("alert"),
                ),
            )

        if security_result is not None:
            _insert_security_check(conn, run_id=run_id, security_result=security_result)

        for row in benchmark_rows or []:
            conn.execute(
                """
                INSERT INTO benchmark_results(
                    run_id, devices, gateways, batches, num_tags, price_min, price_max,
                    total_windows, average_hit_rate, avg_gateway_protocol_time_ms, alert_count, wall_clock_s
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    row.get("devices"),
                    row.get("gateways"),
                    row.get("batches"),
                    row.get("num_tags"),
                    row.get("price_min"),
                    row.get("price_max"),
                    row.get("total_windows"),
                    row.get("average_hit_rate"),
                    row.get("avg_gateway_protocol_time_ms"),
                    row.get("alert_count"),
                    row.get("wall_clock_s"),
                ),
            )

        _store_event(
            conn,
            event_type="run_completed",
            entity_type="run",
            entity_id=run_id,
            message=f"完成云边端协同运行 #{run_id}",
            detail={"scene": scenario.get("scene"), "protocol": scenario.get("protocol")},
        )
    return run_id


def _insert_security_check(
    conn: sqlite3.Connection,
    *,
    run_id: int | None,
    security_result: dict[str, Any],
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO security_checks(
            run_id, created_at, algorithm, valid_message_accepted, tampered_payload_rejected,
            wrong_secret_rejected, missing_signature_rejected, passed, result_json
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            utc_now_text(),
            security_result.get("algorithm", "HMAC-SHA256"),
            int(bool(security_result.get("valid_message_accepted"))),
            int(bool(security_result.get("tampered_payload_rejected"))),
            int(bool(security_result.get("wrong_secret_rejected"))),
            int(bool(security_result.get("missing_signature_rejected"))),
            int(bool(security_result.get("passed"))),
            _json(security_result),
        ),
    )
    _store_event(
        conn,
        event_type="security_check",
        entity_type="run" if run_id else "security_check",
        entity_id=run_id if run_id else cursor.lastrowid,
        message="完成 HMAC-SHA256 完整性校验",
        detail={"passed": bool(security_result.get("passed"))},
    )
    return int(cursor.lastrowid)


def store_security_check(
    security_result: dict[str, Any],
    *,
    db_path: Path | str = DB_PATH,
) -> int:
    init_database(db_path)
    with open_database(db_path) as conn:
        return _insert_security_check(conn, run_id=None, security_result=security_result)


def store_ai_job(
    *,
    request: dict[str, Any],
    result: dict[str, Any],
    logs: list[str],
    db_path: Path | str = DB_PATH,
) -> int:
    init_database(db_path)
    with open_database(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO ai_jobs(
                created_at, scene, parent_a, parent_b, protocol_name, target_generation,
                dry_run, status, prompt_file, target_file, logs, result_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now_text(),
                request.get("scene"),
                request.get("parent_a"),
                request.get("parent_b"),
                request.get("protocol_name"),
                request.get("target_generation", "next"),
                int(bool(request.get("dry_run", True))),
                result.get("status", "unknown"),
                result.get("prompt_file"),
                result.get("target_file"),
                "\n".join(logs),
                _json(result),
            ),
        )
        job_id = int(cursor.lastrowid)
        _store_event(
            conn,
            event_type="ai_dry_run",
            entity_type="ai_job",
            entity_id=job_id,
            message=f"生成大模型协议优化任务 {request.get('protocol_name')}",
            detail={"status": result.get("status")},
        )
        return job_id


def store_design_case(
    case: dict[str, Any],
    *,
    db_path: Path | str = DB_PATH,
) -> int:
    init_database(db_path)
    requirement = case["requirement"]
    summary = case.get("summary", {})
    deployment = case.get("deployment", {})
    selected_protocol = summary.get("recommended_protocol") or "unknown"
    fallback_protocol = None
    gateways = deployment.get("gateways") or []
    if gateways:
        fallback_protocol = gateways[0].get("fallback_protocol")

    with open_database(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO design_cases(
                created_at, title, preset, requirement_text, recommended_protocol, recommended_score,
                grdl_json, ahp_json, playbook_json, validation_json, deployment_json, full_case_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now_text(),
                requirement.get("title"),
                requirement.get("preset"),
                requirement.get("requirement"),
                selected_protocol,
                summary.get("recommended_score"),
                _json(case.get("grdl", {})),
                _json(case.get("matches", [])),
                _json(case.get("playbook", [])),
                _json(case.get("validation", [])),
                _json(deployment),
                _json(case),
            ),
        )
        case_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO deployment_manifests(
                design_case_id, run_id, created_at, selected_protocol, fallback_protocol, manifest_json
            )
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (case_id, None, utc_now_text(), selected_protocol, fallback_protocol, _json(deployment)),
        )
        _store_event(
            conn,
            event_type="design_case",
            entity_type="design_case",
            entity_id=case_id,
            message=f"完成需求建模与协议匹配: {requirement.get('title')}",
            detail={"recommended_protocol": selected_protocol, "score": summary.get("recommended_score")},
        )
        return case_id


def list_design_cases(limit: int = 12, *, db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    init_database(db_path)
    with open_database(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, title, preset, recommended_protocol, recommended_score, requirement_text
            FROM design_cases
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_design_case(case_id: int, *, db_path: Path | str = DB_PATH) -> dict[str, Any] | None:
    init_database(db_path)
    with open_database(db_path) as conn:
        row = conn.execute("SELECT * FROM design_cases WHERE id = ?", (case_id,)).fetchone()
    if row is None:
        return None
    data = _row_to_dict(row)
    data["case"] = json.loads(data.pop("full_case_json"))
    for key in ("grdl_json", "ahp_json", "playbook_json", "validation_json", "deployment_json"):
        data.pop(key, None)
    return data


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _ai_job_row(row: sqlite3.Row) -> dict[str, Any]:
    data = _row_to_dict(row)
    result_json = data.pop("result_json", None)
    result: dict[str, Any] = {}
    if result_json:
        try:
            parsed = json.loads(result_json)
            if isinstance(parsed, dict):
                result = parsed
        except json.JSONDecodeError:
            result = {}
    data["task_profile"] = result.get("task_profile") or (result.get("validation") or {}).get("task_profile") or ""
    data["task_title"] = result.get("task_title") or (result.get("validation") or {}).get("task_title") or ""
    data["protocol_exists"] = bool(result.get("protocol_exists"))
    if data.get("target_file") and not data["protocol_exists"] and not bool(data.get("dry_run")):
        data["protocol_exists"] = Path(str(data["target_file"])).exists()
    if bool(data.get("dry_run")):
        data["protocol_exists"] = False
    return data


def list_runs(limit: int = 20, *, db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    init_database(db_path)
    with open_database(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, status, scene, protocol, devices, gateways, batches, num_tags,
                   price_min, price_max, total_windows, average_hit_rate,
                   avg_gateway_protocol_time_ms, alert_count, security_passed,
                   benchmark_count, wall_clock_s, output_dir, dashboard_path
            FROM runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_run_detail(run_id: int, *, db_path: Path | str = DB_PATH) -> dict[str, Any] | None:
    init_database(db_path)
    with open_database(db_path) as conn:
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            return None

        streams = conn.execute(
            "SELECT * FROM stream_snapshots WHERE run_id = ? ORDER BY position",
            (run_id,),
        ).fetchall()
        gateways = conn.execute(
            "SELECT * FROM gateway_summaries WHERE run_id = ? ORDER BY gateway_id",
            (run_id,),
        ).fetchall()
        alerts = conn.execute(
            "SELECT * FROM alerts WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
        security = conn.execute(
            "SELECT * FROM security_checks WHERE run_id = ? ORDER BY id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        benchmark = conn.execute(
            "SELECT * FROM benchmark_results WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()

    detail = _row_to_dict(run)
    detail["result"] = json.loads(detail.pop("result_json"))
    detail["stream_snapshots"] = [_row_to_dict(row) for row in streams]
    detail["gateway_summaries"] = [_row_to_dict(row) for row in gateways]
    detail["alerts"] = [_row_to_dict(row) for row in alerts]
    detail["security"] = _row_to_dict(security) if security else None
    if detail["security"]:
        detail["security"]["result"] = json.loads(detail["security"].pop("result_json"))
    detail["benchmark"] = [_row_to_dict(row) for row in benchmark]
    return detail


def list_topology(*, db_path: Path | str = DB_PATH) -> dict[str, Any]:
    init_database(db_path)
    with open_database(db_path) as conn:
        gateways = conn.execute("SELECT * FROM gateways ORDER BY gateway_id").fetchall()
        devices = conn.execute("SELECT * FROM devices ORDER BY device_id").fetchall()
    return {
        "gateways": [_row_to_dict(row) for row in gateways],
        "devices": [_row_to_dict(row) for row in devices],
    }


def database_stats(*, db_path: Path | str = DB_PATH) -> dict[str, Any]:
    init_database(db_path)
    with open_database(db_path) as conn:
        tables = [
            "runs",
            "devices",
            "gateways",
            "stream_snapshots",
            "gateway_summaries",
            "alerts",
            "security_checks",
            "benchmark_results",
            "ai_jobs",
            "design_cases",
            "deployment_manifests",
            "system_events",
        ]
        counts = {
            table: conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
            for table in tables
        }
        recent_events = conn.execute(
            """
            SELECT id, created_at, event_type, entity_type, entity_id, message
            FROM system_events
            ORDER BY id DESC
            LIMIT 12
            """
        ).fetchall()
        ai_jobs = conn.execute(
            """
            SELECT id, created_at, scene, parent_a, parent_b, protocol_name, target_generation,
                   dry_run, status, prompt_file, target_file, result_json
            FROM ai_jobs
            ORDER BY id DESC
            LIMIT 8
            """
        ).fetchall()
        design_cases = conn.execute(
            """
            SELECT id, created_at, title, preset, recommended_protocol, recommended_score
            FROM design_cases
            ORDER BY id DESC
            LIMIT 8
            """
        ).fetchall()
    return {
        "db_path": str(Path(db_path).resolve()),
        "counts": counts,
        "recent_events": [_row_to_dict(row) for row in recent_events],
        "ai_jobs": [_ai_job_row(row) for row in ai_jobs],
        "design_cases": [_row_to_dict(row) for row in design_cases],
    }

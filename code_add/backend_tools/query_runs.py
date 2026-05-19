from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend_tools.run_store import DEFAULT_DB_PATH, PROJECT_ROOT, get_run, list_runs


def main() -> int:
    parser = argparse.ArgumentParser(description="Query stored runs from SQLite.")
    parser.add_argument("--db", type=str, default=str(DEFAULT_DB_PATH), help="sqlite db path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list recent runs")
    p_list.add_argument("--limit", type=int, default=20)

    p_show = sub.add_parser("show", help="show one run")
    p_show.add_argument("run_id", type=int)

    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = (PROJECT_ROOT / db_path).resolve()

    if args.cmd == "list":
        rows = list_runs(limit=int(args.limit), db_path=db_path)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "show":
        run = get_run(run_id=int(args.run_id), db_path=db_path)
        print(json.dumps(run, ensure_ascii=False, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())

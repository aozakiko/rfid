"""Audit a benchmark output directory without rerunning the experiment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .provenance import audit_manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audit RFID benchmark artifact hashes and reproducibility key")
    parser.add_argument("root", nargs="?", default="Experiments/stat_protocols")
    args = parser.parse_args(argv)
    report = audit_manifest(Path(args.root))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())

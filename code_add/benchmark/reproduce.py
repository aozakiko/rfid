"""Replay one frozen config twice and compare data, outputs, failures and costs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .run_config import run_config
from .provenance import audit_manifest


def deterministic_rows(path):
    reports = json.loads(Path(path).read_text(encoding="utf-8"))["reports"]
    rows = []
    for report in reports:
        raw = report["run_result"]
        metadata = raw["metadata"]
        rows.append({k: report[k] for k in ("protocol", "task", "seed", "status", "failure_reason", "absolute_error", "relative_error", "precision", "recall", "f1", "jaccard", "communication_bits")} | {
            "truth": raw["truth"], "estimate": raw["estimate"],
            "counters": {k: raw[k] for k in ("reader_bits", "tag_bits", "slots", "frames", "rounds")},
            "scenario": {k: metadata.get(k) for k in ("distribution", "num_tags", "price_min", "price_max", "missing_rate", "noise_rate", "extreme_rate", "split", "dataset_hash", "observed_num_tags")},
        })
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))


def replay(config, output, *, reference_paths=()):
    output = Path(output)
    manifests = [run_config(config, output / name, reference_paths=reference_paths) for name in ("replay_1", "replay_2")]
    rows = [deterministic_rows(output / name / "results.json") for name in ("replay_1", "replay_2")]
    hashes = [hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest() for data in rows]
    audits = [audit_manifest(output / name) for name in ("replay_1", "replay_2")]
    report = {"passed": hashes[0] == hashes[1] and all(a["passed"] for a in audits) and manifests[0]["reproducibility_key"] == manifests[1]["reproducibility_key"],
        "compared_fields": "data hash, truth, estimate, seed, status, failure, metrics, communication counters; CPU/wall time excluded",
        "deterministic_hashes": hashes, "reproducibility_keys": [m["reproducibility_key"] for m in manifests],
        "row_counts": [len(data) for data in rows], "artifact_audits": audits}
    (output / "replay_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--reference", action="append", default=[])
    args = parser.parse_args()
    result = replay(args.config, args.output_dir, reference_paths=args.reference)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

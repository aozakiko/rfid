"""Check held-out data identities against explicitly supplied reference results."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def dataset_rows(reports):
    for report in reports:
        if hasattr(report, "run_result"):
            metadata, seed = report.run_result.metadata, report.seed
        else:
            metadata, seed = report.get("run_result", {}).get("metadata", {}), report.get("seed")
        yield {"dataset_hash": metadata.get("dataset_hash"), "split": metadata.get("split"), "seed": seed}


def read_references(paths):
    rows, hashes = [], {}
    for value in paths:
        path = Path(value).resolve()
        data = path.read_bytes()
        payload = json.loads(data)
        if not isinstance(payload, dict) or not isinstance(payload.get("reports"), list):
            raise ValueError(f"invalid reference reports: {path}")
        rows.extend(dataset_rows(payload["reports"]))
        hashes[str(path)] = hashlib.sha256(data).hexdigest()
    return rows, hashes


def audit_splits(reports, references=()):
    current = list(dataset_rows(reports))
    reference_rows = []
    for row in references:
        reference_rows.extend(dataset_rows([row]) if hasattr(row, "run_result") or "run_result" in row else [row])
    all_rows = current + reference_rows
    protected = [row for row in current if row["split"] in {"validation", "blind"}]
    if not protected:
        return {"passed": True, "status": "not_applicable", "overlaps": [], "protected_datasets": 0}
    unknown = sum(not row["dataset_hash"] for row in all_rows)
    collisions, tested = {}, set()
    reference_sets = {}
    for split in {row["split"] for row in protected}:
        # Validation must not reuse development/evaluation inputs. Blind data
        # must not reuse any data from a different split.
        candidates = [row for row in all_rows if row["split"] != split and (split == "blind" or row["split"] != "blind")]
        reference_sets[split] = candidates
        known = {row["dataset_hash"] for row in candidates if row["dataset_hash"]}
        for row in protected:
            if row["split"] != split:
                continue
            tested.add((split, row["dataset_hash"]))
            if row["dataset_hash"] in known:
                collisions[(split, row["dataset_hash"])] = {"split": split, "dataset_hash": row["dataset_hash"], "seed": row["seed"]}
    unavailable = unknown > 0 or any(not values for values in reference_sets.values())
    status = "failed" if collisions else "not_available" if unavailable else "passed"
    return {"passed": status == "passed", "status": status, "overlaps": list(collisions.values())[:50],
            "overlap_count": len(collisions), "protected_datasets": len(tested), "missing_hash_rows": unknown,
            "reference_datasets": {key: len({r["dataset_hash"] for r in values}) for key, values in reference_sets.items()},
            "scope": "dataset identity overlap against supplied recorded references; not proof about unrecorded use"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports")
    parser.add_argument("--reference", action="append", default=[])
    args = parser.parse_args()
    rows, _ = read_references(args.reference)
    payload = json.loads(Path(args.reports).read_text(encoding="utf-8"))
    result = audit_splits(payload["reports"], rows)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

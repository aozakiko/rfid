"""Rebuild comparable tables and figures exclusively from frozen raw results."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

from stat_protocols.contracts import EvaluationReport, RunResult
from .runner import summarize_reports

DIMENSIONS = ("task", "distribution", "num_tags", "missing_rate", "noise_rate", "extreme_rate", "split",
              "price_min", "price_max", "top_k", "enabled_tasks")


def read_reports(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [EvaluationReport(**{**{k: v for k, v in row.items() if k != "run_result"},
        "run_result": RunResult(**{k: v for k, v in row["run_result"].items() if k != "communication_bits"})})
        for row in data.get("reports", [])]


def _case(report):
    metadata = report.run_result.metadata
    return tuple(report.task if k == "task" else tuple(metadata.get(k, [])) if k == "enabled_tasks"
                 else metadata.get(k) for k in DIMENSIONS)


def comparison_rows(reports, axis="mean_communication_bits"):
    if axis not in {"mean_communication_bits", "mean_slots", "mean_rounds"}:
        raise ValueError("unsupported comparison axis")
    indexed = defaultdict(list)
    for report in reports:
        indexed[(report.protocol, _case(report))].append(report)
    rows, groups = [], defaultdict(list)
    for (protocol, case), values in sorted(indexed.items(), key=lambda item: repr(item[0])):
        row = summarize_reports(values)[0]
        row.update(zip(DIMENSIONS, case))
        reasons = []
        if row["success_rate"] < 1:
            reasons.append("execution_failed")
        if row.get("cost_status") == "missing":
            reasons.append("communication_cost_missing")
        if any(r.run_result.metadata.get("cost_origin") == "candidate_self_reported" for r in values):
            reasons.append("communication_cost_not_independently_measured")
        if row.get("mean_error") is None:
            reasons.append("error_unavailable")
        if any(r.run_result.metadata.get("validation_gate") == "rejected" for r in values):
            reasons.append("validation_failed")
        if len({r.seed for r in values}) != len(values):
            reasons.append("duplicate_seed")
        inputs = tuple(sorted((r.seed, str(r.run_result.metadata.get("dataset_hash", ""))) for r in values))
        if any(not digest or digest == "None" for _, digest in inputs):
            reasons.append("dataset_hash_missing")
        signature = json.dumps([case, inputs], separators=(",", ":"), sort_keys=True)
        row.update(pareto_group=hashlib.sha256(signature.encode()).hexdigest(),
                   pareto_eligible=not reasons, pareto_exclusion_reasons=reasons, is_pareto=False)
        row["failure_reasons"] = sorted({r.failure_reason for r in values if r.failure_reason})
        row["ci_sample_size"] = sum(r.status == "success" and (r.f1 is not None or r.relative_error is not None) for r in values)
        row["ci_scope"] = "successful_executions_only"
        row["ci_method"] = "student_t_95_independent_seeds"
        if row["ci_sample_size"] < 2 or "duplicate_seed" in reasons:
            row["error_ci95_low"] = row["error_ci95_high"] = None
            row["ci_method"] = "insufficient_independent_seeds"
        rows.append(row)
        groups[row["pareto_group"]].append(row)
    for candidates in groups.values():
        eligible = [r for r in candidates if r["pareto_eligible"]]
        for row in eligible:
            row["is_pareto"] = not any(other is not row and other[axis] <= row[axis] and other["mean_error"] <= row["mean_error"]
                and (other[axis] < row[axis] or other["mean_error"] < row["mean_error"]) for other in eligible)
    return rows


def _plot(rows, base):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    tasks = sorted({row["task"] for row in rows})
    cols = min(2, max(1, len(tasks)))
    height = max(1, math.ceil(len(tasks) / cols))
    fig, axes = plt.subplots(height, cols, figsize=(7 * cols, 4.8 * height),
                             constrained_layout=True, squeeze=False)
    protocols = sorted({row["protocol"] for row in rows})
    palette = {name: plt.get_cmap("tab20")(i % 20) for i, name in enumerate(protocols)}
    for ax, task in zip(axes.flat, tasks):
        values = [row for row in rows if row["task"] == task and row["pareto_eligible"]]
        for protocol in protocols:
            subset = [row for row in values if row["protocol"] == protocol]
            if subset:
                ax.scatter([r["mean_communication_bits"] for r in subset], [r["mean_error"] for r in subset],
                           color=palette[protocol], label=protocol, alpha=.75, s=28)
                if len({r["pareto_group"] for r in values}) == 1:
                    for row in subset:
                        if row["error_ci95_low"] is not None and row["error_ci95_high"] is not None:
                            ax.errorbar(row["mean_communication_bits"], row["mean_error"],
                                yerr=[[max(0, row["mean_error"] - row["error_ci95_low"])],
                                      [max(0, row["error_ci95_high"] - row["mean_error"])]],
                                color=palette[protocol], capsize=3, alpha=.6, fmt="none")
        fronts = [row for row in values if row["is_pareto"]]
        ax.scatter([r["mean_communication_bits"] for r in fronts], [r["mean_error"] for r in fronts],
                   marker="D", facecolors="none", edgecolors="black", s=75, label="Within-case Pareto")
        if len({r["pareto_group"] for r in values}) == 1:
            points = sorted(fronts, key=lambda r: r["mean_communication_bits"])
            ax.plot([r["mean_communication_bits"] for r in points], [r["mean_error"] for r in points],
                    color="black", linestyle="--", alpha=.45)
        ax.set(title=task, xlabel="Transmitted bits (simulation)",
               ylabel="1 - F1" if task == "top_k" else "Relative error")
        ax.grid(alpha=.15)
        if values:
            ax.legend(fontsize=7, loc="best")
        else:
            ax.text(.5, .5, "No eligible results", ha="center", transform=ax.transAxes)
    for ax in list(axes.flat)[len(tasks):]:
        ax.set_visible(False)
    fig.suptitle("Frozen raw results; within-scenario Pareto\n95% CI on successful seeds shown only for a single scenario", fontsize=11)
    generated = []
    for suffix in (".png", ".pdf"):
        target = base.with_suffix(suffix)
        fig.savefig(target, dpi=160)
        generated.append(str(target))
    plt.close(fig)
    return generated


def regenerate(root, output=None):
    """Never mutate the source manifest or raw artifacts; derive into a new dir."""
    root = Path(root).resolve()
    output = Path(output).resolve() if output else root.with_name(root.name + "_derived")
    if output == root or root in output.parents:
        raise ValueError("derived output must be outside the audited input directory")
    paths = sorted(root.glob("*/*_reports.json"))
    if (root / "results.json").exists():
        paths.append(root / "results.json")
    if not paths:
        raise ValueError("no raw benchmark reports found")
    output.mkdir(parents=True, exist_ok=True)
    generated, sources = [], {}
    for path in paths:
        sources[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        rows = comparison_rows(read_reports(path))
        base = output / (path.stem.replace("_reports", "") + "_evidence")
        for suffix in (".json", ".csv"):
            target = base.with_suffix(suffix)
            if suffix == ".json":
                target.write_text(json.dumps(rows, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
            else:
                with target.open("w", newline="", encoding="utf-8-sig") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["protocol"])
                    writer.writeheader()
                    writer.writerows({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict, tuple)) else v
                                      for k, v in row.items()} for row in rows)
            generated.append(str(target))
        generated.extend(_plot(rows, base))
    from .provenance import artifact_hashes
    from .provenance import source_hashes
    manifest = {"schema_version": "1.0", "input_root": str(root), "source_artifact_hashes": sources,
                "source_hashes": source_hashes(Path(__file__).resolve().parents[1]),
                "artifact_hashes": {k: v for k, v in artifact_hashes(output).items() if k != "derived_manifest.json"},
                "method": "independent seed Student-t CI; within-case Pareto; failed runs retained"}
    (output / "derived_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    generated.append(str(output / "derived_manifest.json"))
    return generated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    print(json.dumps({"generated": regenerate(args.root, args.output_dir)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

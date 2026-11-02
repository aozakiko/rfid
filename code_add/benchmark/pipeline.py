"""Frozen engineering pipeline: development, validation, stress, blind and replay."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from .evidence import regenerate
from .provenance import audit_manifest, source_hashes
from .reproduce import replay
from .run_config import ROOT, RunConfig, load_config, run_config


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def inventory(root):
    # Nested raw/derived/replay manifests are evidence too.
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and p != root / "pipeline_manifest.json"}


def seed_groups(config, validation_seeds=None, blind_seeds=None):
    development = list(config.seeds)
    validation = list(validation_seeds) if validation_seeds is not None else [max(development) + 1000 + i for i in range(len(development))]
    blind = list(blind_seeds) if blind_seeds is not None else [max(development + validation) + 1000 + i for i in range(len(development))]
    for values in (validation, blind):
        RunConfig(**{**config.to_dict(), "seeds": values})
    if set(development) & set(validation) or set(development) & set(blind) or set(validation) & set(blind):
        raise ValueError("development, validation and blind seeds must be disjoint")
    return development, validation, blind


def run_pipeline(config_path, output, *, validation_seeds=None, blind_seeds=None, reference_paths=(), progress=print):
    config = load_config(config_path)
    development, validation, blind = seed_groups(config, validation_seeds, blind_seeds)
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("pipeline output must be empty; select a new directory")
    output.mkdir(parents=True, exist_ok=True)
    sources = source_hashes(ROOT)
    manifest = {"schema_version": "1.0", "status": "running", "source_hashes": sources,
        "seed_groups": {"development": development, "validation": validation, "blind": blind},
        "scope": "engineering regression with frozen protocol parameters; no LLM calls or parameter selection",
        "stages": [], "replay": None}
    manifest_path = output / "pipeline_manifest.json"
    write_json(manifest_path, manifest)
    references = [Path(p).resolve() for p in reference_paths]
    plans = [
        ("development", development, "development", {}),
        ("validation", validation, "validation", {}),
        ("missing", development, "evaluation", {"missing_rate": max(.1, config.missing_rate)}),
        ("noise", development, "evaluation", {"noise_rate": max(.1, config.noise_rate)}),
        ("extreme", development, "evaluation", {"extreme_rate": max(.1, config.extreme_rate)}),
        ("blind", blind, "blind", {}),
    ]
    try:
        with zipfile.ZipFile(output / "source_snapshot.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for name, expected in sources.items():
                data = (ROOT / name).read_bytes()
                if hashlib.sha256(data).hexdigest() != expected:
                    raise RuntimeError("source changed while snapshotting")
                archive.writestr(name, data)
        for name, seeds, split, rates in plans:
            progress(f"Running stage: {name}", flush=True) if progress is print else progress(f"Running stage: {name}")
            folder = output / name
            folder.mkdir()
            frozen = {**config.to_dict(), "name": f"{config.name}-{name}", "seeds": seeds,
                      "split": split, "output_dir": str(folder / "raw"), **rates}
            stage_config = folder / "config.json"
            write_json(stage_config, frozen)
            supplied = list(references) if split in {"validation", "blind"} else []
            def reference_name(path):
                try:
                    return path.relative_to(output).as_posix()
                except ValueError:
                    return str(path)
            record = {"name": name, "status": "running", "config_path": str(stage_config.relative_to(output)),
                      "reference_paths": [reference_name(p) for p in supplied]}
            manifest["stages"].append(record)
            write_json(manifest_path, manifest)
            raw = run_config(stage_config, folder / "raw", reference_paths=supplied)
            audit = audit_manifest(folder / "raw")
            if not audit["passed"]:
                raise RuntimeError(f"{name} raw integrity audit failed")
            regenerate(folder / "raw", folder / "derived")
            record.update(status="completed", split_audit=raw["split_audit"],
                          run_count=raw["run_count"], reproducibility_key=raw["reproducibility_key"])
            if name == "blind":
                progress("Running deterministic blind replay")
                manifest["replay"] = replay(stage_config, output / "replay", reference_paths=supplied)
                if not manifest["replay"]["passed"]:
                    raise RuntimeError("blind deterministic replay failed")
            references.append(folder / "raw" / "results.json")
            write_json(manifest_path, manifest)
        if source_hashes(ROOT) != sources:
            raise RuntimeError("source files changed during pipeline execution")
        manifest["status"] = "completed"
    except Exception as exc:
        manifest.update(status="failed", error_type=type(exc).__name__)
        if manifest["stages"] and manifest["stages"][-1]["status"] == "running":
            manifest["stages"][-1]["status"] = "failed"
        raise
    finally:
        manifest["artifact_hashes"] = inventory(output)
        write_json(manifest_path, manifest)
    progress("Pipeline completed: all stages, split audits and blind replay passed")
    return manifest


def audit_pipeline(output):
    output = Path(output).resolve()
    manifest = json.loads((output / "pipeline_manifest.json").read_text(encoding="utf-8"))
    expected, actual = manifest.get("artifact_hashes", {}), inventory(output)
    stages = {s["name"] for s in manifest.get("stages", []) if s.get("status") == "completed"}
    complete = stages == {"development", "validation", "missing", "noise", "extreme", "blind"}
    return {"passed": manifest.get("status") == "completed" and complete and bool(expected) and expected == actual
            and manifest.get("replay", {}).get("passed") is True,
            "status": manifest.get("status"), "artifact_count": len(actual),
            "missing": sorted(set(expected) - set(actual)), "unexpected": sorted(set(actual) - set(expected)),
            "mismatched": sorted(k for k in expected.keys() & actual.keys() if expected[k] != actual[k])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--validation-seeds", help="comma-separated independent seeds")
    parser.add_argument("--blind-seeds", help="comma-separated independent seeds")
    parser.add_argument("--reference", action="append", default=[], help="recorded results from earlier experiments")
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    if not args.audit:
        if not args.config:
            parser.error("--config is required for execution")
        parse = lambda value: [int(v) for v in value.split(",")] if value else None
        run_pipeline(args.config, args.output_dir, validation_seeds=parse(args.validation_seeds), blind_seeds=parse(args.blind_seeds), reference_paths=args.reference)
    result = audit_pipeline(args.output_dir)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

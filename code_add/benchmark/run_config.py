"""Configuration-driven benchmark runner with reproducible provenance."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import math
import os
import platform
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from stat_protocols.contracts import TaskSpec
from .runner import run_benchmark, summarize_cases, write_results


ROOT = Path(__file__).resolve().parents[1]
CONFIG_FIELDS = (
    "name",
    "profiles",
    "protocols",
    "distributions",
    "sizes",
    "seeds",
    "output_dir",
    "price_min",
    "price_max",
    "missing_rate",
    "noise_rate",
    "extreme_rate",
    "split",
)
CANONICAL_PROFILE_TASKS = {
    "mean_estimation": "mean",
    "variance_estimation": "variance",
    "cardinality_estimation": "cardinality",
    "top_k": "top_k",
}
PROFILE_ALIASES = {
    "mean": "mean",
    "variance": "variance",
    "cardinality": "cardinality",
    "mean_estimation": "mean",
    "variance_estimation": "variance",
    "cardinality_estimation": "cardinality",
    "median_quantile": "top_k",
}
PROFILE_ID_ALIASES = {
    "mean": "mean_estimation",
    "variance": "variance_estimation",
    "cardinality": "cardinality_estimation",
    "legacy": "mode_query",
    "legacy_mode_query": "mode_query",
    "custom": "custom_task",
    "custom_text": "custom_task",
}
NON_BENCHMARK_PROFILES = {
    "legacy",
    "legacy_mode_query",
    "mode_query",
    "median_quantile",
    "missing_stocktaking",
    "custom",
    "custom_task",
    "custom_text",
}
SUPPORTED_PROFILES = set(CANONICAL_PROFILE_TASKS) | set(PROFILE_ALIASES) | set(PROFILE_ID_ALIASES) | NON_BENCHMARK_PROFILES
SUPPORTED_DISTRIBUTIONS = {
    "single_peak",
    "multi_peak",
    "gaussian",
    "long_tail",
    "power_law",
    "uniform",
}
SUPPORTED_PROTOCOLS = {
    "polling",
    "full_identification",
    "single_mean",
    "single_variance",
    "single_cardinality",
    "single_top_k",
    "single_serial",
    "shared_sketch",
    "random_sketch_control",
    "llm_random",
    "fixed_2",
    "fixed_3",
    "fixed_4",
    "adaptive",
    "adaptive_t05",
    "adaptive_t20",
}
SUPPORTED_SPLITS = {"development", "validation", "evaluation", "blind", "test"}
CANONICAL_TASK_ORDER = ("mean", "variance", "cardinality", "top_k")


@dataclass
class RunConfig:
    """All inputs needed to run one reproducible benchmark configuration."""

    name: str
    profiles: list[str]
    protocols: list[str]
    distributions: list[str]
    sizes: list[int]
    seeds: list[int]
    output_dir: str
    price_min: int
    price_max: int
    missing_rate: float = 0.0
    noise_rate: float = 0.0
    extreme_rate: float = 0.0
    split: str = "evaluation"

    def __post_init__(self) -> None:
        _validate_config(self)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_int(value: Any) -> bool:
    return type(value) is int


def _is_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value))


def _validate_str_list(value: Any, field: str, allowed: set[str] | None = None) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty JSON array")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must contain non-empty strings")
    if len({item.strip() for item in value}) != len(value):
        raise ValueError(f"{field} must not contain duplicates")
    if allowed is not None:
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"unsupported {field}: {', '.join(unknown)}")


def _validate_int_list(value: Any, field: str, *, minimum: int, maximum: int | None = None) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty JSON array")
    if any(not _is_int(item) or item < minimum or (maximum is not None and item > maximum) for item in value):
        limit = f"[{minimum}, {maximum}]" if maximum is not None else f">= {minimum}"
        raise ValueError(f"{field} values must be integers {limit}")
    if len(set(value)) != len(value):
        raise ValueError(f"{field} must not contain duplicates")


def _validate_config(config: RunConfig) -> None:
    if not isinstance(config.name, str) or not config.name.strip():
        raise ValueError("name must be a non-empty string")
    _validate_str_list(config.profiles, "profiles", SUPPORTED_PROFILES)
    _validate_str_list(config.protocols, "protocols", SUPPORTED_PROTOCOLS)
    _validate_str_list(config.distributions, "distributions", SUPPORTED_DISTRIBUTIONS)
    _validate_int_list(config.sizes, "sizes", minimum=1, maximum=200000)
    _validate_int_list(config.seeds, "seeds", minimum=0)
    if not isinstance(config.output_dir, str) or not config.output_dir.strip():
        raise ValueError("output_dir must be a non-empty string")
    if not _is_int(config.price_min) or not _is_int(config.price_max):
        raise ValueError("price_min and price_max must be integers")
    if config.price_min < 0 or config.price_max >= 2**31 or config.price_min > config.price_max:
        raise ValueError("invalid price bounds")
    if config.price_max - config.price_min + 1 > 10000:
        raise ValueError("price domain exceeds 10000 bins")
    for field in ("missing_rate", "noise_rate", "extreme_rate"):
        value = getattr(config, field)
        if not _is_number(value) or not 0 <= float(value) <= 1:
            raise ValueError(f"{field} must be a finite number in [0, 1]")
    if not isinstance(config.split, str) or config.split not in SUPPORTED_SPLITS:
        raise ValueError(f"unsupported split: {config.split!r}")


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_config(path: str | os.PathLike[str]) -> RunConfig:
    """Load a JSON config and reject malformed or unknown input."""
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"config does not exist: {config_path}")
    try:
        raw = config_path.read_bytes()
        data = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON config: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("config root must be a JSON object")
    unknown = sorted(set(data) - set(CONFIG_FIELDS))
    missing = sorted(set(CONFIG_FIELDS) - set(data))
    if unknown:
        raise ValueError(f"unknown config fields: {', '.join(unknown)}")
    if missing:
        raise ValueError(f"missing config fields: {', '.join(missing)}")
    return RunConfig(**data)


def resolve_canonical_tasks(profiles: Sequence[str]) -> Any:
    """Call the project task registry, with a strict built-in fallback.

    The external `generic_tasks.resolve_canonical_tasks` remains the source of
    truth when the generic task package is installed. The fallback only knows
    the four statistical contracts already supported by `TaskSpec`, so legacy
    and custom profiles are reported as unsupported instead of being scored.
    """
    try:
        module = importlib.import_module("generic_tasks")
    except ModuleNotFoundError:
        return [CANONICAL_PROFILE_TASKS[p] for p in profiles if p in CANONICAL_PROFILE_TASKS]
    resolver = getattr(module, "resolve_canonical_tasks", None)
    if not callable(resolver):
        raise RuntimeError("generic_tasks.resolve_canonical_tasks is not callable")
    return resolver([PROFILE_ID_ALIASES.get(profile, profile) for profile in profiles])


def _flatten_task_names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [PROFILE_ALIASES.get(value, value)]
    if isinstance(value, Mapping):
        value = value.get("tasks", value.get("canonical_tasks", value.get("task", [])))
        return _flatten_task_names(value)
    if not isinstance(value, Iterable) or isinstance(value, (bytes, bytearray, dict)):
        raise ValueError("resolve_canonical_tasks returned an invalid task value")
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            result.append(PROFILE_ALIASES.get(item, item))
        else:
            result.extend(_flatten_task_names(item))
    return result

def _resolve_profile_tasks(profiles: Sequence[str], resolved: Any) -> tuple[list[str], dict[str, list[str]]]:
    by_profile: dict[str, list[str]] = {profile: [] for profile in profiles}
    if isinstance(resolved, Mapping):
        source: Mapping[str, Any] = resolved.get("profiles", resolved) if "profiles" in resolved else resolved
        for profile in profiles:
            if profile in source:
                by_profile[profile] = [task for task in _flatten_task_names(source[profile]) if task in CANONICAL_TASK_ORDER]
        if not any(by_profile.values()):
            for task in _flatten_task_names(resolved.get("tasks", [])):
                for profile in profiles:
                    if CANONICAL_PROFILE_TASKS.get(profile) == task:
                        by_profile[profile] = [task]
    elif isinstance(resolved, tuple) and len(resolved) == 2:
        tasks = _flatten_task_names(resolved[0])
        for profile in profiles:
            task = CANONICAL_PROFILE_TASKS.get(profile, PROFILE_ALIASES.get(profile))
            if task in tasks:
                by_profile[profile] = [task]
    else:
        tasks = _flatten_task_names(resolved)
        for profile in profiles:
            task = CANONICAL_PROFILE_TASKS.get(profile, PROFILE_ALIASES.get(profile))
            if task in tasks:
                by_profile[profile] = [task]
    for profile in profiles:
        if profile in NON_BENCHMARK_PROFILES:
            by_profile[profile] = []
        by_profile[profile] = list(dict.fromkeys(task for task in by_profile[profile] if task in CANONICAL_TASK_ORDER))
    canonical_tasks = [task for task in CANONICAL_TASK_ORDER if any(task in values for values in by_profile.values())]
    return canonical_tasks, by_profile


def _git_state() -> tuple[str | None, bool]:
    repo = ROOT.parent
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
        ).strip()
        dirty = bool(subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain"],
            text=True,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
        ).strip())
        return commit or None, dirty
    except (OSError, subprocess.CalledProcessError):
        return None, False


def _package_versions() -> dict[str, str]:
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover - Python 3.10 always has this.
        return {}
    versions: dict[str, str] = {}
    for name in ("numpy", "pandas", "matplotlib"):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return versions


def _display_path(path: str | os.PathLike[str]) -> str:
    value = Path(path)
    try:
        return value.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return value.as_posix()


def _command_record(command: Any, config_path: Path) -> Any:
    if command is None:
        return ["python", "-m", "benchmark.run_config", "--config", _display_path(config_path)]
    if isinstance(command, str):
        return command
    if isinstance(command, Sequence) and all(isinstance(item, str) for item in command):
        return list(command)
    raise ValueError("command must be a string or a sequence of strings")


def _write_csv(path: Path, rows: list[dict[str, Any]], fallback_fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = list(fallback_fields)
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _profile_support_payload(config: RunConfig, canonical_tasks: list[str], by_profile: dict[str, list[str]], run_id: str) -> dict[str, Any]:
    entries = []
    for profile in config.profiles:
        tasks = by_profile.get(profile, [])
        benchmarkable = bool(tasks) and profile not in NON_BENCHMARK_PROFILES
        entries.append({
            "profile": profile,
            "benchmarkable": benchmarkable,
            "status": "benchmarkable" if benchmarkable else "not_benchmarkable",
            "canonical_tasks": tasks,
            "accuracy": None if not benchmarkable else "measured_in_results_json",
            "accuracy_policy": "measured_by_benchmark" if benchmarkable else "not_available",
            "reason": None if benchmarkable else "legacy/custom profiles cannot be mapped to a statistical truth contract; no accuracy is inferred",
        })
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "resolver": "generic_tasks.resolve_canonical_tasks",
        "canonical_tasks": canonical_tasks,
        "profiles": entries,
    }


def _make_spec(config: RunConfig, canonical_tasks: list[str], seed: int) -> TaskSpec:
    return TaskSpec(
        task=canonical_tasks[0],
        tasks=tuple(canonical_tasks),
        distribution=config.distributions[0],
        num_tags=config.sizes[0],
        price_min=config.price_min,
        price_max=config.price_max,
        top_k=min(3, config.price_max - config.price_min + 1),
        missing_rate=config.missing_rate,
        noise_rate=config.noise_rate,
        extreme_rate=config.extreme_rate,
        seed=seed,
        split=config.split,
    )


def run_config(path: str | os.PathLike[str], output_override: str | os.PathLike[str] | None = None, command: Any = None) -> dict[str, Any]:
    """Run one config and write results, summary, support and manifest files."""
    config_path = Path(path)
    config = load_config(config_path)
    output_dir = Path(output_override).expanduser() if output_override is not None else Path(config.output_dir).expanduser()
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    run_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()
    start_commit, git_dirty = _git_state()
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": run_id,
        "config_hash": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "command": _command_record(command, config_path),
        "start_commit": start_commit,
        "experiment_generation_commit": os.getenv("RFID_EXPERIMENT_GENERATION_COMMIT") or (start_commit if not git_dirty else None),
        "release_commit": os.getenv("RFID_RELEASE_COMMIT") or (start_commit if not git_dirty else None),
        "git_dirty": git_dirty,
        "python_version": platform.python_version(),
        "package_versions": _package_versions(),
        "profiles": list(config.profiles),
        "protocols": list(config.protocols),
        "distributions": list(config.distributions),
        "sizes": list(config.sizes),
        "seeds": list(config.seeds),
        "started_at": started_at,
        "finished_at": None,
        "status": "running",
        "output_files": ["results.json", "summary.csv", "profile_support.json", "manifest.json"],
    }
    support_path = output_dir / "profile_support.json"
    results_path = output_dir / "results.json"
    summary_path = output_dir / "summary.csv"
    manifest_path = output_dir / "manifest.json"
    try:
        resolved = resolve_canonical_tasks(config.profiles)
        canonical_tasks, by_profile = _resolve_profile_tasks(config.profiles, resolved)
        support = _profile_support_payload(config, canonical_tasks, by_profile, run_id)
        support_path.write_text(json.dumps(support, ensure_ascii=False, indent=2), encoding="utf-8")

        reports = []
        if canonical_tasks:
            spec = _make_spec(config, canonical_tasks, config.seeds[0])
            reports = run_benchmark(
                spec,
                seeds=config.seeds,
                sizes=config.sizes,
                distributions=config.distributions,
                protocol_names=config.protocols,
            )
        write_results(reports, results_path, summary_path)
        case_rows = summarize_cases(reports)
        _write_csv(
            summary_path,
            case_rows,
            ("distribution", "num_tags", "missing_rate", "noise_rate", "extreme_rate", "split", "protocol", "task"),
        )
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        manifest["status"] = "success"
    except Exception as exc:
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        manifest["status"] = "failed"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a JSON-configured RFID benchmark")
    parser.add_argument("--config", required=True, help="path to a benchmark JSON config")
    parser.add_argument("--output-dir", default=None, help="override the config output directory")
    args = parser.parse_args(argv)
    try:
        run_config(args.config, output_override=args.output_dir, command=["python", "-m", "benchmark.run_config", "--config", _display_path(args.config)] + (["--output-dir", args.output_dir] if args.output_dir else []))
    except Exception as exc:
        print(f"benchmark config failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

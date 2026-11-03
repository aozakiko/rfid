"""Reproducible sensitivity sweeps through the shared candidate sandbox."""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import math
from pathlib import Path
import re

import numpy as np

from base import Tag
from course_project.candidate_evaluation import evaluate_protocol, save_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALID_FACTORS = {"dominance", "scale", "density"}


@dataclass(frozen=True)
class ProtocolSource:
    path: Path
    name: str
    source: str


def discover_valid_sources():
    return {"base_protocol"} | {p.name for p in PROJECT_ROOT.iterdir()
        if p.is_dir() and (p / "Task.py").is_file()}


def load_protocol(protocol_name, source):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", protocol_name) or source not in discover_valid_sources():
        raise ValueError("invalid protocol or source")
    root = PROJECT_ROOT / source
    generations = sorted((p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"generation_?\d+", p.name)),
                         key=lambda p: int(re.search(r"\d+$", p.name).group()), reverse=True)
    path = next((p for p in [root / (protocol_name + ".py"), *(g / (protocol_name + ".py") for g in generations)] if p.is_file()), None)
    if path is None:
        raise FileNotFoundError(protocol_name)
    return ProtocolSource(path, protocol_name, source)


def parse_protocol_specs(text):
    rows = []
    for part in text.split(","):
        name, separator, source = part.strip().partition(":")
        if not separator:
            raise ValueError("protocol spec must be name:source")
        load_protocol(name, source)  # Resolve files, never import candidate code.
        rows.append({"name": name, "source": source})
    return rows


def parse_factors(text):
    factors = list(dict.fromkeys(part.strip() for part in text.split(",")))
    if not factors or any(factor not in VALID_FACTORS for factor in factors):
        raise ValueError("unsupported sensitivity factor")
    return factors


def generate_tags_for_dominance_test(num_tags, price_min, price_max, dominance_ratio):
    prices = np.random.randint(price_min, price_max + 1, num_tags)
    if num_tags:
        prices[:int(num_tags * dominance_ratio)] = np.random.randint(price_min, price_max + 1)
        np.random.shuffle(prices)
    return [Tag(i, int(price)) for i, price in enumerate(prices)], {}


def generate_tags_single_peak(num_tags, price_min, price_max):
    return generate_tags_for_dominance_test(num_tags, price_min, price_max, .2)


def run_sensitivity_test_for_protocol(protocol, protocol_name, factor_name, factor_values, test_config):
    if not isinstance(protocol, ProtocolSource):
        raise TypeError("sensitivity requires a file source; in-process candidate execution is disabled")
    results = []
    for index, value in enumerate(factor_values):
        config = {key: test_config[key] for key in ("num_tags", "price_min", "price_max", "total_runs")}
        if factor_name == "scale":
            config["num_tags"] = int(value)
        if factor_name == "density":
            config["price_max"] = int(value)
        ratio = float(value) if factor_name == "dominance" else .2
        def generator(n, low, high):
            return generate_tags_for_dominance_test(n, low, high, ratio)
        config.update(task_profile=test_config.get("task_profile", "mode_query"),
                      security_mode=test_config.get("security_mode", "strict"),
                      seed=test_config.get("seed", 2000) + sorted(VALID_FACTORS).index(factor_name) * 100000 + index * 200,
                      timeout_seconds=test_config.get("timeout_seconds", 10))
        item = evaluate_protocol(protocol.path, protocol.name, generator, config)
        summary, report = item["summary"], item["validation"]
        hits = sum(row.get("semantic_hit") is True for row in report["runs"])
        n = config["total_runs"]
        p = hits / n
        z = 1.959963984540054
        center = (p + z*z/(2*n)) / (1 + z*z/n)
        half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1+z*z/n)
        times = [row["measured_wall_time_ms"] for row in report["runs"]
                 if row["execution_succeeded"] and row["measured_wall_time_ms"] is not None]
        results.append({"factor_name": factor_name, "factor_value": value, "protocol_name": protocol_name,
            "avg_time_s": sum(times) / len(times) / 1000 if times else None,
            "timing_scope": "worker_measured_wall_excluding_startup_not_RF",
            "semantic_pass_rate": p if summary["semantic_available"] else None,
            "pass_rate_ci_low": max(0., center-half) if summary["semantic_available"] else None,
            "pass_rate_ci_high": min(1., center+half) if summary["semantic_available"] else None,
            "completed_runs": report["completed_runs"], "requested_runs": n,
            "final_decision": report["final_decision"], "security_mode": config["security_mode"],
            "communication_cost_ms": summary["mean_communication_cost_ms"],
            "channel_model_id": summary["channel_model_id"], "source_sha256": summary["source_sha256"],
            "seed": config["seed"], "evidence": item})
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocols", default="g10_4:single_mode,g8_3:multi_mode")
    parser.add_argument("--factors", default="density")
    parser.add_argument("--num-tags", type=int, default=2000)
    parser.add_argument("--price-min", type=int, default=1)
    parser.add_argument("--price-max", type=int, default=1000)
    parser.add_argument("--total-runs", type=int, default=3)
    parser.add_argument("--point-count", type=int, default=3)
    parser.add_argument("--density-max", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=2000)
    parser.add_argument("--task-profile", default="mode_query")
    parser.add_argument("--security-mode", choices=("strict", "legacy"), default="strict")
    parser.add_argument("--output", default="Experiments/sensitivity_results_web.csv")
    args = parser.parse_args(argv)
    if not 1 <= args.total_runs <= 200 or not 2 <= args.point_count <= 100 or not 1 <= args.num_tags <= 200000:
        parser.error("runs/point-count/population outside supported limits")
    if not 0 <= args.seed or not 0 <= args.price_min < args.price_max < 2**31 or args.density_max < args.price_min:
        parser.error("invalid seed or attribute bounds")
    specs, factors = parse_protocol_specs(args.protocols), parse_factors(args.factors)
    rows = []
    for factor in factors:
        values = {"dominance": np.linspace(.05, .5, args.point_count),
                  "scale": np.geomspace(max(20, args.num_tags // 2), min(200000, args.num_tags * 2), args.point_count).astype(int),
                  "density": np.geomspace(max(args.price_min + 1, min(1000, args.density_max)), args.density_max, args.point_count).astype(int)}[factor].tolist()
        for spec in specs:
            protocol = load_protocol(spec["name"], spec["source"])
            config = {"num_tags": args.num_tags, "price_min": args.price_min, "price_max": args.price_max,
                      "total_runs": args.total_runs, "task_profile": args.task_profile,
                      "security_mode": args.security_mode, "seed": args.seed}
            rows.extend(run_sensitivity_test_for_protocol(protocol, protocol.name, factor, values, config))
    path = Path(args.output)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path.with_suffix(".evidence.json"), {"schema_version": "2.0", "rows": rows})
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=[key for key in rows[0] if key != "evidence"])
        writer.writeheader()
        writer.writerows({key: value for key, value in row.items() if key != "evidence"} for row in rows)
    print("Sensitivity CSV and per-run evidence saved:", path)
    return 0

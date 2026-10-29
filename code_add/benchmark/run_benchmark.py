"""CLI: python -m benchmark.run_benchmark --output-dir Experiments/benchmarks"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from stat_protocols.contracts import TaskSpec
from .experiments import pareto_front
from .experiments import MAIN_PROTOCOLS, ABLATION_PROTOCOLS
from .runner import run_benchmark, write_results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run seeded RFID statistical protocol benchmarks")
    parser.add_argument("--mode", choices=("main", "ablation"), default="main")
    parser.add_argument("--seeds", default=",".join(str(i) for i in range(20)), help="comma-separated seeds")
    parser.add_argument("--sizes", default="1000,10000", help="comma-separated tag scales")
    parser.add_argument("--distributions", default="single_peak,multi_peak,gaussian,long_tail,uniform")
    parser.add_argument("--output-dir", default="Experiments/benchmarks")
    args = parser.parse_args()
    spec = TaskSpec(tasks=("mean", "variance", "cardinality", "top_k"), top_k=3)
    seeds = [int(x) for x in args.seeds.split(",") if x.strip()]
    sizes = [int(x) for x in args.sizes.split(",") if x.strip()]
    distributions = [x.strip() for x in args.distributions.split(",") if x.strip()]
    protocol_names = (
        MAIN_PROTOCOLS
        if args.mode == "main"
        else ABLATION_PROTOCOLS
    )
    reports = run_benchmark(spec, seeds=seeds, sizes=sizes, distributions=distributions, protocol_names=protocol_names)
    output = Path(args.output_dir)
    write_results(reports, output / f"{args.mode}_reports.json", output / f"{args.mode}_summary.csv")
    front = pareto_front(reports)
    (output / f"{args.mode}_pareto.json").write_text(json.dumps(front, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"reports={len(reports)} output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

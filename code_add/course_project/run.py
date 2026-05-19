from __future__ import annotations

import argparse
import time
from pathlib import Path

from cloud_edge_demo.pipeline import run_distributed_demo
from course_project.artifacts import export_course_result, make_run_dir
from course_project.benchmark import run_scalability_benchmark
from course_project.dashboard import generate_dashboard
from course_project.database import store_course_run
from course_project.security import run_security_check


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the course-ready RFID cloud-edge-device IoT project."
    )
    parser.add_argument("--scene", default="multi_mode", help="Existing RFID scene name.")
    parser.add_argument("--protocol", default="g2_2", help="Protocol class/file name.")
    parser.add_argument("--devices", type=int, default=4, help="Number of RFID reader devices.")
    parser.add_argument("--gateways", type=int, default=2, help="Number of edge gateways.")
    parser.add_argument("--batches", type=int, default=3, help="Batches captured by each device.")
    parser.add_argument("--num-tags", type=int, default=80, help="RFID tags per device batch.")
    parser.add_argument("--price-min", type=int, default=1, help="Minimum price value.")
    parser.add_argument("--price-max", type=int, default=200, help="Maximum price value.")
    parser.add_argument("--stream-window", type=int, default=4, help="Cloud stream window size.")
    parser.add_argument("--shared-secret", default="rfid-course-demo-secret", help="HMAC shared secret.")
    parser.add_argument("--output-dir", default=None, help="Directory for generated artifacts.")
    parser.add_argument("--skip-security", action="store_true", help="Skip HMAC tamper test.")
    parser.add_argument("--benchmark", action="store_true", help="Run a small scalability benchmark.")
    parser.add_argument("--no-dashboard", action="store_true", help="Do not generate dashboard.html.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = make_run_dir(args.output_dir)
    started = time.perf_counter()

    result = run_distributed_demo(
        scene=args.scene,
        protocol_name=args.protocol,
        device_count=max(1, args.devices),
        gateway_count=max(1, args.gateways),
        batch_count=max(1, args.batches),
        num_tags=max(20, args.num_tags),
        price_min=args.price_min,
        price_max=args.price_max,
        stream_window_size=max(1, args.stream_window),
        shared_secret=args.shared_secret,
    )

    security_result = None
    if not args.skip_security:
        security_result = run_security_check(args.shared_secret)

    benchmark_rows = None
    if args.benchmark:
        benchmark_rows = run_scalability_benchmark(
            scene=args.scene,
            protocol_name=args.protocol,
            num_tags=max(20, min(args.num_tags, 50)),
            price_min=args.price_min,
            price_max=min(args.price_max, 120),
            stream_window_size=max(1, args.stream_window),
            shared_secret=args.shared_secret,
        )

    paths = export_course_result(result, output_dir, security_result, benchmark_rows)

    if not args.no_dashboard:
        paths["dashboard"] = generate_dashboard(
            result,
            Path(output_dir) / "dashboard.html",
            security_result=security_result,
            benchmark_rows=benchmark_rows,
        )

    run_id = store_course_run(
        config={
            "scene": args.scene,
            "protocol": args.protocol,
            "devices": max(1, args.devices),
            "gateways": max(1, args.gateways),
            "batches": max(1, args.batches),
            "num_tags": max(20, args.num_tags),
            "price_min": args.price_min,
            "price_max": args.price_max,
            "stream_window": max(1, args.stream_window),
            "shared_secret": args.shared_secret,
        },
        result=result,
        security_result=security_result,
        benchmark_rows=benchmark_rows,
        output_dir=Path(output_dir),
        dashboard_path=paths.get("dashboard"),
        wall_clock_s=round(time.perf_counter() - started, 3),
    )

    print("Course project run completed.")
    print(f"Database run id: {run_id}")
    print(f"Output directory: {output_dir.resolve()}")
    for name, path in paths.items():
        print(f"- {name}: {Path(path).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import time
from statistics import mean
from typing import Any

from cloud_edge_demo.pipeline import run_distributed_demo


DEFAULT_BENCHMARK_CONFIGS = [
    {"devices": 2, "gateways": 1, "batches": 1},
    {"devices": 2, "gateways": 2, "batches": 1},
    {"devices": 4, "gateways": 2, "batches": 1},
]


def _avg_gateway_protocol_time(batch_report: dict[str, Any]) -> float:
    values = [
        float(item.get("avg_protocol_time_ms", 0.0))
        for item in batch_report.get("gateway_summary", {}).values()
    ]
    return round(mean(values), 2) if values else 0.0


def run_scalability_benchmark(
    *,
    scene: str,
    protocol_name: str,
    configs: list[dict[str, int]] | None = None,
    num_tags: int = 40,
    price_min: int = 1,
    price_max: int = 120,
    stream_window_size: int = 4,
    shared_secret: str = "rfid-course-demo-secret",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for config in configs or DEFAULT_BENCHMARK_CONFIGS:
        devices = max(1, int(config["devices"]))
        gateways = max(1, int(config["gateways"]))
        batches = max(1, int(config["batches"]))

        start = time.perf_counter()
        result = run_distributed_demo(
            scene=scene,
            protocol_name=protocol_name,
            device_count=devices,
            gateway_count=gateways,
            batch_count=batches,
            num_tags=max(20, int(num_tags)),
            price_min=price_min,
            price_max=price_max,
            stream_window_size=stream_window_size,
            shared_secret=shared_secret,
        )
        wall_clock_s = round(time.perf_counter() - start, 3)
        report = result["batch_report"]
        rows.append(
            {
                "devices": devices,
                "gateways": gateways,
                "batches": batches,
                "num_tags": max(20, int(num_tags)),
                "price_min": price_min,
                "price_max": price_max,
                "total_windows": report.get("total_windows"),
                "average_hit_rate": report.get("average_hit_rate"),
                "avg_gateway_protocol_time_ms": _avg_gateway_protocol_time(report),
                "alert_count": len(report.get("alerts", [])),
                "wall_clock_s": wall_clock_s,
            }
        )
    return rows


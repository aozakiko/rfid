from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Iterable


COURSE_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_ROOT = COURSE_ROOT / "outputs"


def make_run_dir(output_dir: str | Path | None = None) -> Path:
    if output_dir is not None:
        path = Path(output_dir)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = DEFAULT_OUTPUT_ROOT / f"run_{timestamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig")
    return path


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str] | None = None) -> Path:
    materialized_rows = list(rows)
    if fieldnames is None:
        keys: list[str] = []
        for row in materialized_rows:
            for key in row.keys():
                if key not in keys:
                    keys.append(key)
        fieldnames = keys

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in materialized_rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})
    return path


def gateway_summary_rows(batch_report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for gateway_id, values in batch_report.get("gateway_summary", {}).items():
        row = {"gateway_id": gateway_id}
        row.update(values)
        rows.append(row)
    return rows


def alert_rows(batch_report: dict[str, Any]) -> list[dict[str, Any]]:
    return list(batch_report.get("alerts", []))


def stream_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(result.get("stream_snapshots", []), start=1):
        rows.append(
            {
                "index": index,
                "latest_batch": item.get("latest_batch"),
                "latest_gateway": item.get("latest_gateway"),
                "latest_device": item.get("latest_device"),
                "latest_modes": json.dumps(item.get("latest_modes", []), ensure_ascii=False),
                "recent_hot_prices": json.dumps(item.get("recent_hot_prices", []), ensure_ascii=False),
                "recent_hit_rate": item.get("recent_hit_rate"),
                "recent_avg_protocol_time_ms": item.get("recent_avg_protocol_time_ms"),
                "recent_alert_count": item.get("recent_alert_count"),
            }
        )
    return rows


def _average_gateway_latency(batch_report: dict[str, Any]) -> float:
    latencies = [
        float(row.get("avg_protocol_time_ms", 0.0))
        for row in batch_report.get("gateway_summary", {}).values()
    ]
    return round(mean(latencies), 2) if latencies else 0.0


def build_summary_markdown(
    result: dict[str, Any],
    security_result: dict[str, Any] | None = None,
    benchmark_rows: list[dict[str, Any]] | None = None,
) -> str:
    scenario = result["scenario"]
    report = result["batch_report"]
    security_text = "未运行"
    if security_result is not None:
        security_text = "通过" if security_result.get("passed") else "未通过"

    benchmark_text = "未运行"
    if benchmark_rows:
        benchmark_text = f"已运行 {len(benchmark_rows)} 组配置"

    return f"""# 智慧仓储 RFID 云边端协同系统运行摘要

## 场景参数

- 场景: {scenario.get("scene")}
- 边缘协议: {scenario.get("protocol")} ({scenario.get("protocol_source")})
- 终端读写器数量: {scenario.get("device_count")}
- 边缘网关数量: {scenario.get("gateway_count")}
- 采集批次数: {scenario.get("batch_count")}
- 每批 RFID 标签数: {scenario.get("num_tags_per_batch")}
- 价格范围: {scenario.get("price_min")} - {scenario.get("price_max")}
- 云端流处理窗口: {scenario.get("stream_window_size")}

## 关键结果

- 云端累计处理窗口数: {report.get("total_windows")}
- 全局识别命中率: {report.get("average_hit_rate")}
- 边缘平均协议耗时指标: {_average_gateway_latency(report)} ms
- 全局热点价格: {json.dumps(report.get("global_hot_prices", []), ensure_ascii=False)}
- 异常告警数量: {len(report.get("alerts", []))}
- 安全完整性测试: {security_text}
- 可扩展性基准实验: {benchmark_text}

## 课程要求对应

- 云边端计算划分: 端侧负责 RFID/环境数据采集，边缘侧负责协议推理和即时告警，云端负责滑动窗口统计和全局汇总。
- 流处理: `stream_snapshots.csv` 记录每个边缘结果到达后云端滑动窗口的实时状态。
- 一般处理: `batch_report.json` 汇总全局热点、网关平均延迟、命中率和告警信息。
- 分布式计算: 多个 `EdgeGateway` 通过线程池并发处理不同终端批次。
- 大模型技术: 可结合 `ai_protocol_optimizer.py` 对 RFID 查询协议进行自动生成、验证和迭代优化。
- 网络安全: `security_check.json` 展示 HMAC-SHA256 对篡改消息、错误密钥和缺失签名的拒绝能力。
"""


def export_course_result(
    result: dict[str, Any],
    output_dir: Path,
    security_result: dict[str, Any] | None = None,
    benchmark_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Path]:
    scenario = result["scenario"]
    batch_report = result["batch_report"]

    paths = {
        "scenario": write_json(output_dir / "scenario.json", scenario),
        "stream_snapshots": write_csv(
            output_dir / "stream_snapshots.csv",
            stream_rows(result),
            [
                "index",
                "latest_batch",
                "latest_gateway",
                "latest_device",
                "latest_modes",
                "recent_hot_prices",
                "recent_hit_rate",
                "recent_avg_protocol_time_ms",
                "recent_alert_count",
            ],
        ),
        "batch_report": write_json(output_dir / "batch_report.json", batch_report),
        "gateway_summary": write_csv(
            output_dir / "gateway_summary.csv",
            gateway_summary_rows(batch_report),
            ["gateway_id", "windows", "avg_protocol_time_ms", "avg_frame_size", "hit_rate"],
        ),
        "alerts": write_csv(
            output_dir / "alerts.csv",
            alert_rows(batch_report),
            ["gateway_id", "device_id", "batch_index", "alert"],
        ),
    }

    if security_result is not None:
        paths["security_check"] = write_json(output_dir / "security_check.json", security_result)

    if benchmark_rows:
        paths["benchmark"] = write_csv(
            output_dir / "benchmark.csv",
            benchmark_rows,
            [
                "devices",
                "gateways",
                "batches",
                "num_tags",
                "price_min",
                "price_max",
                "total_windows",
                "average_hit_rate",
                "avg_gateway_protocol_time_ms",
                "alert_count",
                "wall_clock_s",
            ],
        )

    paths["summary"] = write_text(
        output_dir / "run_summary.md",
        build_summary_markdown(result, security_result, benchmark_rows),
    )
    return paths

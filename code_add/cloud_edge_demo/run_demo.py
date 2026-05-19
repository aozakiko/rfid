from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cloud_edge_demo.pipeline import run_distributed_demo


def main() -> int:
    parser = argparse.ArgumentParser(description="运行一个最小可执行的 RFID 云边端协同演示。")
    parser.add_argument("--scene", type=str, default="multi_mode", help="场景名称")
    parser.add_argument("--protocol", type=str, default="g2_2", help="用于边缘推理的协议名称")
    parser.add_argument("--devices", type=int, default=4, help="终端读写器数量")
    parser.add_argument("--gateways", type=int, default=2, help="边缘网关数量")
    parser.add_argument("--batches", type=int, default=3, help="每个终端采集的窗口批次数")
    parser.add_argument("--num-tags", type=int, default=180, help="每批采集标签数")
    parser.add_argument("--price-min", type=int, default=None, help="价格下限")
    parser.add_argument("--price-max", type=int, default=None, help="价格上限")
    parser.add_argument("--stream-window", type=int, default=4, help="云端流式看板窗口大小")
    parser.add_argument("--shared-secret", type=str, default="rfid-course-demo-secret", help="签名密钥")
    args = parser.parse_args()

    result = run_distributed_demo(
        scene=args.scene,
        protocol_name=args.protocol,
        device_count=max(1, int(args.devices)),
        gateway_count=max(1, int(args.gateways)),
        batch_count=max(1, int(args.batches)),
        num_tags=max(20, int(args.num_tags)),
        price_min=args.price_min,
        price_max=args.price_max,
        stream_window_size=max(1, int(args.stream_window)),
        shared_secret=args.shared_secret,
    )

    scenario = result["scenario"]
    print("=== 云边端协同演示启动 ===")
    print(json.dumps(scenario, ensure_ascii=False, indent=2))

    print("\n=== 流式处理输出 ===")
    for snapshot in result["stream_snapshots"]:
        print(
            "[stream] "
            f"batch={snapshot['latest_batch']} "
            f"gateway={snapshot['latest_gateway']} "
            f"device={snapshot['latest_device']} "
            f"modes={snapshot['latest_modes']} "
            f"recent_hit_rate={snapshot['recent_hit_rate']:.3f} "
            f"recent_avg_protocol_time_ms={snapshot['recent_avg_protocol_time_ms']:.2f} "
            f"recent_alerts={snapshot['recent_alert_count']}"
        )

    print("\n=== 一般处理汇总 ===")
    print(json.dumps(result["batch_report"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
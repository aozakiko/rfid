from __future__ import annotations

import hashlib
import hmac
import json
import random
import sys
import time
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from statistics import mean
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from base import IProtocol, Tag
from backend_tools.protocol_metrics import normalize_protocol_metrics


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sign_payload(payload: dict[str, Any], shared_secret: str) -> str:
    return hmac.new(
        shared_secret.encode("utf-8"),
        _canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def create_signed_envelope(payload: dict[str, Any], shared_secret: str) -> dict[str, Any]:
    return {
        "payload": payload,
        "signature": sign_payload(payload, shared_secret),
    }


def verify_signed_envelope(envelope: dict[str, Any], shared_secret: str) -> dict[str, Any]:
    payload = envelope.get("payload")
    signature = envelope.get("signature", "")
    if not isinstance(payload, dict):
        raise ValueError("消息体格式错误。")

    expected_signature = sign_payload(payload, shared_secret)
    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError("消息签名校验失败。")
    return payload


def load_scene_task(scene: str) -> tuple[Callable[..., Any], dict[str, Any]]:
    task_path = PROJECT_ROOT / scene / "Task.py"
    if not task_path.exists():
        raise FileNotFoundError(f"找不到场景任务文件: {task_path}")

    spec = spec_from_file_location(f"cloud_edge_demo.task.{scene}", task_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载场景任务文件: {task_path}")

    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    generate_tags = getattr(module, "generate_tags", None)
    if generate_tags is None:
        raise AttributeError(f"{task_path} 中缺少 generate_tags 函数")

    simulation_config = getattr(module, "SIMULATION_CONFIG", {})
    return generate_tags, simulation_config


def load_protocol_class(scene: str, protocol_name: str) -> tuple[type[IProtocol], str]:
    scene_path = PROJECT_ROOT / scene
    if scene_path.is_dir():
        for gen_dir in sorted(scene_path.iterdir()):
            protocol_path = gen_dir / f"{protocol_name}.py"
            if gen_dir.is_dir() and gen_dir.name.startswith("generation") and protocol_path.exists():
                spec = spec_from_file_location(f"cloud_edge_demo.{scene}.{gen_dir.name}.{protocol_name}", protocol_path)
                if spec is None or spec.loader is None:
                    raise ImportError(f"无法加载协议文件: {protocol_path}")
                module = module_from_spec(spec)
                spec.loader.exec_module(module)
                protocol_class = getattr(module, protocol_name)
                return protocol_class, f"{scene}/{gen_dir.name}"

    base_protocol_path = PROJECT_ROOT / "base_protocol" / f"{protocol_name}.py"
    if base_protocol_path.exists():
        spec = spec_from_file_location(f"cloud_edge_demo.base.{protocol_name}", base_protocol_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载协议文件: {base_protocol_path}")
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        protocol_class = getattr(module, protocol_name)
        return protocol_class, "base_protocol"

    raise FileNotFoundError(f"在场景 {scene} 及 base_protocol 中均未找到协议 {protocol_name}")


@dataclass(frozen=True)
class DeviceScanBatch:
    device_id: str
    gateway_id: str
    batch_index: int
    tags: list[Tag]
    true_modes: list[int]
    shelf_hot_price: int
    temperature_c: float
    humidity_pct: float

    def to_signed_envelope(self, shared_secret: str) -> dict[str, Any]:
        payload = {
            "device_id": self.device_id,
            "gateway_id": self.gateway_id,
            "batch_index": self.batch_index,
            "true_modes": self.true_modes,
            "shelf_hot_price": self.shelf_hot_price,
            "temperature_c": round(self.temperature_c, 2),
            "humidity_pct": round(self.humidity_pct, 2),
            "tags": [{"id": tag.id, "price": tag.price} for tag in self.tags],
        }
        return create_signed_envelope(payload, shared_secret)


class ShelfReaderDevice:
    def __init__(
        self,
        *,
        device_index: int,
        gateway_id: str,
        generate_tags_func: Callable[..., Any],
        num_tags: int,
        price_min: int,
        price_max: int,
        hot_price: int,
        hot_ratio: float,
        rng: random.Random,
    ) -> None:
        self.device_index = device_index
        self.device_id = f"reader-{device_index + 1}"
        self.gateway_id = gateway_id
        self.generate_tags_func = generate_tags_func
        self.num_tags = num_tags
        self.price_min = price_min
        self.price_max = price_max
        self.hot_price = hot_price
        self.hot_ratio = hot_ratio
        self.rng = rng

    def capture_batch(self, batch_index: int) -> DeviceScanBatch:
        tags, true_info = self.generate_tags_func(self.num_tags, self.price_min, self.price_max)
        remapped_tags: list[Tag] = []
        tag_id_prefix = (batch_index * 1_000_000) + (self.device_index * 100_000)

        for offset, tag in enumerate(tags):
            remapped_tags.append(Tag(tag_id_prefix + offset, tag.price))

        hot_tag_count = min(len(remapped_tags), max(1, int(len(remapped_tags) * self.hot_ratio)))
        hot_indices = list(range(len(remapped_tags)))
        self.rng.shuffle(hot_indices)
        for idx in hot_indices[:hot_tag_count]:
            remapped_tags[idx] = Tag(remapped_tags[idx].id, self.hot_price)

        self.rng.shuffle(remapped_tags)

        base_modes = true_info.get("modes", []) if isinstance(true_info, dict) else []
        merged_modes = sorted({int(mode) for mode in base_modes} | {self.hot_price})

        temperature_c = self.rng.uniform(19.0, 27.5)
        if batch_index % 3 == 0 and self.device_index % 2 == 0:
            temperature_c += 5.5
        humidity_pct = self.rng.uniform(42.0, 63.0)

        return DeviceScanBatch(
            device_id=self.device_id,
            gateway_id=self.gateway_id,
            batch_index=batch_index,
            tags=remapped_tags,
            true_modes=merged_modes,
            shelf_hot_price=self.hot_price,
            temperature_c=temperature_c,
            humidity_pct=humidity_pct,
        )


class EdgeGateway:
    def __init__(
        self,
        *,
        gateway_id: str,
        protocol_factory: Callable[[], IProtocol],
        shared_secret: str,
        price_min: int,
        price_max: int,
        temp_threshold: float = 30.0,
    ) -> None:
        self.gateway_id = gateway_id
        self.protocol_factory = protocol_factory
        self.shared_secret = shared_secret
        self.price_min = price_min
        self.price_max = price_max
        self.temp_threshold = temp_threshold

    def process_device_message(self, envelope: dict[str, Any]) -> dict[str, Any]:
        payload = verify_signed_envelope(envelope, self.shared_secret)
        tags = [Tag(tag["id"], tag["price"]) for tag in payload.get("tags", [])]

        try:
            protocol = self.protocol_factory()
            wall_clock_start = time.perf_counter()
            estimated_modes, protocol_score, protocol_time_ms, verification_data = protocol.execute(
                tags,
                self.price_min,
                self.price_max,
            )
            wall_clock_ms = (time.perf_counter() - wall_clock_start) * 1000.0
            metrics = normalize_protocol_metrics(verification_data)

            alert = None
            if payload.get("temperature_c", 0.0) >= self.temp_threshold:
                alert = f"温度异常: {payload['temperature_c']:.1f}C"

            estimated_mode_list = [int(mode) for mode in estimated_modes]
            true_mode_list = [int(mode) for mode in payload.get("true_modes", [])]
            detection_hit = bool(set(estimated_mode_list) & set(true_mode_list))

            summary = {
                "status": "ok",
                "gateway_id": self.gateway_id,
                "device_id": payload["device_id"],
                "batch_index": payload["batch_index"],
                "estimated_modes": estimated_mode_list,
                "true_modes": true_mode_list,
                "shelf_hot_price": int(payload.get("shelf_hot_price", 0)),
                "detection_hit": detection_hit,
                "protocol_score": protocol_score,
                "protocol_time_ms": round(float(protocol_time_ms), 2),
                "wall_clock_ms": round(wall_clock_ms, 2),
                "frame_size": int(metrics.get("frame_size", 0)),
                "bits_per_slot": int(metrics.get("bits_per_slot", 0)),
                "temperature_c": round(float(payload.get("temperature_c", 0.0)), 2),
                "humidity_pct": round(float(payload.get("humidity_pct", 0.0)), 2),
                "alert": alert,
                "validation_notes": metrics.get("validation_notes", []),
            }
        except Exception as exc:
            summary = {
                "status": "error",
                "gateway_id": self.gateway_id,
                "device_id": payload.get("device_id", "unknown"),
                "batch_index": payload.get("batch_index", -1),
                "estimated_modes": [],
                "true_modes": [int(mode) for mode in payload.get("true_modes", [])],
                "shelf_hot_price": int(payload.get("shelf_hot_price", 0)),
                "detection_hit": False,
                "protocol_score": None,
                "protocol_time_ms": 0.0,
                "wall_clock_ms": 0.0,
                "frame_size": 0,
                "bits_per_slot": 0,
                "temperature_c": round(float(payload.get("temperature_c", 0.0)), 2),
                "humidity_pct": round(float(payload.get("humidity_pct", 0.0)), 2),
                "alert": f"边缘执行失败: {exc}",
                "validation_notes": [],
            }

        return create_signed_envelope(summary, self.shared_secret)


class CloudCoordinator:
    def __init__(self, *, shared_secret: str, stream_window_size: int) -> None:
        self.shared_secret = shared_secret
        self.stream_window_size = stream_window_size
        self.recent_results: deque[dict[str, Any]] = deque(maxlen=stream_window_size)
        self.history: list[dict[str, Any]] = []

    def ingest_edge_summary(self, envelope: dict[str, Any]) -> dict[str, Any]:
        payload = verify_signed_envelope(envelope, self.shared_secret)
        self.history.append(payload)
        self.recent_results.append(payload)
        return self._build_stream_snapshot(payload)

    def _build_stream_snapshot(self, latest_payload: dict[str, Any]) -> dict[str, Any]:
        hot_counter: Counter[int] = Counter()
        alert_count = 0
        hit_values: list[float] = []
        protocol_latencies: list[float] = []

        for item in self.recent_results:
            hot_counter.update(int(mode) for mode in item.get("estimated_modes", []))
            if item.get("alert"):
                alert_count += 1
            hit_values.append(1.0 if item.get("detection_hit") else 0.0)
            protocol_latencies.append(float(item.get("protocol_time_ms", 0.0)))

        top_recent_modes = [
            {"price": int(price), "count": int(count)}
            for price, count in hot_counter.most_common(3)
        ]

        return {
            "latest_gateway": latest_payload.get("gateway_id"),
            "latest_device": latest_payload.get("device_id"),
            "latest_batch": latest_payload.get("batch_index"),
            "latest_modes": latest_payload.get("estimated_modes", []),
            "recent_hot_prices": top_recent_modes,
            "recent_hit_rate": round(mean(hit_values) if hit_values else 0.0, 3),
            "recent_avg_protocol_time_ms": round(mean(protocol_latencies) if protocol_latencies else 0.0, 2),
            "recent_alert_count": alert_count,
        }

    def build_batch_report(self) -> dict[str, Any]:
        gateway_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        global_hot_counter: Counter[int] = Counter()
        alert_rows: list[dict[str, Any]] = []
        hit_values: list[float] = []

        for row in self.history:
            gateway_rows[row["gateway_id"]].append(row)
            global_hot_counter.update(int(mode) for mode in row.get("estimated_modes", []))
            hit_values.append(1.0 if row.get("detection_hit") else 0.0)
            if row.get("alert"):
                alert_rows.append(
                    {
                        "gateway_id": row["gateway_id"],
                        "device_id": row["device_id"],
                        "batch_index": row["batch_index"],
                        "alert": row["alert"],
                    }
                )

        gateway_summary: dict[str, Any] = {}
        for gateway_id, rows in gateway_rows.items():
            gateway_summary[gateway_id] = {
                "windows": len(rows),
                "avg_protocol_time_ms": round(mean(float(row.get("protocol_time_ms", 0.0)) for row in rows), 2),
                "avg_frame_size": round(mean(float(row.get("frame_size", 0.0)) for row in rows), 2),
                "hit_rate": round(mean(1.0 if row.get("detection_hit") else 0.0 for row in rows), 3),
            }

        return {
            "total_windows": len(self.history),
            "average_hit_rate": round(mean(hit_values) if hit_values else 0.0, 3),
            "global_hot_prices": [
                {"price": int(price), "count": int(count)}
                for price, count in global_hot_counter.most_common(5)
            ],
            "gateway_summary": gateway_summary,
            "alerts": alert_rows,
        }


def _build_devices(
    *,
    device_count: int,
    gateway_count: int,
    generate_tags_func: Callable[..., Any],
    num_tags: int,
    price_min: int,
    price_max: int,
    seed: int,
) -> list[ShelfReaderDevice]:
    devices: list[ShelfReaderDevice] = []
    price_span = max(1, price_max - price_min)
    hot_step = max(1, price_span // max(2, gateway_count + 1))

    for device_index in range(device_count):
        gateway_id = f"edge-{(device_index % gateway_count) + 1}"
        hot_price = min(price_max, price_min + ((device_index % gateway_count) + 1) * hot_step)
        rng = random.Random(seed + device_index)
        devices.append(
            ShelfReaderDevice(
                device_index=device_index,
                gateway_id=gateway_id,
                generate_tags_func=generate_tags_func,
                num_tags=num_tags,
                price_min=price_min,
                price_max=price_max,
                hot_price=hot_price,
                hot_ratio=0.22,
                rng=rng,
            )
        )

    return devices


def run_distributed_demo(
    *,
    scene: str,
    protocol_name: str,
    device_count: int,
    gateway_count: int,
    batch_count: int,
    num_tags: int,
    price_min: int | None = None,
    price_max: int | None = None,
    stream_window_size: int = 4,
    shared_secret: str = "rfid-course-demo-secret",
    seed: int = 20260510,
) -> dict[str, Any]:
    generate_tags_func, simulation_config = load_scene_task(scene)
    protocol_class, protocol_source = load_protocol_class(scene, protocol_name)

    effective_price_min = int(price_min if price_min is not None else simulation_config.get("PRICE_MIN", 1))
    effective_price_max = int(price_max if price_max is not None else simulation_config.get("PRICE_MAX", 1000))
    effective_num_tags = int(num_tags if num_tags is not None else simulation_config.get("NUM_TAGS", 500))

    devices = _build_devices(
        device_count=device_count,
        gateway_count=gateway_count,
        generate_tags_func=generate_tags_func,
        num_tags=effective_num_tags,
        price_min=effective_price_min,
        price_max=effective_price_max,
        seed=seed,
    )

    def protocol_factory() -> IProtocol:
        return protocol_class()

    gateways = {
        f"edge-{index + 1}": EdgeGateway(
            gateway_id=f"edge-{index + 1}",
            protocol_factory=protocol_factory,
            shared_secret=shared_secret,
            price_min=effective_price_min,
            price_max=effective_price_max,
        )
        for index in range(gateway_count)
    }

    cloud = CloudCoordinator(shared_secret=shared_secret, stream_window_size=stream_window_size)
    stream_snapshots: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=gateway_count) as executor:
        for batch_index in range(1, batch_count + 1):
            future_map = {}
            for device in devices:
                envelope = device.capture_batch(batch_index).to_signed_envelope(shared_secret)
                gateway = gateways[device.gateway_id]
                future = executor.submit(gateway.process_device_message, envelope)
                future_map[future] = device.device_id

            for future in as_completed(future_map):
                edge_summary = future.result()
                stream_snapshots.append(cloud.ingest_edge_summary(edge_summary))

    return {
        "scenario": {
            "scene": scene,
            "protocol": protocol_name,
            "protocol_source": protocol_source,
            "device_count": device_count,
            "gateway_count": gateway_count,
            "batch_count": batch_count,
            "num_tags_per_batch": effective_num_tags,
            "price_min": effective_price_min,
            "price_max": effective_price_max,
            "stream_window_size": stream_window_size,
        },
        "stream_snapshots": stream_snapshots,
        "batch_report": cloud.build_batch_report(),
    }
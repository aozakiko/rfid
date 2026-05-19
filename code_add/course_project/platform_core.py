from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ProtocolProfile:
    name: str
    family: str
    function_tags: tuple[str, ...]
    strengths: tuple[str, ...]
    risk_notes: tuple[str, ...]
    feature_vector: dict[str, float]


PROTOCOL_PROFILES: dict[str, ProtocolProfile] = {
    "g2_2": ProtocolProfile(
        name="g2_2",
        family="Adaptive Hybrid Selective Estimation",
        function_tags=("hotspot", "multi_mode", "filter", "accurate_count"),
        strengths=("支持粗筛-精筛-精确计数三阶段", "适合热点价格识别", "已有较完整的验证数据"),
        risk_notes=("大规模标签下精确阶段通信量会升高",),
        feature_vector={"structure": 0.88, "function": 0.91, "constraint": 0.79, "context": 0.84, "process": 0.87},
    ),
    "g2_1": ProtocolProfile(
        name="g2_1",
        family="Selective Estimation Baseline",
        function_tags=("hotspot", "estimation", "baseline"),
        strengths=("结构简单", "适合作为交叉父代", "便于参数调优"),
        risk_notes=("复杂场景下需要额外过滤策略",),
        feature_vector={"structure": 0.81, "function": 0.76, "constraint": 0.83, "context": 0.68, "process": 0.73},
    ),
    "g8_3": ProtocolProfile(
        name="g8_3",
        family="Robust Multi-Mode Candidate Protocol",
        function_tags=("robust", "multi_mode", "dynamic"),
        strengths=("对多众数和动态场景更稳健", "适合作为压力测试对照"),
        risk_notes=("不同业务下参数需要重新校准",),
        feature_vector={"structure": 0.83, "function": 0.85, "constraint": 0.75, "context": 0.88, "process": 0.79},
    ),
    "g10_4": ProtocolProfile(
        name="g10_4",
        family="High-Generation Optimized Protocol",
        function_tags=("optimized", "benchmark", "low_latency"),
        strengths=("高代优化候选协议", "适合与新生成协议进行对照"),
        risk_notes=("部署前需要再次确认物理层约束",),
        feature_vector={"structure": 0.86, "function": 0.84, "constraint": 0.82, "context": 0.81, "process": 0.85},
    ),
    "ModeQueryTCS": ProtocolProfile(
        name="ModeQueryTCS",
        family="Baseline Mode Query",
        function_tags=("baseline", "mode_query", "simple"),
        strengths=("基础协议库组件", "适合稳定回退策略"),
        risk_notes=("面对复合需求时需要组合优化",),
        feature_vector={"structure": 0.71, "function": 0.68, "constraint": 0.88, "context": 0.58, "process": 0.66},
    ),
    "ModeQuerySEM": ProtocolProfile(
        name="ModeQuerySEM",
        family="Statistical Estimation Mode Query",
        function_tags=("baseline", "estimation", "statistics"),
        strengths=("适合统计估计类子任务", "容易与其他协议组合"),
        risk_notes=("偏态分布下需要上下文感知调参",),
        feature_vector={"structure": 0.75, "function": 0.73, "constraint": 0.86, "context": 0.67, "process": 0.71},
    ),
    "ModeQueryTKQ": ProtocolProfile(
        name="ModeQueryTKQ",
        family="Top-K Query Baseline",
        function_tags=("baseline", "topk", "hotspot"),
        strengths=("适合热度 Top-K 查询", "便于解释和回退"),
        risk_notes=("高干扰环境下需要更多冗余轮次",),
        feature_vector={"structure": 0.72, "function": 0.78, "constraint": 0.84, "context": 0.64, "process": 0.70},
    ),
}


TASK_LIBRARY: dict[str, dict[str, Any]] = {
    "inventory_count": {
        "title": "库存计数",
        "operator": "count",
        "layer": "edge",
        "mode": "stream",
        "description": "统计读写器窗口内的有效标签数量并估计缺口。",
    },
    "mode_query": {
        "title": "众数查询",
        "operator": "mode",
        "layer": "edge",
        "mode": "stream",
        "description": "识别当前货架或工位中出现频率最高的价格/类别。",
    },
    "hotspot_topk": {
        "title": "热点 Top-K",
        "operator": "topk",
        "layer": "cloud",
        "mode": "stream",
        "description": "在滑动窗口中输出跨网关热点标签特征。",
    },
    "missing_detection": {
        "title": "丢失标签检测",
        "operator": "set-difference",
        "layer": "edge",
        "mode": "stream",
        "description": "计算期望集合 E 与观测集合 T 的差集 M。",
    },
    "unknown_detection": {
        "title": "未知标签检测",
        "operator": "set-difference",
        "layer": "edge",
        "mode": "stream",
        "description": "计算观测集合 T 中不属于业务清单的未知集合 U。",
    },
    "temperature_outlier": {
        "title": "温度异常",
        "operator": "filter/alert",
        "layer": "edge",
        "mode": "stream",
        "description": "在边缘侧对温度越界进行低延迟告警。",
    },
    "humidity_histogram": {
        "title": "湿度分布",
        "operator": "histogram",
        "layer": "cloud",
        "mode": "batch",
        "description": "对环境湿度形成批处理分布报表。",
    },
    "route_trace": {
        "title": "路径追溯",
        "operator": "trace",
        "layer": "cloud",
        "mode": "batch",
        "description": "聚合网关、读写器和时间窗口形成资产移动链路。",
    },
    "anti_collision": {
        "title": "抗碰撞调度",
        "operator": "frame-tuning",
        "layer": "edge",
        "mode": "stream",
        "description": "根据冲突率动态调整帧长和查询轮次。",
    },
    "energy_budget": {
        "title": "低功耗预算",
        "operator": "energy-budget",
        "layer": "device",
        "mode": "stream",
        "description": "限制读写器采样频率和重试次数，降低端侧功耗。",
    },
    "security_audit": {
        "title": "安全审计",
        "operator": "hmac-audit",
        "layer": "cloud",
        "mode": "batch",
        "description": "保存签名校验、异常消息和回退事件，支持报告追溯。",
    },
    "predictive_maintenance": {
        "title": "预测性维护",
        "operator": "trend-detect",
        "layer": "cloud",
        "mode": "batch",
        "description": "对延迟、帧长、命中率趋势进行健康度评估。",
    },
}


OPTIMIZATION_PRIORITIES: dict[str, dict[str, str]] = {
    "low_latency": {"title": "低延迟", "description": "优先压缩边缘推理和告警链路时延。"},
    "accuracy": {"title": "高准确率", "description": "优先提高热点识别、漏读检测和召回率。"},
    "energy": {"title": "低功耗", "description": "降低读写器采样、重试和上报开销。"},
    "robustness": {"title": "抗干扰", "description": "适应遮挡、多读写器和高速移动环境。"},
    "security": {"title": "安全合规", "description": "强调签名校验、信息隔离和审计留痕。"},
    "traceability": {"title": "可追溯", "description": "保留批处理报表、路径链路和配置版本。"},
}


SCENARIO_PRESETS: dict[str, dict[str, Any]] = {
    "fresh_cold_chain": {
        "title": "生鲜冷链多维感知",
        "domain": "冷链仓储",
        "requirement": "在冷链仓库中监测生鲜货物的 RFID、温度和湿度数据，识别丢失标签、热点货架和温度异常，要求 500ms 内告警并支持云端追溯。",
        "task_ids": ("inventory_count", "mode_query", "hotspot_topk", "missing_detection", "temperature_outlier", "humidity_histogram", "security_audit", "route_trace"),
        "constraints": {"latency_ms": 500, "accuracy": 0.95, "loss_rate": 0.04, "mobility": 0.25, "interference": 0.45, "energy_budget": 0.62},
        "priorities": ("low_latency", "accuracy", "traceability", "security"),
    },
    "dynamic_logistics": {
        "title": "高动态工业物流分拣",
        "domain": "工业物流",
        "requirement": "在高速传送带和多读写器环境下识别进入、离开、丢失和未知标签，500ms 内完成热点批次统计，边缘侧需要抗碰撞和上下文自适应调参。",
        "task_ids": ("inventory_count", "missing_detection", "unknown_detection", "hotspot_topk", "anti_collision", "route_trace", "security_audit"),
        "constraints": {"latency_ms": 420, "accuracy": 0.97, "loss_rate": 0.08, "mobility": 0.86, "interference": 0.72, "energy_budget": 0.48},
        "priorities": ("low_latency", "robustness", "accuracy"),
    },
    "smart_retail": {
        "title": "智慧零售货架分析",
        "domain": "无人零售",
        "requirement": "对多货架 RFID 数据进行实时热点价格识别、补货建议和异常温湿度告警，云端生成销售热度报表并保存审计记录。",
        "task_ids": ("inventory_count", "mode_query", "hotspot_topk", "temperature_outlier", "humidity_histogram", "predictive_maintenance", "security_audit"),
        "constraints": {"latency_ms": 650, "accuracy": 0.93, "loss_rate": 0.03, "mobility": 0.35, "interference": 0.35, "energy_budget": 0.55},
        "priorities": ("accuracy", "traceability", "energy"),
    },
    "medical_asset": {
        "title": "医疗药品与器械监管",
        "domain": "医疗资产",
        "requirement": "对药品、手术器械和高值耗材进行 RFID 盘点，监测温湿度越界、丢失和未授权移动，所有事件必须云端留痕并可审计。",
        "task_ids": ("inventory_count", "missing_detection", "unknown_detection", "temperature_outlier", "route_trace", "security_audit"),
        "constraints": {"latency_ms": 600, "accuracy": 0.985, "loss_rate": 0.02, "mobility": 0.45, "interference": 0.42, "energy_budget": 0.50},
        "priorities": ("security", "accuracy", "traceability"),
    },
    "library_archive": {
        "title": "图书档案智能盘点",
        "domain": "图书档案",
        "requirement": "在图书馆和档案库中完成大批量 RFID 盘点、错架识别、缺失标签检测和历史批处理报表，要求低成本、可解释、易部署。",
        "task_ids": ("inventory_count", "missing_detection", "unknown_detection", "route_trace", "security_audit"),
        "constraints": {"latency_ms": 1200, "accuracy": 0.94, "loss_rate": 0.05, "mobility": 0.18, "interference": 0.30, "energy_budget": 0.70},
        "priorities": ("traceability", "energy", "accuracy"),
    },
    "manufacturing_wip": {
        "title": "制造业在制品 WIP 跟踪",
        "domain": "智能制造",
        "requirement": "在多工位产线上跟踪 RFID 在制品流转，识别工序遗漏、异常停留和高频热点批次，边缘网关需要在高干扰环境下稳定运行。",
        "task_ids": ("inventory_count", "missing_detection", "route_trace", "anti_collision", "predictive_maintenance", "security_audit"),
        "constraints": {"latency_ms": 480, "accuracy": 0.965, "loss_rate": 0.06, "mobility": 0.72, "interference": 0.68, "energy_budget": 0.52},
        "priorities": ("low_latency", "robustness", "traceability"),
    },
    "hazmat_storage": {
        "title": "危化品仓储安全监管",
        "domain": "安全仓储",
        "requirement": "对危化品容器进行 RFID 身份核验、温湿度异常监测、越界移动告警和全链路审计，优先保证安全合规和告警可靠性。",
        "task_ids": ("inventory_count", "missing_detection", "unknown_detection", "temperature_outlier", "route_trace", "security_audit"),
        "constraints": {"latency_ms": 380, "accuracy": 0.99, "loss_rate": 0.015, "mobility": 0.38, "interference": 0.55, "energy_budget": 0.46},
        "priorities": ("security", "low_latency", "accuracy", "traceability"),
    },
    "campus_asset": {
        "title": "校园资产与实验设备管理",
        "domain": "校园物联",
        "requirement": "对实验设备、借还物资和固定资产进行 RFID 盘点，支持普通用户自然语言配置盘点规则，云端生成资产报表和异常清单。",
        "task_ids": ("inventory_count", "missing_detection", "unknown_detection", "route_trace", "security_audit", "predictive_maintenance"),
        "constraints": {"latency_ms": 900, "accuracy": 0.94, "loss_rate": 0.04, "mobility": 0.30, "interference": 0.36, "energy_budget": 0.68},
        "priorities": ("traceability", "energy", "security"),
    },
}


DIMENSION_LABELS = {
    "structure": "结构适配",
    "function": "功能覆盖",
    "constraint": "约束满足",
    "context": "上下文适应",
    "process": "流程可部署",
}


DIMENSION_WEIGHTS = {
    "structure": 0.18,
    "function": 0.28,
    "constraint": 0.22,
    "context": 0.18,
    "process": 0.14,
}


SCENARIO_KEYWORDS = {
    "fresh_cold_chain": ("冷链", "生鲜", "冷库", "温度", "湿度", "保鲜", "食品"),
    "dynamic_logistics": ("物流", "分拣", "传送带", "高速", "移动", "托盘", "出入库"),
    "smart_retail": ("零售", "货架", "补货", "门店", "销售", "热销", "价格"),
    "medical_asset": ("医疗", "药品", "器械", "耗材", "医院", "手术", "合规"),
    "library_archive": ("图书", "档案", "书架", "借还", "错架", "盘点"),
    "manufacturing_wip": ("制造", "工位", "产线", "在制品", "工序", "工业"),
    "hazmat_storage": ("危化", "危险品", "安全", "越界", "容器", "监管"),
    "campus_asset": ("校园", "实验室", "设备", "资产", "借用", "归还"),
}


TASK_KEYWORDS = {
    "inventory_count": ("盘点", "库存", "计数", "数量", "清点"),
    "mode_query": ("众数", "频率最高", "热点价格", "主流", "热销"),
    "hotspot_topk": ("top", "topk", "热度", "热点", "排行榜", "高频"),
    "missing_detection": ("丢失", "缺失", "漏读", "少了", "遗失"),
    "unknown_detection": ("未知", "未授权", "异常标签", "多出", "陌生"),
    "temperature_outlier": ("温度", "高温", "低温", "温控", "越界"),
    "humidity_histogram": ("湿度", "潮湿", "干燥"),
    "route_trace": ("追溯", "路径", "流转", "轨迹", "历史", "审计"),
    "anti_collision": ("碰撞", "抗干扰", "多读写器", "遮挡", "冲突"),
    "energy_budget": ("低功耗", "省电", "电池", "功耗"),
    "security_audit": ("安全", "签名", "篡改", "权限", "合规", "审计"),
    "predictive_maintenance": ("预测", "维护", "趋势", "健康", "异常趋势"),
}


PRIORITY_KEYWORDS = {
    "low_latency": ("低延迟", "实时", "毫秒", "ms", "告警"),
    "accuracy": ("高准确", "准确率", "召回", "精度", "可靠"),
    "energy": ("低功耗", "省电", "电池", "功耗"),
    "robustness": ("抗干扰", "高速", "移动", "遮挡", "鲁棒"),
    "security": ("安全", "合规", "签名", "篡改", "权限"),
    "traceability": ("追溯", "留痕", "审计", "报表", "历史"),
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _first_number(text: str, patterns: tuple[str, ...]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def infer_from_natural_language(text: str) -> dict[str, Any]:
    compact = (text or "").strip()
    lowered = compact.lower()
    scenario_scores = {key: 0 for key in SCENARIO_PRESETS}
    for key, words in SCENARIO_KEYWORDS.items():
        scenario_scores[key] += sum(1 for word in words if word.lower() in lowered)
    inferred_preset = max(scenario_scores, key=scenario_scores.get)
    if scenario_scores[inferred_preset] == 0:
        inferred_preset = "fresh_cold_chain"

    task_ids = set(SCENARIO_PRESETS[inferred_preset]["task_ids"])
    for task_id, words in TASK_KEYWORDS.items():
        if any(word.lower() in lowered for word in words):
            task_ids.add(task_id)

    priorities = set(SCENARIO_PRESETS[inferred_preset]["priorities"])
    for priority_id, words in PRIORITY_KEYWORDS.items():
        if any(word.lower() in lowered for word in words):
            priorities.add(priority_id)

    constraints = dict(SCENARIO_PRESETS[inferred_preset]["constraints"])
    latency = _first_number(lowered, (r"(\d+(?:\.\d+)?)\s*ms", r"(\d+(?:\.\d+)?)\s*毫秒"))
    if latency is not None:
        constraints["latency_ms"] = latency
        priorities.add("low_latency")

    accuracy_pct = _first_number(lowered, (r"准确率[^\d]*(\d+(?:\.\d+)?)\s*%", r"精度[^\d]*(\d+(?:\.\d+)?)\s*%"))
    if accuracy_pct is not None:
        constraints["accuracy"] = accuracy_pct / 100.0
        priorities.add("accuracy")
    elif any(word in compact for word in ("高准确", "高精度", "可靠识别")):
        constraints["accuracy"] = max(float(constraints["accuracy"]), 0.97)
        priorities.add("accuracy")

    loss_pct = _first_number(lowered, (r"丢失率[^\d]*(\d+(?:\.\d+)?)\s*%", r"漏读率[^\d]*(\d+(?:\.\d+)?)\s*%"))
    if loss_pct is not None:
        constraints["loss_rate"] = loss_pct / 100.0

    if any(word in compact for word in ("高速", "移动", "传送带", "车载", "流转")):
        constraints["mobility"] = max(float(constraints["mobility"]), 0.72)
        priorities.add("robustness")
    if any(word in compact for word in ("干扰", "遮挡", "多读写器", "金属", "碰撞")):
        constraints["interference"] = max(float(constraints["interference"]), 0.68)
        priorities.add("robustness")
    if any(word in compact for word in ("低功耗", "电池", "省电")):
        constraints["energy_budget"] = max(float(constraints["energy_budget"]), 0.72)
        priorities.add("energy")
        task_ids.add("energy_budget")

    keyword_sources = {**SCENARIO_KEYWORDS, **TASK_KEYWORDS, **PRIORITY_KEYWORDS}
    matched_keywords = {
        key: [word for word in words if word.lower() in lowered]
        for key, words in keyword_sources.items()
        if any(word.lower() in lowered for word in words)
    }
    return {
        "preset": inferred_preset,
        "task_ids": sorted(task_ids),
        "priorities": sorted(priorities),
        "constraints": constraints,
        "confidence": round(min(0.98, 0.52 + 0.06 * sum(scenario_scores.values()) + 0.03 * len(task_ids)), 2),
        "matched_keywords": matched_keywords,
    }


def normalize_requirement(payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("requirement") or payload.get("natural_language") or "").strip()
    inferred = infer_from_natural_language(text)
    preset_key = str(payload.get("preset") or inferred["preset"] or "fresh_cold_chain")
    preset = SCENARIO_PRESETS.get(preset_key, SCENARIO_PRESETS["fresh_cold_chain"])
    if not text:
        text = str(preset["requirement"])

    constraints = dict(preset["constraints"])
    constraints.update(inferred.get("constraints", {}))
    raw_constraints = payload.get("constraints")
    if isinstance(raw_constraints, dict):
        constraints.update(raw_constraints)
    for key in ("latency_ms", "accuracy", "loss_rate", "mobility", "interference", "energy_budget"):
        if key in payload:
            constraints[key] = payload[key]

    constraints["latency_ms"] = int(max(50, min(5000, _to_float(constraints.get("latency_ms"), 500))))
    constraints["accuracy"] = _clamp(_to_float(constraints.get("accuracy"), 0.95), 0.5, 0.999)
    constraints["loss_rate"] = _clamp(_to_float(constraints.get("loss_rate"), 0.04), 0.0, 0.8)
    constraints["mobility"] = _clamp(_to_float(constraints.get("mobility"), 0.3), 0.0, 1.0)
    constraints["interference"] = _clamp(_to_float(constraints.get("interference"), 0.4), 0.0, 1.0)
    constraints["energy_budget"] = _clamp(_to_float(constraints.get("energy_budget"), 0.55), 0.0, 1.0)

    selected_tasks = payload.get("task_ids") or payload.get("tasks") or inferred["task_ids"] or preset["task_ids"]
    task_ids = [str(item) for item in selected_tasks if str(item) in TASK_LIBRARY]
    if not task_ids:
        task_ids = list(preset["task_ids"])
    for required_task in ("inventory_count", "security_audit"):
        if required_task not in task_ids:
            task_ids.append(required_task)

    selected_priorities = payload.get("priorities") or inferred["priorities"] or preset["priorities"]
    priorities = [str(item) for item in selected_priorities if str(item) in OPTIMIZATION_PRIORITIES]
    if not priorities:
        priorities = list(preset["priorities"])

    task_details = [{**TASK_LIBRARY[task_id], "id": task_id} for task_id in task_ids]
    return {
        "preset": preset_key,
        "title": str(payload.get("title") or preset["title"]),
        "domain": str(preset["domain"]),
        "requirement": text,
        "task_ids": task_ids,
        "tasks": task_details,
        "operators": sorted({task["operator"] for task in task_details}),
        "priorities": priorities,
        "constraints": constraints,
        "natural_language_parse": inferred,
    }


def build_grdl_model(requirement: dict[str, Any]) -> dict[str, Any]:
    constraints = requirement["constraints"]
    task_ids = set(requirement["task_ids"])
    target = {
        "fresh_cold_chain": "ColdChainGoods",
        "dynamic_logistics": "MovingPallet",
        "smart_retail": "RetailShelf",
        "medical_asset": "MedicalAsset",
        "library_archive": "ArchiveItem",
        "manufacturing_wip": "WorkInProcess",
        "hazmat_storage": "HazmatContainer",
        "campus_asset": "CampusAsset",
    }.get(requirement["preset"], "RFIDTags")

    grdl_lines = [
        f"SCENE {requirement['preset']} TARGET {target}",
        f"LET E = expected_tags({target})",
        "LET T = observed_tags(reader_window)",
        "LET M = E - T",
        "LET U = T - E",
    ]
    if "inventory_count" in task_ids:
        grdl_lines.append("COUNT T BY gateway_id, reader_id, shelf_id")
    if "mode_query" in task_ids:
        grdl_lines.append("MODE T.price OVER edge_window")
    if "hotspot_topk" in task_ids:
        grdl_lines.append("TOPK mode(T.price), k=3 OVER cloud_sliding_window")
    if "missing_detection" in task_ids:
        grdl_lines.append("SELECT M AS missing_tags")
    if "unknown_detection" in task_ids:
        grdl_lines.append("SELECT U AS unknown_tags")
    if "temperature_outlier" in task_ids:
        grdl_lines.append("FILTER sensor.temperature WHERE outside safe_range THEN ALERT")
    if "humidity_histogram" in task_ids:
        grdl_lines.append("HISTOGRAM sensor.humidity BUCKETS 8 BY hour")
    if "route_trace" in task_ids:
        grdl_lines.append("TRACE tag_id BY reader_id, gateway_id, timestamp")
    if "anti_collision" in task_ids:
        grdl_lines.append("TUNE frame_size USING collision_rate, empty_slot_rate")
    if "energy_budget" in task_ids:
        grdl_lines.append("LIMIT device.retry_rounds AND sampling_rate BY energy_budget")
    if "security_audit" in task_ids:
        grdl_lines.append("VERIFY hmac_signature AND STORE audit_event")
    if "predictive_maintenance" in task_ids:
        grdl_lines.append("DETECT trend(latency, frame_size, hit_rate) AS device_health")
    grdl_lines.extend(
        [
            f"CONSTRAINT latency_ms <= {constraints['latency_ms']}",
            f"CONSTRAINT accuracy >= {constraints['accuracy']:.3f}",
            "CONSTRAINT bits_per_slot <= 256",
            "CONSTRAINT frame_size <= 32768",
            "CONSTRAINT reader_cannot_read_private_tag_data",
        ]
    )

    task_graph = [
        {"name": "DeviceCapture", "layer": "device", "operator": "sense/sign", "depends_on": []},
        {"name": "EdgeSignatureVerify", "layer": "edge", "operator": "hmac-verify", "depends_on": ["DeviceCapture"]},
    ]
    task_graph += [
        {
            "name": task["id"],
            "label": task["title"],
            "layer": task["layer"],
            "mode": task["mode"],
            "operator": task["operator"],
            "depends_on": ["EdgeSignatureVerify"] if task["layer"] != "cloud" else ["EdgeSummary"],
        }
        for task in requirement["tasks"]
    ]
    task_graph.extend(
        [
            {"name": "EdgeSummary", "layer": "edge", "operator": "compress/sign", "depends_on": [task["id"] for task in requirement["tasks"] if task["layer"] == "edge"] or ["EdgeSignatureVerify"]},
            {"name": "CloudStreamWindow", "layer": "cloud", "operator": "sliding-window", "depends_on": ["EdgeSummary"]},
            {"name": "CloudBatchReport", "layer": "cloud", "operator": "aggregate/report", "depends_on": ["CloudStreamWindow"]},
            {"name": "FeedbackTune", "layer": "cloud", "operator": "re-rank/tune", "depends_on": ["CloudBatchReport"]},
        ]
    )

    return {
        "dsl": "\n".join(grdl_lines),
        "sets": {"E": "业务期望标签集合", "T": "读写器窗口观测标签集合", "M": "丢失标签集合", "U": "未知标签集合"},
        "operators": requirement["operators"],
        "constraints": constraints,
        "task_graph": task_graph,
    }


def _demand_vector(requirement: dict[str, Any]) -> dict[str, float]:
    constraints = requirement["constraints"]
    task_ids = set(requirement["task_ids"])
    priorities = set(requirement["priorities"])
    latency_pressure = _clamp((1100 - float(constraints["latency_ms"])) / 1050)
    context_pressure = _clamp((constraints["mobility"] + constraints["interference"] + constraints["loss_rate"]) / 2)
    function_pressure = 0.62 + 0.035 * len(task_ids)
    if {"missing_detection", "unknown_detection", "temperature_outlier"} & task_ids:
        function_pressure += 0.05
    if "accuracy" in priorities:
        function_pressure += 0.04
    return {
        "structure": _clamp(0.70 + 0.12 * context_pressure + (0.04 if "robustness" in priorities else 0)),
        "function": _clamp(function_pressure),
        "constraint": _clamp(0.66 + 0.22 * latency_pressure + (0.05 if "low_latency" in priorities else 0)),
        "context": _clamp(0.55 + 0.36 * context_pressure + (0.05 if "robustness" in priorities else 0)),
        "process": _clamp(0.69 + 0.08 * latency_pressure + (0.05 if "traceability" in priorities else 0)),
    }


def _fallback_profile(name: str) -> ProtocolProfile | None:
    if not (name.startswith("g") or name.startswith("ModeQuery")):
        return None
    function_tags: tuple[str, ...] = ("discovered", "candidate")
    if "TKQ" in name or name.endswith("_2"):
        function_tags += ("topk", "hotspot")
    if "SEM" in name:
        function_tags += ("statistics",)
    if name.startswith("g10") or name.startswith("g11") or name.startswith("g12"):
        function_tags += ("optimized",)
    return ProtocolProfile(
        name=name,
        family="Discovered Protocol",
        function_tags=function_tags,
        strengths=("从现有实验目录发现", "可进入统一验证队列"),
        risk_notes=("缺少人工标注画像，需要通过仿真补充指标",),
        feature_vector={"structure": 0.70, "function": 0.68, "constraint": 0.72, "context": 0.64, "process": 0.66},
    )


def ahp_protocol_matching(requirement: dict[str, Any], available_protocols: list[str]) -> list[dict[str, Any]]:
    demand = _demand_vector(requirement)
    task_ids = set(requirement["task_ids"])
    candidates: list[dict[str, Any]] = []
    for name in available_protocols:
        profile = PROTOCOL_PROFILES.get(name) or _fallback_profile(name)
        if profile is None:
            continue
        dimension_scores: dict[str, float] = {}
        total = 0.0
        for dim, weight in DIMENSION_WEIGHTS.items():
            score = _clamp(1.0 - abs(demand[dim] - profile.feature_vector[dim]))
            dimension_scores[dim] = round(score, 3)
            total += score * weight

        bonus = 0.0
        reasons: list[str] = []
        if "hotspot_topk" in task_ids and ("hotspot" in profile.function_tags or "topk" in profile.function_tags):
            bonus += 0.045
            reasons.append("覆盖热点/Top-K 查询")
        if "mode_query" in task_ids and "mode_query" in profile.function_tags:
            bonus += 0.035
            reasons.append("可复用众数查询基线")
        if requirement["constraints"]["mobility"] > 0.7 and "dynamic" in profile.function_tags:
            bonus += 0.04
            reasons.append("适配高动态移动场景")
        if "robustness" in requirement["priorities"] and "robust" in profile.function_tags:
            bonus += 0.04
            reasons.append("鲁棒性画像较强")
        if "low_latency" in requirement["priorities"] and "low_latency" in profile.function_tags:
            bonus += 0.03
            reasons.append("低延迟候选协议")
        if not reasons:
            reasons.append("结构和约束相似度较高")

        candidates.append(
            {
                "name": profile.name,
                "family": profile.family,
                "score": round(_clamp(total + bonus), 3),
                "dimension_scores": dimension_scores,
                "dimension_labels": DIMENSION_LABELS,
                "strengths": list(profile.strengths),
                "risk_notes": list(profile.risk_notes),
                "function_tags": list(profile.function_tags),
                "fit_reason": reasons,
            }
        )
    return sorted(candidates, key=lambda item: item["score"], reverse=True)[:8]


def optimization_playbook(requirement: dict[str, Any], matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    constraints = requirement["constraints"]
    priorities = set(requirement["priorities"])
    return [
        {"name": "通信合并", "target": "减少端-边与边-云重复查询帧", "method": "把无交集子任务映射到正交编码/时分复用回复槽，共享种子和帧参数。", "expected_gain": "降低 15%-30% 帧间开销", "layer": "edge", "enabled": True},
        {"name": "参数调优", "target": "根据上一轮估计结果缩小搜索空间", "method": "利用候选价格、空槽比例和冲突比例动态调整 frame_size、batch_size 和重试轮次。", "expected_gain": "降低边缘推理耗时并保持命中率", "layer": "edge", "enabled": True},
        {"name": "上下文感知切换", "target": "适应移动、遮挡、干扰和标签规模波动", "method": "干扰升高时切换到鲁棒协议；低干扰时优先低延迟协议；异常时触发回退策略。", "expected_gain": "提升复杂环境稳定性", "layer": "edge", "enabled": constraints["mobility"] > 0.45 or constraints["interference"] > 0.45 or "robustness" in priorities},
        {"name": "LLM 交叉与变异", "target": "突破固定协议库限制", "method": "选择 AHP 前两名作为父代，生成结构化 Prompt，要求新协议通过功能、物理层和信息隔离三重验证。", "expected_gain": "生成面向场景的专用协议候选", "layer": "cloud", "enabled": bool(matches)},
        {"name": "云端反馈重排", "target": "将真实运行指标反馈给协议选择器", "method": "根据命中率、平均时延、告警误报和签名失败率更新 AHP 上下文维度。", "expected_gain": "形成可解释的自优化闭环", "layer": "cloud", "enabled": True},
        {"name": "低功耗采样控制", "target": "降低端侧读写器能耗", "method": "当货架状态稳定时降低采样频率；异常风险升高时短时提高采样密度。", "expected_gain": "在不牺牲告警的前提下降低端侧功耗", "layer": "device", "enabled": "energy" in priorities or "energy_budget" in requirement["task_ids"]},
    ]


def validation_matrix(requirement: dict[str, Any], matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    top_score = matches[0]["score"] if matches else 0
    return [
        {"gate": "功能仿真验证", "check": "命中率、召回率、热点价格识别、缺失/未知标签检测和异常告警", "threshold": f"accuracy >= {requirement['constraints']['accuracy']:.2f}", "status": "ready" if top_score >= 0.72 else "needs_generation"},
        {"gate": "物理层约束验证", "check": "bits_per_slot <= 256, frame_size <= 32768, stage_frame_sizes 合法", "threshold": "符合 EPC C1G2/课程约束", "status": "ready"},
        {"gate": "信息隔离验证", "check": "读写器不得直接读取 tag.price/private 字段做决策，只能通过协议交互推断", "threshold": "Reader/Tag 边界清晰", "status": "manual_review"},
        {"gate": "边缘部署验证", "check": "CPU、内存、平均协议耗时、上行摘要大小、HMAC 签名校验", "threshold": f"latency <= {int(requirement['constraints']['latency_ms'])} ms", "status": "ready"},
        {"gate": "云端审计验证", "check": "设计案例、运行记录、Prompt、部署清单和安全事件可追溯", "threshold": "SQLite 表完整落库", "status": "ready"},
    ]


def deployment_manifest(
    requirement: dict[str, Any],
    matches: list[dict[str, Any]],
    *,
    device_count: int = 4,
    gateway_count: int = 2,
) -> dict[str, Any]:
    selected = matches[0]["name"] if matches else "g2_2"
    fallback = matches[1]["name"] if len(matches) > 1 else "ModeQueryTCS"
    gateway_count = max(1, int(gateway_count))
    device_count = max(1, int(device_count))
    tasks_by_layer = {"device": [], "edge": [], "cloud": []}
    for task in requirement["tasks"]:
        tasks_by_layer.setdefault(task["layer"], []).append(task["title"])
    tasks_by_layer["cloud"].extend(["滑动窗口流处理", "批处理报表", "AHP/LLM 自优化反馈"])

    gateways = [
        {
            "gateway_id": f"edge-{index + 1}",
            "role": "edge-inference",
            "primary_protocol": selected,
            "fallback_protocol": fallback,
            "tasks": tasks_by_layer["edge"],
            "topics": {"uplink": f"iot/rfid/edge-{index + 1}/summary", "downlink": f"iot/rfid/edge-{index + 1}/policy"},
            "security": "HMAC-SHA256",
            "sla_ms": int(requirement["constraints"]["latency_ms"] * 0.58),
        }
        for index in range(gateway_count)
    ]
    devices = [
        {
            "device_id": f"reader-{index + 1}",
            "gateway_id": f"edge-{(index % gateway_count) + 1}",
            "sensors": ("RFID", "temperature", "humidity"),
            "tasks": tasks_by_layer["device"] or ["RFID 采集", "消息签名"],
            "sampling": "adaptive-window",
        }
        for index in range(device_count)
    ]
    return {
        "cloud": {
            "stream_processor": "sliding-window coordinator",
            "batch_processor": "global report aggregator",
            "database": "SQLite audit store",
            "feedback": "AHP re-rank + parameter tuning + LLM prompt generation",
            "tasks": tasks_by_layer["cloud"],
            "tables": ("runs", "stream_snapshots", "alerts", "design_cases", "deployment_manifests", "ai_jobs"),
        },
        "gateways": gateways,
        "devices": devices,
        "message_flow": [
            "device.scan -> device.sign -> edge.verify",
            "edge.protocol_inference -> edge.alert -> edge.summary.sign",
            "cloud.stream_window -> cloud.batch_report -> feedback.tune",
        ],
        "rollback_policy": {"trigger": "hit_rate < threshold OR latency > SLA OR signature failure", "action": f"switch {selected} -> {fallback}, upload event, schedule LLM repair"},
    }


def protocol_code_profile(protocol_name: str, scene: str = "multi_mode") -> dict[str, Any]:
    candidates = []
    scene_path = PROJECT_ROOT / scene
    if scene_path.exists():
        candidates.extend(scene_path.glob(f"generation*/{protocol_name}.py"))
        candidates.extend(scene_path.glob(f"generation_*/{protocol_name}.py"))
    candidates.extend((PROJECT_ROOT / "base_protocol").glob(f"{protocol_name}.py"))
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        return {"name": protocol_name, "found": False}

    text = path.read_text(encoding="utf-8", errors="ignore")
    try:
        tree = ast.parse(text)
        function_count = len([node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)])
        loop_count = len([node for node in ast.walk(tree) if isinstance(node, (ast.For, ast.While))])
        class_count = len([node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)])
    except SyntaxError:
        function_count = loop_count = class_count = 0
    return {
        "name": protocol_name,
        "found": True,
        "path": str(path.relative_to(PROJECT_ROOT)),
        "lines": len(text.splitlines()),
        "classes": class_count,
        "functions": function_count,
        "loops": loop_count,
        "uses_frame": "Frame(" in text,
        "uses_hash": "hash" in text.lower() or "simple_hash" in text,
        "returns_verification_data": "verification_data" in text,
    }


def build_llm_prompt(requirement: dict[str, Any], grdl: dict[str, Any], matches: list[dict[str, Any]], deployment: dict[str, Any]) -> str:
    parents = [item["name"] for item in matches[:2]]
    if len(parents) < 2:
        parents.append("ModeQueryTCS")
    task_text = "\n".join(f"- {task['title']} ({task['layer']}/{task['mode']}): {task['description']}" for task in requirement["tasks"])
    match_text = "\n".join(f"- {item['name']}: score={item['score']}, tags={','.join(item['function_tags'])}" for item in matches[:5])
    constraints = requirement["constraints"]
    return f"""你是一名 RFID 协议自动优化工程师，请基于下面的业务需求、GRDL 形式化模型和云边端部署约束，生成一个可验证的 Python RFID 查询协议。

[业务场景]
标题: {requirement['title']}
领域: {requirement['domain']}
自然语言需求: {requirement['requirement']}

[任务清单]
{task_text}

[GRDL 模型]
{grdl['dsl']}

[候选父代协议]
{match_text}
建议父代: {parents[0]} + {parents[1]}

[硬性约束]
- execute(tags, price_min, price_max) 必须返回 (modes, metric, total_time_ms, verification_data)。
- verification_data 至少包含 frame_size 和 bits_per_slot。
- bits_per_slot <= 256，frame_size <= 32768。
- 读写器不能直接读取私有标签信息做决策，必须保持信息隔离。
- 延迟目标 <= {constraints['latency_ms']} ms，准确率目标 >= {constraints['accuracy']:.3f}。
- 需要考虑 loss_rate={constraints['loss_rate']:.3f}, mobility={constraints['mobility']:.2f}, interference={constraints['interference']:.2f}。

[云边端落地]
- 端侧: {len(deployment['devices'])} 个 RFID+温湿度读写器，负责采集、轻量过滤和消息签名。
- 边缘: {len(deployment['gateways'])} 个网关，负责签名校验、协议推理、异常告警和摘要上报。
- 云端: 流处理窗口、批处理报表、SQLite 审计、AHP 重排和 LLM 修复。

[优化要求]
1. 优先复用父代协议的可靠结构，再加入场景相关的通信合并、帧长调优和上下文切换策略。
2. 输出必须是完整 Python 文件内容，不要输出 Markdown 解释。
3. 生成协议应便于通过功能仿真、物理层约束和信息隔离三重验证。
"""


def build_chart_data(requirement: dict[str, Any], matches: list[dict[str, Any]], deployment: dict[str, Any]) -> dict[str, Any]:
    constraints = requirement["constraints"]
    layer_counts = {"device": 0, "edge": 0, "cloud": 0}
    mode_counts = {"stream": 0, "batch": 0}
    for task in requirement["tasks"]:
        layer_counts[task["layer"]] = layer_counts.get(task["layer"], 0) + 1
        mode_counts[task["mode"]] = mode_counts.get(task["mode"], 0) + 1
    mode_counts["stream"] += 1
    mode_counts["batch"] += 1
    latency = int(constraints["latency_ms"])
    latency_budget = [
        {"name": "端侧采集", "value": max(25, round(latency * 0.18)), "layer": "device"},
        {"name": "端边传输", "value": max(20, round(latency * 0.10)), "layer": "network"},
        {"name": "边缘推理", "value": max(30, round(latency * 0.42)), "layer": "edge"},
        {"name": "边云摘要", "value": max(20, round(latency * 0.12)), "layer": "network"},
        {"name": "云端窗口", "value": max(30, round(latency * 0.18)), "layer": "cloud"},
    ]
    top_dimensions = matches[0]["dimension_scores"] if matches else {key: 0 for key in DIMENSION_LABELS}
    risk_cards = [
        {"name": "漏读风险", "value": round(constraints["loss_rate"], 3), "level": "high" if constraints["loss_rate"] > 0.08 else "medium" if constraints["loss_rate"] > 0.04 else "low"},
        {"name": "移动强度", "value": round(constraints["mobility"], 3), "level": "high" if constraints["mobility"] > 0.7 else "medium" if constraints["mobility"] > 0.4 else "low"},
        {"name": "环境干扰", "value": round(constraints["interference"], 3), "level": "high" if constraints["interference"] > 0.65 else "medium" if constraints["interference"] > 0.4 else "low"},
        {"name": "端侧能耗", "value": round(constraints["energy_budget"], 3), "level": "high" if constraints["energy_budget"] > 0.72 else "medium" if constraints["energy_budget"] > 0.5 else "low"},
    ]
    return {
        "protocol_scores": [{"name": item["name"], "score": item["score"]} for item in matches[:6]],
        "ahp_dimensions": [{"key": key, "label": DIMENSION_LABELS[key], "value": top_dimensions.get(key, 0)} for key in DIMENSION_LABELS],
        "task_allocation": [
            {"layer": "端侧", "key": "device", "value": layer_counts.get("device", 0) + len(deployment.get("devices", []))},
            {"layer": "边缘", "key": "edge", "value": layer_counts.get("edge", 0) + len(deployment.get("gateways", []))},
            {"layer": "云端", "key": "cloud", "value": layer_counts.get("cloud", 0) + 3},
        ],
        "processing_modes": [{"name": "流处理", "value": mode_counts["stream"]}, {"name": "一般/批处理", "value": mode_counts["batch"]}],
        "latency_budget": latency_budget,
        "risk_cards": risk_cards,
        "feedback_loop": [
            {"name": "运行指标采集", "value": "hit_rate / latency / frame_size"},
            {"name": "AHP 权重重排", "value": "context + constraint"},
            {"name": "LLM Prompt 修复", "value": "parents + GRDL + gates"},
            {"name": "边缘策略下发", "value": "primary/fallback/tuning"},
        ],
    }


def build_tech_coverage() -> list[dict[str, Any]]:
    return [
        {"layer": "感知层", "items": ("RFID 标签", "温湿度传感", "读写器采样", "标签集合 E/T/M/U")},
        {"layer": "网络层", "items": ("端-边签名消息", "边-云摘要上报", "MQTT-like topic", "HMAC-SHA256")},
        {"layer": "边缘层", "items": ("协议推理", "异常检测", "抗碰撞调度", "上下文策略切换")},
        {"layer": "云端层", "items": ("滑动窗口流处理", "批处理报表", "SQLite 审计", "反馈调参")},
        {"layer": "智能优化", "items": ("GRDL", "AHP 匹配", "LLM 交叉/变异", "三重验证")},
    ]


def build_design_case(payload: dict[str, Any], available_protocols: list[str]) -> dict[str, Any]:
    requirement = normalize_requirement(payload)
    grdl = build_grdl_model(requirement)
    matches = ahp_protocol_matching(requirement, available_protocols)
    playbook = optimization_playbook(requirement, matches)
    validation = validation_matrix(requirement, matches)
    deployment = deployment_manifest(
        requirement,
        matches,
        device_count=int(payload.get("devices") or 4),
        gateway_count=int(payload.get("gateways") or 2),
    )
    profiles = [protocol_code_profile(item["name"], str(payload.get("scene") or "multi_mode")) for item in matches[:4]]
    llm_prompt = build_llm_prompt(requirement, grdl, matches, deployment)
    charts = build_chart_data(requirement, matches, deployment)
    return {
        "requirement": requirement,
        "grdl": grdl,
        "matches": matches,
        "playbook": playbook,
        "validation": validation,
        "deployment": deployment,
        "protocol_profiles": profiles,
        "tech_coverage": build_tech_coverage(),
        "llm_prompt": llm_prompt,
        "charts": charts,
        "summary": {
            "recommended_protocol": matches[0]["name"] if matches else None,
            "recommended_score": matches[0]["score"] if matches else 0,
            "optimization_route": "GRDL 需求建模 -> AHP 协议复用 -> LLM 交叉变异 -> 三重验证 -> 云边端反馈调参",
            "deployability": "本地可运行；可迁移到真实 RFID 读写器、边缘网关、MQTT 和云端数据库。",
        },
    }


def design_case_markdown(case: dict[str, Any]) -> str:
    return json.dumps(case, ensure_ascii=False, indent=2)

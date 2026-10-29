from __future__ import annotations

import argparse
import getpass
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Validator import ProtocolValidator
from backend_tools.openai_compatible import LLMClientError, LLMConfig, OpenAICompatibleClient
from prompt_generation import build_crossover_prompt


GENERATION_PATTERN = re.compile(r"^generation_?(\d+)$")
CLASS_PATTERN = re.compile(r"class\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")

TASK_GUIDANCE = {
    "mode_query": {
        "title": "离散属性众数/多众数查询",
        "output": "返回一个候选值列表，列表中的值应尽量属于真实众数集合。",
        "objective": "在不直接读取 tag.price 的前提下，利用匿名聚合帧估计出现频率最高的属性值。",
    },
    "mean_estimation": {
        "title": "连续数值均值估计",
        "output": "返回一个只包含均值估计值的列表，例如 [estimated_mean]。",
        "objective": "面向工业传感 RFID，估计标签携带连续数值属性的总体均值，并控制通信开销。",
    },
    "variance_estimation": {
        "title": "连续数值方差/稳定性估计",
        "output": "返回一个只包含方差估计值的列表，例如 [estimated_variance]。",
        "objective": "估计标签数值属性的方差或波动强度，用于生产稳定性与异常波动监测。",
    },
    "median_quantile": {
        "title": "中位数/分位数估计",
        "output": "返回一个只包含目标分位数估计值的列表，例如 [estimated_quantile]。",
        "objective": "估计产品质量、温湿度或能耗分布中的中位数/分位数，适配长尾分布与偏态分布。",
    },
    "cardinality_estimation": {
        "title": "标签基数/库存规模估计",
        "output": "返回一个只包含标签规模估计值的列表，例如 [estimated_cardinality]。",
        "objective": "在大规模 RFID 系统中估计标签总量或类别规模，适配盘点与缺失率评估。",
    },
    "missing_stocktaking": {
        "title": "高缺失率库存盘点",
        "output": "返回疑似存在或疑似缺失的目标集合摘要，可用整数列表表示候选 ID/桶编号。",
        "objective": "在大量标签缺失或遮挡时利用碰撞信息进行快速库存盘点与缺失检测。",
    },
    "custom_task": {
        "title": "任意自定义任务",
        "output": "严格按照用户给出的输出契约返回结果；系统会验证协议是否可执行及物理约束，不对未知业务指标强行套用固定真值。",
        "objective": "由用户用自然语言定义统计目标、输入属性、输出格式、精度要求和通信约束。",
    },
}

CUSTOM_TASK_PROFILE = "custom_task"


def validate_task_profile(task_profile: str, custom_task_text: str | None = None) -> str:
    profile = str(task_profile or "mode_query").strip()
    if profile not in TASK_GUIDANCE:
        raise ValueError(f"未知任务画像: {profile}")
    if profile == CUSTOM_TASK_PROFILE and len(str(custom_task_text or "").strip()) < 8:
        raise ValueError("选择“任意自定义任务”时，请至少填写 8 个字符的任务说明")
    return profile


def task_guidance(task_profile: str, custom_task_text: str | None = None) -> dict[str, str]:
    profile = validate_task_profile(task_profile, custom_task_text)
    guidance = dict(TASK_GUIDANCE[profile])
    if profile == CUSTOM_TASK_PROFILE:
        guidance["custom_text"] = str(custom_task_text or "").strip()
    return guidance


def _log(message: str, logger) -> None:
    logger(message)


def _load_scene_task(scene_name: str):
    task_path = PROJECT_ROOT / scene_name / "Task.py"
    if not task_path.exists():
        raise FileNotFoundError(f"找不到场景任务文件: {task_path}")

    spec = importlib.util.spec_from_file_location(f"ai_opt.task.{scene_name}", task_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 Task.py: {task_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    generate_tags = getattr(module, "generate_tags", None)
    if generate_tags is None:
        raise AttributeError(f"{task_path} 缺少 generate_tags 函数")

    simulation_config = getattr(module, "SIMULATION_CONFIG", {})
    return generate_tags, simulation_config


def _resolve_target_generation(scene_path: Path, target_generation: str) -> Path:
    if target_generation and target_generation != "next":
        return scene_path / target_generation

    generation_dirs = [p.name for p in scene_path.iterdir() if p.is_dir() and p.name.startswith("generation")]
    if not generation_dirs:
        return scene_path / "generation_0"

    parsed = []
    for name in generation_dirs:
        match = GENERATION_PATTERN.match(name)
        if match:
            parsed.append((int(match.group(1)), name))

    if not parsed:
        return scene_path / "generation_0"

    max_index, template_name = max(parsed, key=lambda item: item[0])
    if "_" in template_name:
        return scene_path / f"generation_{max_index + 1}"
    return scene_path / f"generation{max_index + 1}"


def _build_optimizer_prompt(
    *,
    scene_name: str,
    parent_a: str,
    parent_b: str,
    protocol_name: str,
    task_profile: str = "mode_query",
    custom_task_text: str | None = None,
    requirement_text: str | None = None,
    repair_feedback: str | None = None,
    previous_code: str | None = None,
) -> str:
    base_prompt, errors = build_crossover_prompt(parent_a, parent_b, scene_name)
    if errors or base_prompt is None:
        raise ValueError("; ".join(errors or ["无法构造交叉提示词。"]))

    task = task_guidance(task_profile, custom_task_text)
    requirement_block = requirement_text.strip() if requirement_text and requirement_text.strip() else "用户未提供额外自然语言需求，请根据任务画像和父代协议自行设计。"

    extra_requirements = f"""

任务画像：
- 任务类型：{task['title']}
- 优化目标：{task['objective']}
- 输出约定：{task['output']}
- 用户需求：{requirement_block}
{f'- 自定义任务说明（必须遵守）：{task["custom_text"]}' if task_profile == CUSTOM_TASK_PROFILE else ''}

平台定位：
本平台不是只面向众数查询，而是面向多维统计场景的 RFID 协议自优化平台。你需要把父代协议中的时隙帧、碰撞利用、位图/草图估计、自适应帧长、分批验证等机制抽象成可复用的协议基因，并针对当前任务画像生成可执行的专用协议。

附加硬性要求：
1. 输出必须是完整的 Python 文件内容，不要输出 Markdown、解释文字或额外说明。
2. 生成类名必须严格为 {protocol_name}，且该类必须继承 IProtocol。
3. 文件内必须包含必要 import，使代码可直接保存为 {protocol_name}.py 后运行。
4. execute() 必须返回 (estimated_values, metric, total_time_ms, verification_data)。estimated_values 必须是 list；对于均值、方差、中位数、分位数、基数等单值估计任务，返回单元素列表。
5. verification_data 至少必须包含 frame_size 和 bits_per_slot；如果存在多阶段，还应补充 stage_frame_sizes 与 stage_bits_per_slot。
6. 优先保证约束验证能通过：bits_per_slot <= 256，frame_size <= 32768。
7. 仍然必须遵守信息隔离原则，不允许读写器直接依据 tag.price 做决策；如果仿真需要标签读取自身属性，请封装到 `_tag_response_*` 或 `_simulate_tag_*` 方法中，读写器主流程只处理匿名聚合帧。
8. 对 execute() 返回的 estimated_values 必须保守，不要通过猜测堆出大量候选值；宁可少报，也不要返回明显无关的大列表。
"""

    if repair_feedback:
        extra_requirements += f"""

上一轮代码未满足要求，请针对以下问题修复：
{repair_feedback}
"""
    if previous_code:
        extra_requirements += f"""

上一轮代码如下，请在此基础上修复，而不是完全偏离原方案：
{previous_code}
"""

    return base_prompt + "\n" + extra_requirements.strip() + "\n"


def _extract_code(text: str) -> str:
    stripped = text.strip()
    fenced = re.findall(r"```(?:python)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced[0].strip()
    return stripped


def _enforce_class_name(code: str, protocol_name: str) -> str:
    stripped = code.strip()
    match = re.search(r"class\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", stripped)
    if not match:
        raise ValueError("AI 返回代码中未找到类定义。")
    current_name = match.group(1)
    if current_name == protocol_name:
        return stripped + "\n"
    start, end = match.span(1)
    renamed = stripped[:start] + protocol_name + stripped[end:]
    return renamed + "\n"


def _load_protocol_instance(protocol_file: Path, protocol_name: str):
    spec = importlib.util.spec_from_file_location(f"ai_opt.generated.{protocol_name}", protocol_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载协议文件: {protocol_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    protocol_class = getattr(module, protocol_name)
    return protocol_class()


def _scan_information_isolation(protocol_file: Path) -> dict[str, Any]:
    try:
        text = protocol_file.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return {"passed": False, "warnings": [f"无法读取协议源码: {exc}"], "warning_count": 1}

    patterns = [
        ("tag.price", "直接访问 tag.price"),
        ("getattr(tag, \"price\")", "通过 getattr(tag, \"price\") 访问私有属性"),
        ("getattr(tag, 'price')", "通过 getattr(tag, 'price') 访问私有属性"),
    ]
    warnings: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for needle, message in patterns:
            if needle in stripped:
                warnings.append({"line": line_no, "rule": message, "code": stripped[:160]})
    return {
        "passed": not warnings,
        "warnings": warnings[:20],
        "warning_count": len(warnings),
        "note": "启发式静态扫描：仿真中允许标签侧响应读取自身属性，但读写器决策逻辑不应直接依赖单个标签私有值。",
    }


def _numeric_attribute_values(tags: list[Any]) -> list[float]:
    values: list[float] = []
    for tag in tags:
        try:
            values.append(float(getattr(tag, "price")))
        except (TypeError, ValueError):
            continue
    return values


def _single_value_hit(estimated: list[Any], target: list[Any], *, relative_tolerance: float = 0.15) -> bool:
    if not estimated or not target:
        return False
    try:
        estimated_value = float(estimated[0])
        target_value = float(target[0])
    except (TypeError, ValueError):
        return False
    absolute_error = abs(estimated_value - target_value)
    allowed_error = max(1.0, abs(target_value) * relative_tolerance)
    return absolute_error <= allowed_error


def _target_values_for_profile(tags: list[Any], true_info: dict[str, Any], task_profile: str) -> list[Any]:
    if task_profile == CUSTOM_TASK_PROFILE:
        return []
    values = _numeric_attribute_values(tags)
    if task_profile == "mode_query":
        return list(true_info.get("modes", []))
    if task_profile == "mean_estimation":
        return [sum(values) / len(values)] if values else []
    if task_profile == "variance_estimation":
        if not values:
            return []
        mean_value = sum(values) / len(values)
        return [sum((value - mean_value) ** 2 for value in values) / len(values)]
    if task_profile == "median_quantile":
        if not values:
            return []
        sorted_values = sorted(values)
        mid = len(sorted_values) // 2
        if len(sorted_values) % 2:
            return [sorted_values[mid]]
        return [(sorted_values[mid - 1] + sorted_values[mid]) / 2]
    if task_profile == "cardinality_estimation":
        return [len(tags)]
    if task_profile == "missing_stocktaking":
        missing = true_info.get("missing_ids") or true_info.get("missing") or []
        return list(missing)
    return list(true_info.get("modes", []))


def _profile_metrics(estimated_values: list[Any], target_values: list[Any], task_profile: str) -> tuple[bool, float, float]:
    if task_profile in {"mode_query", "missing_stocktaking"}:
        estimated_set = set(estimated_values or [])
        target_set = set(target_values or [])
        overlap_count = len(estimated_set & target_set)
        hit = bool(estimated_set) and bool(target_set) and estimated_set.issubset(target_set)
        precision = (overlap_count / len(estimated_set)) if estimated_set else 0.0
        recall = (overlap_count / len(target_set)) if target_set else 0.0
        return hit, precision, recall

    hit = _single_value_hit(estimated_values, target_values)
    score = 1.0 if hit else 0.0
    return hit, score, score


def _validate_protocol(
    protocol_file: Path,
    protocol_name: str,
    scene_name: str,
    validate_num_tags: int,
    task_profile: str,
    custom_task_text: str | None = None,
) -> dict[str, Any]:
    task_profile = validate_task_profile(task_profile, custom_task_text)
    generate_tags, simulation_config = _load_scene_task(scene_name)
    price_min = int(simulation_config.get("PRICE_MIN", 1))
    price_max = int(simulation_config.get("PRICE_MAX", 1000))
    num_tags = max(20, int(validate_num_tags))

    protocol_instance = _load_protocol_instance(protocol_file, protocol_name)
    information_isolation = _scan_information_isolation(protocol_file)
    validation_tags, _ = generate_tags(num_tags, price_min, price_max)
    validator = ProtocolValidator(protocol_name=protocol_name, protocol_instance=protocol_instance)
    is_valid, report = validator.validate(validation_tags, price_min, price_max)

    smoke_tags, true_info = generate_tags(num_tags, price_min, price_max)
    estimated_values, metric, exec_time_ms, _ = protocol_instance.execute(smoke_tags, price_min, price_max)
    estimated_values = list(estimated_values or [])
    target_values = _target_values_for_profile(smoke_tags, true_info if isinstance(true_info, dict) else {}, task_profile)
    if task_profile == CUSTOM_TASK_PROFILE:
        # There is no universal business truth for an arbitrary natural-language task.
        # Treat a successfully executed list-shaped result as the smoke contract and
        # report semantic accuracy as unavailable instead of inventing a target.
        smoke_hit = isinstance(estimated_values, list)
        smoke_precision = None
        smoke_recall = None
    else:
        smoke_hit, smoke_precision, smoke_recall = _profile_metrics(estimated_values, target_values, task_profile)

    return {
        "is_valid": bool(is_valid),
        "report": report,
        "information_isolation_passed": information_isolation["passed"],
        "information_isolation": information_isolation,
        "smoke_hit": smoke_hit,
        "estimated_values": estimated_values,
        "target_values": target_values,
        "estimated_modes": estimated_values,
        "true_modes": target_values,
        "smoke_precision": smoke_precision,
        "smoke_recall": smoke_recall,
        "metric": metric,
        "exec_time_ms": exec_time_ms,
        "task_profile": task_profile,
        "task_title": task_guidance(task_profile, custom_task_text)["title"],
        "custom_task_text": str(custom_task_text or "").strip() if task_profile == CUSTOM_TASK_PROFILE else "",
        "semantic_validation": "not_available" if task_profile == CUSTOM_TASK_PROFILE else "evaluated",
        "validation_scope": "execution_and_output_contract" if task_profile == CUSTOM_TASK_PROFILE else "execution_and_semantic_target",
    }


def run_ai_optimization_workflow(
    *,
    scene_name: str,
    parent_a: str,
    parent_b: str,
    protocol_name: str,
    target_generation: str,
    base_url: str | None = None,
    model: str | None = None,
    endpoint_path: str | None = None,
    api_key_env: str | None = None,
    api_key_value: str | None = None,
    temperature: float,
    max_tokens: int,
    timeout_seconds: int,
    validate_num_tags: int,
    max_repair_rounds: int,
    dry_run: bool,
    llm_config: LLMConfig | None = None,
    retry_attempts: int = 3,
    task_profile: str = "mode_query",
    requirement_text: str | None = None,
    custom_task_text: str | None = None,
    logger=print,
) -> dict[str, Any]:
    task_profile = validate_task_profile(task_profile, custom_task_text)
    scene_path = PROJECT_ROOT / scene_name
    if not scene_path.exists():
        raise FileNotFoundError(f"场景不存在: {scene_path}")

    target_dir = _resolve_target_generation(scene_path, target_generation)
    target_file = target_dir / f"{protocol_name}.py"
    prompt_dir = scene_path / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = prompt_dir / f"ai_{task_profile}_{protocol_name}_{parent_a}_{parent_b}.txt"

    initial_prompt = _build_optimizer_prompt(
        scene_name=scene_name,
        parent_a=parent_a,
        parent_b=parent_b,
        protocol_name=protocol_name,
        task_profile=task_profile,
        requirement_text=requirement_text,
        custom_task_text=custom_task_text,
    )
    prompt_file.write_text(initial_prompt, encoding="utf-8")
    _log(f"已生成 AI 优化提示词: {prompt_file}", logger)
    _log(f"目标输出路径: {target_file}", logger)

    if dry_run:
        return {
            "status": "dry_run",
            "prompt_file": str(prompt_file),
            "target_file": str(target_file),
            "protocol_exists": False,
            "artifact_note": "预览任务只生成 Prompt，不调用模型，也不会创建协议文件。",
        }

    config = llm_config or LLMConfig.from_sources(
        base_url=base_url,
        model=model,
        endpoint_path=endpoint_path,
        api_key=api_key_value,
        api_key_env=api_key_env,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        retry_attempts=retry_attempts,
    )
    client = OpenAICompatibleClient(config)
    _log(f"模型配置已解析: {config.model} @ {config.base_url}（{'自动识别接口' if config.endpoint_path == 'auto' else config.endpoint_path}）", logger)

    system_prompt = (
        "你是一个严格的 Python 协议生成器。"
        "你只能输出单个 .py 文件的源码，不要输出 Markdown，不要解释，不要道歉。"
    )

    previous_code = None
    repair_feedback = None
    last_validation = None
    attempts = max(1, int(max_repair_rounds) + 1)

    for attempt in range(1, attempts + 1):
        prompt_text = _build_optimizer_prompt(
            scene_name=scene_name,
            parent_a=parent_a,
            parent_b=parent_b,
            protocol_name=protocol_name,
            task_profile=task_profile,
            requirement_text=requirement_text,
            custom_task_text=custom_task_text,
            repair_feedback=repair_feedback,
            previous_code=previous_code,
        )
        _log(f"--- AI 自优化第 {attempt} 轮 ---", logger)
        _log(f"正在请求大模型（第 {attempt} 轮），等待候选协议源码...", logger)
        response_text = client.generate_text(system_prompt=system_prompt, user_prompt=prompt_text)
        _log(f"已收到模型响应，长度 {len(response_text)} 字符，正在抽取 Python 源码。", logger)
        code = _enforce_class_name(_extract_code(response_text), protocol_name)

        target_dir.mkdir(parents=True, exist_ok=True)
        target_file.write_text(code, encoding="utf-8")
        previous_code = code
        _log(f"已写入候选协议: {target_file}", logger)

        _log("正在执行协议物理约束、任务烟测和信息隔离验证。", logger)
        last_validation = _validate_protocol(
            target_file, protocol_name, scene_name, validate_num_tags, task_profile, custom_task_text
        )
        _log(last_validation["report"], logger)
        precision_text = "n/a" if last_validation["smoke_precision"] is None else f"{last_validation['smoke_precision']:.3f}"
        recall_text = "n/a" if last_validation["smoke_recall"] is None else f"{last_validation['smoke_recall']:.3f}"
        _log(
            f"任务烟测: hit={last_validation['smoke_hit']} precision={precision_text} recall={recall_text} estimated={last_validation['estimated_values']} target={last_validation['target_values']} time_ms={last_validation['exec_time_ms']:.2f}",
            logger,
        )
        if not last_validation["information_isolation_passed"]:
            _log(
                f"信息隔离静态扫描告警: {last_validation['information_isolation']['warning_count']} 处直接属性访问风险",
                logger,
            )

        if last_validation["is_valid"] and last_validation["smoke_hit"]:
            _log("候选协议通过验证，自动生成流程完成。", logger)
            return {
                "status": "success",
                "target_file": str(target_file),
                "prompt_file": str(prompt_file),
                "protocol_exists": target_file.exists(),
                "attempts": attempt,
                "validation": last_validation,
            }

        repair_feedback = (
            last_validation["report"]
            + "\n"
            + f"任务烟测命中: {last_validation['smoke_hit']}，precision={last_validation['smoke_precision']}，recall={last_validation['smoke_recall']}，estimated_values={last_validation['estimated_values']}，target_values={last_validation['target_values']}"
        )
        _log("候选协议未完全通过，准备进入自修复轮次。", logger)

    _log("达到最大自修复轮数，返回最后候选协议和验证报告。", logger)
    return {
        "status": "failed",
        "target_file": str(target_file),
        "prompt_file": str(prompt_file),
        "protocol_exists": target_file.exists(),
        "attempts": attempts,
        "validation": last_validation,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="通过 OpenAI 兼容 API 生成并自修复 RFID 协议。")
    parser.add_argument("--scene", required=True, help="目标场景目录")
    parser.add_argument("--parent-a", required=True, help="父代协议 A 名称")
    parser.add_argument("--parent-b", required=True, help="父代协议 B 名称")
    parser.add_argument("--protocol-name", required=True, help="输出协议类名与文件名")
    parser.add_argument("--target-generation", default="next", help="目标世代目录，默认 next")
    parser.add_argument("--base-url", default=None, help="OpenAI 兼容接口 base URL")
    parser.add_argument("--model", default=None, help="模型名称")
    parser.add_argument("--endpoint-path", default=None, help="接口路径，默认 /v1/chat/completions")
    parser.add_argument("--api-key-env", default="GEEK_API_KEY", help="存放 API Key 的环境变量名")
    parser.add_argument("--prompt-key", action="store_true", help="运行时以不回显方式输入 API Key")
    parser.add_argument("--temperature", type=float, default=0.2, help="采样温度")
    parser.add_argument("--max-tokens", type=int, default=2800, help="最大输出 token 数")
    parser.add_argument("--timeout-seconds", type=int, default=120, help="网络超时秒数")
    parser.add_argument("--validate-num-tags", type=int, default=120, help="验证时使用的标签数量")
    parser.add_argument("--max-repair-rounds", type=int, default=1, help="最大自修复轮数")
    parser.add_argument("--task-profile", default="mode_query", choices=sorted(TASK_GUIDANCE), help="协议任务画像")
    parser.add_argument("--requirement-text", default=None, help="额外自然语言需求")
    parser.add_argument("--custom-task-text", default=None, help="任意自定义任务说明（task-profile=custom_task 时必填）")
    parser.add_argument("--dry-run", action="store_true", help="仅生成提示词和目标路径，不调用 API")
    parser.add_argument("--list-models", action="store_true", help="仅调用 /v1/models 列出模型")
    args = parser.parse_args()

    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", args.protocol_name):
        print("[错误] protocol-name 必须是合法的 Python 标识符。")
        return 2

    try:
        api_key_value = None
        if args.prompt_key:
            api_key_value = getpass.getpass("请输入 API Key: ")

        if args.list_models:
            config = LLMConfig.from_sources(
                base_url=args.base_url,
                model=args.model or "placeholder-model",
                endpoint_path=args.endpoint_path,
                api_key=api_key_value,
                api_key_env=args.api_key_env,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                timeout_seconds=args.timeout_seconds,
            )
            client = OpenAICompatibleClient(config)
            print("\n".join(client.list_models()))
            return 0

        result = run_ai_optimization_workflow(
            scene_name=args.scene,
            parent_a=args.parent_a,
            parent_b=args.parent_b,
            protocol_name=args.protocol_name,
            target_generation=args.target_generation,
            base_url=args.base_url,
            model=args.model,
            endpoint_path=args.endpoint_path,
            api_key_env=args.api_key_env,
            api_key_value=api_key_value,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout_seconds=args.timeout_seconds,
            validate_num_tags=args.validate_num_tags,
            max_repair_rounds=args.max_repair_rounds,
            dry_run=args.dry_run,
            task_profile=args.task_profile,
            requirement_text=args.requirement_text,
            custom_task_text=args.custom_task_text,
            logger=print,
        )
    except (ValueError, FileNotFoundError, ImportError, AttributeError, LLMClientError) as exc:
        print(f"[错误] {exc}")
        return 1
    except Exception as exc:
        print(f"[严重错误] {exc}")
        return 1

    print(f"结果状态: {result['status']}")
    print(f"提示词文件: {result['prompt_file']}")
    print(f"目标协议文件: {result['target_file']}")
    if result.get("validation"):
        validation = result["validation"]
        print(f"验证通过: {validation['is_valid']}")
        print(f"任务烟测命中: {validation['smoke_hit']}")
    return 0 if result["status"] in {"success", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

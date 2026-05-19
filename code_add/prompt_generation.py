from __future__ import annotations

from itertools import combinations
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


# 这里只放“父代协议交叉 + 通用约束”。具体统计任务由 ai_protocol_optimizer.py
# 追加任务画像与自然语言需求，避免把平台固定成众数查询专用工具。
PROMPT_TEMPLATE = """提示词：任务: 作为一名资深的 RFID 协议设计与优化专家，请基于给定的两个父代 RFID 协议（不要引入任何其他父代协议），创造一个全新的、功能完整的子代协议。

平台背景：
本平台定位为“面向多维统计场景的 RFID 协议自优化 Web 平台”。生成协议不应被限定为众数查询，而应能根据后续任务画像扩展到均值、方差、标准差、中位数、分位数、基数估计、缺失盘点、热点检测等 RFID/物联网统计任务。请把父代协议中的时隙帧、碰撞利用、位图/草图估计、自适应帧长、分批验证、提前终止、分层抽样等机制抽象成可复用的“协议基因”。

核心设计准则（必须严格遵守）：
1. 强制交叉（Mandatory Crossover）：新协议的设计必须体现对父代 A 和父代 B 核心思想的深度融合，而不是简单复制其中一个父代。
2. 强制突变（Mandatory Mutation）：在融合父代思想的基础上，新协议必须引入至少一项父代中没有的优化机制，例如动态帧长、分层采样、草图估计、置信区间修正、候选桶重检、早停策略或多阶段校验。
3. 任务适配（Task Adaptation）：请等待并服从后续追加的任务画像。若任务画像要求单值估计，execute() 应返回单元素 list；若任务画像要求候选集合，execute() 应返回保守且相关的候选 list。
4. 准确率与通信开销平衡：优先保证逻辑正确性，同时控制 reader 发送位数、标签响应时隙数和多阶段开销。
5. 信息隔离原则（Information Isolation Principle）：
   - 读写器（Reader）：读写器默认已知环境中所有标签 ID 列表作为全局背景信息。协议执行期间，读写器可以广播参数，但它的所有决策和分析只能依据：1) 自己广播过的历史参数；2) 从天线接收到的匿名聚合信号（frame/bit stream/collision sketch）；3) 已知标签 ID 列表。严禁在读写器决策逻辑中直接或间接访问单个标签私有属性，例如 tag.price。
   - 标签（Tag）：标签响应只能依据：1) 自身私有数据（ID 和数值属性）；2) 当前读写器广播指令。标签不得读取其他标签信息或全局通信结果。
6. 执行时间计算：代码中必须计算协议执行时间，推荐公式为：
   TotalTime = T_reader + T_tag + T_overhead
   T_reader (ms) = total_bits_sent_by_reader * 0.025
   T_tag (ms) = total_slots * bits_per_slot * 0.4
   T_overhead (ms) = (total_frames - 1) * 30

执行步骤：
第一步：交叉（Crossover）- 融合父代基因
请分析父代 A 和父代 B 的 Python 代码，从它们的帧结构、响应规则、估计逻辑、终止条件和验证数据中提取可复用设计，生成结构完整且逻辑自洽的初始子代协议。

第二步：突变（Mutation）- 引入创新优化
请在初始子代协议基础上加入一项有意义的新机制。可考虑但不限于：
1. 动态调整 frame_size、bits_per_slot 或采样轮数；
2. 用 bitmap/sketch/histogram/quantile summary 替代笨重数据结构；
3. 增加早停、异常桶重检或置信度修正；
4. 增加面向多维统计任务的参数化算子，使同一协议骨架可服务不同统计目标。

输出要求：
1. 生成一个功能完整、符合 IProtocol 接口标准的 Python 类。
2. 输出只包含可直接保存为 .py 文件运行的代码，不要输出 Markdown 或解释文字。
3. 代码中请用注释标注：
   # --- 交叉部分：融合父代 A 的...思想与父代 B 的...思想 ---
   # --- 突变部分：引入...全新机制 ---
4. execute(tags, price_min, price_max) 必须返回 (estimated_values, metric, total_time_ms, verification_data)。
5. verification_data 至少包含 frame_size 和 bits_per_slot；若有多阶段流程，也应包含 stage_frame_sizes、stage_bits_per_slot、total_frames 等可解释指标。
6. 若仿真中必须读取标签自身属性，请封装在 `_tag_response_*` 或 `_simulate_tag_*` 这类“标签侧响应”函数中；读写器主流程只能接收匿名时隙/位图/碰撞草图结果，不要直接用单个标签私有值做统计决策。

重要提示：下面父代源码中的类名、注释或历史目标可能包含“众数查询”等旧任务描述，它们只代表父代产生时的应用背景。你必须以本提示词后续追加的任务画像和用户自然语言需求为准，只复用父代机制，不继承父代的单一任务目标。

协议A：
{protocol_a_code}


协议B：
{protocol_b_code}
"""


def _find_protocol_code(search_paths: list[Path], protocol_name: str) -> str:
    """Search a scene and baseline folders for a protocol source file."""
    for search_dir in search_paths:
        if not search_dir.is_dir():
            continue

        protocol_file = search_dir / f"{protocol_name}.py"
        if protocol_file.exists():
            try:
                return protocol_file.read_text(encoding="utf-8")
            except Exception as exc:
                return f"# Error: 读取文件 {protocol_file} 失败: {exc}"

        for gen_dir in sorted(search_dir.glob("generation*")):
            if not gen_dir.is_dir():
                continue

            nested_protocol_file = gen_dir / f"{protocol_name}.py"
            if nested_protocol_file.exists():
                try:
                    return nested_protocol_file.read_text(encoding="utf-8")
                except Exception as exc:
                    return f"# Error: 读取文件 {nested_protocol_file} 失败: {exc}"

    return f"# Error: 文件 {protocol_name}.py 未在任何有效搜索路径中找到。"


def get_protocol_code(protocol_name: str, scene_path: str | Path) -> str:
    """按场景优先、基线回退的顺序读取协议源码。"""
    scene_path_obj = Path(scene_path)
    if not scene_path_obj.is_absolute():
        scene_path_obj = PROJECT_ROOT / scene_path_obj
    base_protocol_path = PROJECT_ROOT / "base_protocol"
    search_paths = [scene_path_obj, base_protocol_path]
    return _find_protocol_code(search_paths, protocol_name)


def build_crossover_prompt(parent_a_name: str, parent_b_name: str, scene_path: str | Path) -> tuple[str | None, list[str]]:
    """构造单对父代协议的交叉提示词内容。"""
    code_a = get_protocol_code(parent_a_name, scene_path)
    code_b = get_protocol_code(parent_b_name, scene_path)

    errors = []
    if "# Error:" in code_a:
        errors.append(f"未找到 '{parent_a_name}.py'")
    if "# Error:" in code_b:
        errors.append(f"未找到 '{parent_b_name}.py'")

    if errors:
        return None, errors

    prompt_content = PROMPT_TEMPLATE.format(
        protocol_a_code=code_a,
        protocol_b_code=code_b,
    )
    return prompt_content, []


def create_crossover_prompts(parent_names: list[str], scene_path: str) -> tuple[int, list[str]]:
    """根据父代协议列表生成所有两两组合的交叉提示词文件。"""
    if len(parent_names) < 2:
        return 0, ["父代协议数量不足 2 个，无法生成组合。"]

    scene_path_obj = Path(scene_path)
    prompts_dir = scene_path_obj / "prompts"
    prompts_dir.mkdir(exist_ok=True)

    generated_count = 0
    errors: list[str] = []

    print(f"--- 开始为场景 '{scene_path}' 生成交叉提示词 ---")
    print(f"父代列表: {parent_names}")

    for parent_a_name, parent_b_name in combinations(parent_names, 2):
        print(f"\n正在处理组合: ({parent_a_name}, {parent_b_name})")

        prompt_content, pair_errors = build_crossover_prompt(parent_a_name, parent_b_name, scene_path_obj)
        if pair_errors:
            for error_msg in pair_errors:
                print(f"  [错误] {error_msg}")
                errors.append(error_msg)
            continue

        file_path = prompts_dir / f"{parent_a_name}_{parent_b_name}.txt"
        try:
            file_path.write_text(prompt_content, encoding="utf-8")
            print(f"  [成功] 已生成提示词文件: {file_path}")
            generated_count += 1
        except Exception as exc:
            error_msg = f"写入文件 {file_path} 失败: {exc}"
            print(f"  [错误] {error_msg}")
            errors.append(error_msg)

    print("--- 操作完成 ---")
    return generated_count, list(dict.fromkeys(errors))

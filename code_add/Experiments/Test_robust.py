import argparse
import sys
from pathlib import Path
import importlib.util
from typing import Dict, List, Any, Tuple

import numpy as np
import pandas as pd

# Ensure project root (where base.py lives) is importable when running this file
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from base import IProtocol, Tag

VALID_FACTORS = {"dominance", "scale", "density"}


def discover_valid_sources() -> set[str]:
    sources = {"base_protocol"}
    for child in PROJECT_ROOT.iterdir():
        if not child.is_dir():
            continue
        has_task = (child / "Task.py").exists()
        has_generation = any(p.is_dir() and p.name.startswith("generation") for p in child.iterdir())
        if has_task and has_generation:
            sources.add(child.name)
    return sources


def generate_tags_single_peak(num_tags: int, price_min: int, price_max: int):
    """Generate a simple single-peak tag distribution for robustness tests."""
    prices = np.random.randint(price_min, price_max + 1, num_tags)
    if num_tags > 0:
        mode_val = np.random.randint(price_min, price_max + 1)
        mode_count = int(num_tags * 0.2)
        prices[:mode_count] = mode_val
        np.random.shuffle(prices)
    tags = [Tag(i, int(p)) for i, p in enumerate(prices)]
    return tags, {}


def load_protocol(protocol_name: str, source: str) -> IProtocol:
    """Dynamically load a protocol implementation from a scene or base_protocol."""
    source_path = PROJECT_ROOT / source
    module_path = None
    module_name_for_spec = ""

    if source == "base_protocol":
        module_path = source_path / f"{protocol_name}.py"
        module_name_for_spec = f"base.{protocol_name}"
        if not module_path.exists():
            raise ImportError(f"在 '{source}' 文件夹中找不到协议文件 '{protocol_name}.py'。")
    else:
        if not source_path.exists():
            raise ValueError(f"不支持的协议来源: {source}，可选 {sorted(discover_valid_sources())}")
        found = False
        for gen_dir in sorted(source_path.iterdir()):
            if gen_dir.is_dir() and (gen_dir / f"{protocol_name}.py").exists():
                module_path = gen_dir / f"{protocol_name}.py"
                module_name_for_spec = f"evolved.{source}.{gen_dir.name}.{protocol_name}"
                found = True
                break
        if not found:
            raise ImportError(f"在 '{source}' 的任何 generation* 文件夹中都找不到协议 '{protocol_name}'。")

    try:
        spec = importlib.util.spec_from_file_location(module_name_for_spec, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法为 '{module_path}' 创建模块规范。")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name_for_spec] = module
        spec.loader.exec_module(module)
        protocol_class = getattr(module, protocol_name)
        print(f"--- 成功加载协议: {protocol_name} from {source} ---")
        return protocol_class()
    except Exception as e:
        raise ImportError(f"从 '{module_path}' 加载协议 '{protocol_name}' 失败: {e}")


def generate_tags_for_dominance_test(
    num_tags: int, price_min: int, price_max: int, dominance_ratio: float
) -> Tuple[List[Tag], Dict[str, Any]]:
    """Generate tags with a given dominant-mode ratio."""
    true_mode = np.random.randint(price_min, price_max + 1)
    num_mode_tags = int(num_tags * dominance_ratio)
    prices = [int(true_mode)] * num_mode_tags
    other_prices = np.random.randint(price_min, price_max + 1, num_tags - num_mode_tags)
    prices.extend(list(other_prices))
    np.random.shuffle(prices)
    tags = [Tag(i, int(p)) for i, p in enumerate(prices)]
    return tags, {"mode": int(true_mode)}


def run_sensitivity_test_for_protocol(
    protocol: IProtocol,
    protocol_name: str,
    factor_name: str,
    factor_values: List[Any],
    test_config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Run one factor sweep for one protocol and return rows for CSV."""
    print(f"\n--- 正在为协议 [{protocol_name}] 测试因素: {factor_name.upper()} ---")

    all_results = []
    total_runs = test_config.get("total_runs", 10)

    for value in factor_values:
        run_times_ms = []
        num_tags = test_config["num_tags"]
        price_min = test_config["price_min"]
        price_max = test_config["price_max"]

        if factor_name == "dominance":
            print(f"  测试中... 众数标签占比 = {value:.2%}")
            generate_func = lambda: generate_tags_for_dominance_test(num_tags, price_min, price_max, float(value))
        elif factor_name == "scale":
            num_tags = int(value)
            print(f"  测试中... 标签总数 = {num_tags}")
            generate_func = lambda: generate_tags_single_peak(num_tags, price_min, price_max)
        elif factor_name == "density":
            price_max = int(value)
            print(f"  测试中... 价格范围 = [{price_min}, {price_max}]")
            generate_func = lambda: generate_tags_single_peak(num_tags, price_min, price_max)
        else:
            raise ValueError(f"未知的因素名称: {factor_name}")

        for _ in range(total_runs):
            tags, _ = generate_func()
            _, _, exec_time_ms, _ = protocol.execute(tags, price_min, price_max)
            run_times_ms.append(exec_time_ms)

        avg_time_s = float(np.mean(run_times_ms) / 1000.0)
        print(f"    -> 平均执行时间: {avg_time_s:.4f} s")

        all_results.append(
            {
                "factor_name": factor_name,
                "factor_value": value,
                "protocol_name": protocol_name,
                "avg_time_s": avg_time_s,
            }
        )

    return all_results


def parse_protocol_specs(protocol_specs: str) -> List[Dict[str, str]]:
    """Parse 'name:source,name2:source' into structured list."""
    specs = []
    for item in [p.strip() for p in protocol_specs.split(",") if p.strip()]:
        if ":" not in item:
            raise ValueError(f"协议配置格式错误: '{item}'，应为 name:source")
        name, source = [x.strip() for x in item.split(":", 1)]
        if not name or not source:
            raise ValueError(f"协议配置格式错误: '{item}'")
        valid_sources = discover_valid_sources()
        if source not in valid_sources:
            raise ValueError(f"不支持的 source '{source}'，可选: {sorted(valid_sources)}")
        specs.append({"name": name, "source": source})

    if not specs:
        raise ValueError("协议列表不能为空。")
    return specs


def parse_factors(factors: str) -> List[str]:
    selected = [f.strip() for f in factors.split(",") if f.strip()]
    if not selected:
        raise ValueError("因素列表不能为空。")
    invalid = [f for f in selected if f not in VALID_FACTORS]
    if invalid:
        raise ValueError(f"无效因素: {invalid}，可选: {sorted(VALID_FACTORS)}")
    return selected


def main():
    parser = argparse.ArgumentParser(description="运行鲁棒性/敏感度实验并输出CSV。")
    parser.add_argument(
        "--protocols",
        type=str,
        default="g10_4:single_mode,g8_3:multi_mode",
        help="协议列表，格式: name:source,name2:source。source 可以是任意带 Task.py 和 generation* 的场景目录，或 base_protocol",
    )
    parser.add_argument(
        "--factors",
        type=str,
        default="density",
        help="要运行的因素，逗号分隔: dominance,scale,density",
    )
    parser.add_argument("--num-tags", type=int, default=10000, help="基础标签数量")
    parser.add_argument("--price-min", type=int, default=1, help="价格下限")
    parser.add_argument("--price-max", type=int, default=1000, help="价格上限")
    parser.add_argument("--total-runs", type=int, default=10, help="每个点重复次数")
    parser.add_argument("--point-count", type=int, default=10, help="每个因素采样点数量")
    parser.add_argument("--density-max", type=int, default=10000, help="density因素的价格范围上限最大值")
    parser.add_argument("--output", type=str, default="sensitivity_results_2.csv", help="输出CSV路径")
    args = parser.parse_args()

    try:
        protocols_to_test = parse_protocol_specs(args.protocols)
        factors_to_run = parse_factors(args.factors)
    except ValueError as e:
        print(f"[错误] 参数解析失败: {e}")
        return

    base_config = {
        "num_tags": int(args.num_tags),
        "price_min": int(args.price_min),
        "price_max": int(args.price_max),
        "total_runs": int(args.total_runs),
    }

    loaded_protocols = {}
    try:
        for p_info in protocols_to_test:
            loaded_protocols[p_info["name"]] = load_protocol(p_info["name"], p_info["source"])
    except (ImportError, FileNotFoundError, ValueError) as e:
        print(f"[错误] 加载协议失败: {e}")
        return

    point_count = max(2, int(args.point_count))
    dominance_values = np.linspace(0.05, 0.5, point_count).tolist()
    scale_values = np.logspace(3, 5, point_count, dtype=int).tolist()
    density_values = np.logspace(3, np.log10(max(1000, int(args.density_max))), point_count, dtype=int).tolist()

    all_test_results = []
    for factor in factors_to_run:
        if factor == "dominance":
            values = dominance_values
            config = base_config.copy()
        elif factor == "scale":
            values = scale_values
            config = base_config.copy()
        elif factor == "density":
            values = density_values
            config = base_config.copy()
            config["num_tags"] = base_config["num_tags"]
        else:
            continue

        for p_info in protocols_to_test:
            protocol_instance = loaded_protocols[p_info["name"]]
            all_test_results.extend(
                run_sensitivity_test_for_protocol(
                    protocol_instance,
                    p_info["name"],
                    factor,
                    values,
                    config,
                )
            )

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    results_df = pd.DataFrame(all_test_results)
    results_df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"\n{'='*25}")
    print(f"所有实验已完成！结果已全部保存至: {output_path}")
    print(f"{'='*25}")


if __name__ == "__main__":
    main()

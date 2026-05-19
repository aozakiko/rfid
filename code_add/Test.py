import time
import argparse
import fnmatch
import sys
from pathlib import Path
from importlib import import_module
from importlib.util import spec_from_file_location
from typing import Dict, List, Any, Callable

# 确保可以找到 base.py 和 Validator.py
try:
    from base import IProtocol
    from Validator import ProtocolValidator
except ImportError:
    print("错误: 无法导入 'base' 或 'Validator' 模块。")
    print("请确保 Test.py, base.py, 和 Validator.py 文件位于同一个目录下。")
    sys.exit(1)

def discover_protocols(scene_path: Path) -> Dict[str, IProtocol]:
    """
    在指定的场景路径下，自动发现并加载所有世代文件夹中的协议。
    """
    print(f"--- 正在从场景 '{scene_path.name}' 目录自动发现可用协议 ---")
    
    if not scene_path.is_dir():
        print(f"[错误] 场景目录不存在: {scene_path}")
        return {}

    if str(scene_path) not in sys.path:
        sys.path.insert(0, str(scene_path))

    available_protocols = {}
    
    gen_dirs = sorted([p for p in scene_path.iterdir() if p.is_dir() and p.name.startswith('generation')])
    
    if not gen_dirs:
        print(f"[警告] 在 '{scene_path.name}' 目录下未找到 'generation*' 文件夹。")
        return {}
        
    print(f"发现协议目录: {[d.name for d in gen_dirs]}")

    for gen_dir in gen_dirs:
        for proto_file in sorted(gen_dir.glob('*.py')):
            module_name = proto_file.stem
            if module_name == '__init__':
                continue

            try:
                module_path = f"{gen_dir.name}.{module_name}"
                module = import_module(module_path)
                
                from importlib import reload
                reload(module)
                
                protocol_class = getattr(module, module_name)
                available_protocols[module_name] = protocol_class()
                print(f"  [成功] 发现并加载协议: {module_name}")
            except (ImportError, AttributeError, TypeError) as e:
                print(f"  [警告] 无法从 {proto_file.name} 加载协议 ({module_path}): {e}")
                
    print(f"--- 共发现 {len(available_protocols)} 个协议 ---\n")
    return available_protocols

def run_performance_test(
    protocol_name: str, 
    protocol_instance: IProtocol, 
    config: Dict[str, Any],
    generate_tags_func: Callable
) -> Dict[str, Any]:
    """
    为单个协议执行性能测试并返回结果字典。
    """
    passed_runs = 0
    total_execution_time = 0.0
    
    num_tags, price_min, price_max, total_runs = config['num_tags'], config['price_min'], config['price_max'], config['total_runs']
    
    print(f"\n--- 正在为协议 [{protocol_name}] 在场景 [{config['scene']}] 下执行 {total_runs} 次性能模拟 ---")
    
    for i in range(total_runs):
        tag_set, true_info = generate_tags_func(num_tags, price_min, price_max)
        
        try:
            estimated_modes, _, execution_time, _ = protocol_instance.execute(
                tag_set, price_min, price_max
            )
            
            total_execution_time += execution_time
            
            is_correct = False
            true_modes = true_info.get('modes', [])
            if estimated_modes and true_modes:
                if set(estimated_modes).issubset(set(true_modes)):
                    is_correct = True
            
            if is_correct:
                passed_runs += 1
        except Exception as e:
            print(f"  在第 {i+1} 轮测试中协议 '{protocol_name}' 发生运行时错误: {e}")
            break
            
        if total_runs >= 10 and (i + 1) % (total_runs // 10) == 0:
            print(f"  ...进度: {(i + 1) * 100 / total_runs:.0f}%")
            
    avg_execution_time = total_execution_time / total_runs if total_runs > 0 else 0
    pass_rate = (passed_runs / total_runs) * 100 if total_runs > 0 else 0
    
    return {
        "name": protocol_name,
        "pass_rate": pass_rate,
        "avg_time": avg_execution_time
    }

def print_summary_report(results: List[Dict[str, Any]], config: Dict[str, Any]):
    """
    打印所有已测试协议的最终汇总报告。
    """
    if not results:
        print("没有可报告的性能测试结果。")
        return
        
    sorted_results = sorted(results, key=lambda x: (-x['pass_rate'], x['avg_time']))
    
    print(f"\n{'='*30} 最终性能测试汇总报告 {'='*30}")
    print(f"测试场景: {config['scene']}")
    print(f"测试环境: {config['num_tags']} 个标签, 价格范围 [{config['price_min']}, {config['price_max']}], "
          f"共运行 {config['total_runs']} 次。")
    print("-" * 90)
    print(f"{'协议名称':<30} | {'测试通过率':<15} | {'平均执行时间 (ms)':<20} | {'排名'}")
    print("-" * 90)
    
    for rank, result in enumerate(sorted_results, 1):
        print(f"{result['name']:<30} | {result['pass_rate']:>12.2f}% | {result['avg_time']:>18.2f} |  {rank}")
        
    print("-" * 90)

def main():
    """
    主函数，负责协议发现、参数解析、验证和调度测试。
    """
    parser = argparse.ArgumentParser(description="运行并测试指定场景下的RFID众数查询协议。")
    
    parser.add_argument(
        '--scene', 
        type=str, 
        required=True,
        help="必须指定要测试的场景文件夹名称。"
    )
    parser.add_argument(
        'protocols_to_run', 
        nargs='*', 
        default=['all'], 
        help="要运行的协议名称。可以使用通配符 (例如 'g1_*') 或 'all' (默认) 运行所有协议。"
    )

    # Optional overrides to make quick runs possible without editing Task.py
    parser.add_argument('--num-tags', type=int, default=None, help='覆盖标签数量 NUM_TAGS')
    parser.add_argument('--total-runs', type=int, default=None, help='覆盖重复运行次数 TOTAL_RUNS')
    parser.add_argument('--price-min', type=int, default=None, help='覆盖价格下限 PRICE_MIN')
    parser.add_argument('--price-max', type=int, default=None, help='覆盖价格上限 PRICE_MAX')
    
    args = parser.parse_args()
    
    # 构建并验证场景路径
    project_root = Path(__file__).resolve().parent
    scene_path = project_root / args.scene
    if not scene_path.is_dir():
        print(f"错误: 场景文件夹 '{scene_path}' 不存在。")
        return

    # 从场景文件夹动态加载 Task.py 和配置
    try:
        task_file_path = scene_path / "Task.py"
        if not task_file_path.exists():
            raise FileNotFoundError
        spec = spec_from_file_location("Task", task_file_path)
        task_module = spec.loader.load_module()
        generate_tags_func = getattr(task_module, 'generate_tags')
        
        # --- *** 修改点: 从 Task.py 加载配置 *** ---
        simulation_config = getattr(task_module, 'SIMULATION_CONFIG', {})
        print(f"\n--- 成功加载测试任务: {task_file_path} ---")

    except (FileNotFoundError, ImportError, AttributeError) as e:
        print(f"错误：无法从场景 '{args.scene}' 加载 Task.py。请确保该文件存在且包含 'generate_tags' 函数和 'SIMULATION_CONFIG' 字典。错误信息: {e}")
        return

    # --- *** 修改点: 使用加载的配置或默认值构建config字典 *** ---
    config = {
        'num_tags': simulation_config.get('NUM_TAGS', 100000),
        'total_runs': simulation_config.get('TOTAL_RUNS', 100),
        'price_min': simulation_config.get('PRICE_MIN', 1),
        'price_max': simulation_config.get('PRICE_MAX', 1000),
        'scene': args.scene,
    }

    # Apply CLI overrides if provided
    if args.num_tags is not None:
        config['num_tags'] = args.num_tags
    if args.total_runs is not None:
        config['total_runs'] = args.total_runs
    if args.price_min is not None:
        config['price_min'] = args.price_min
    if args.price_max is not None:
        config['price_max'] = args.price_max
    
    all_protocols = discover_protocols(scene_path)
    if not all_protocols:
        print("错误：在指定场景中未找到任何协议。请检查文件结构。")
        return

    targets = args.protocols_to_run
    selected_protocols = {}
    if 'all' in targets:
        selected_protocols = all_protocols
    else:
        for pattern in targets:
            for name, instance in all_protocols.items():
                if fnmatch.fnmatch(name, pattern):
                    selected_protocols[name] = instance
    
    if not selected_protocols:
        print(f"错误：根据输入 '{' '.join(targets)}' 未找到任何匹配的协议。")
        print("可用协议包括:", list(all_protocols.keys()))
        return

    print(f"--- 将要测试以下协议: {', '.join(selected_protocols.keys())} ---\n")

    performance_results = []
    validation_tags, _ = generate_tags_func(100, config['price_min'], config['price_max'])

    for name, instance in selected_protocols.items():
        print(f"\n{'='*25} 开始处理协议: {name} {'='*25}")
        
        validator = ProtocolValidator(protocol_name=name, protocol_instance=instance)
        is_valid, report = validator.validate(validation_tags, config['price_min'], config['price_max'])
        print(report)
        
        if is_valid:
            print(f"✅ 协议 [{name}] 通过约束验证，开始批量性能测试。")
            result = run_performance_test(name, instance, config, generate_tags_func)
            performance_results.append(result)
        else:
            print(f"❌ 协议 [{name}] 未通过约束验证，性能测试已跳过。")
    
    print_summary_report(performance_results, config)

if __name__ == "__main__":
    main()


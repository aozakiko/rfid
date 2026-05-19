import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def resolve_path(path_str: str) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p
    script_dir = Path(__file__).resolve().parent
    if (script_dir / p).exists():
        return script_dir / p
    return Path.cwd() / p


def load_and_prepare_data(scenario: str, data_path: str = 'evolution_data_combined.json'):
    json_file_path = resolve_path(data_path)

    try:
        with open(json_file_path, 'r', encoding='utf-8') as f:
            full_data = json.load(f)
    except FileNotFoundError:
        print(f"错误: 无法在路径 '{json_file_path}' 找到数据文件。")
        return None, None

    if scenario not in full_data:
        print(f"错误: 在数据文件中找不到场景 '{scenario}'。可用场景: {list(full_data.keys())}")
        return None, None

    data = full_data[scenario]
    records = []

    for indiv in data.get('base_algorithms', []):
        records.append(
            {
                'generation': 0,
                'name': indiv['name'],
                'parent_A': None,
                'parent_B': None,
                'correction_count': 0,
                'accuracy': indiv.get('accuracy', 1.0),
                'time': indiv['time'],
                'fitness': indiv.get('fitness'),
            }
        )

    for gen_data in data.get('generations', []):
        gen_num = gen_data['generation']
        for indiv in gen_data['individuals']:
            records.append(
                {
                    'generation': gen_num,
                    'name': indiv['name'],
                    'parent_A': indiv.get('parent_A'),
                    'parent_B': indiv.get('parent_B'),
                    'correction_count': indiv.get('correction_count', 0),
                    'accuracy': indiv.get('accuracy', 1.0),
                    'time': indiv['time'],
                    'fitness': indiv.get('fitness'),
                }
            )

    df = pd.DataFrame(records)
    if df.empty:
        print(f"警告: 场景 '{scenario}' 没有加载到任何数据。")
        return pd.DataFrame(), pd.DataFrame()

    def calculate_custom_metrics(group):
        top_5_fastest = group.sort_values('time').head(5)
        avg_top5_time = top_5_fastest['time'].mean()
        avg_overall_accuracy = group['accuracy'].mean()
        return pd.Series({'avg_time': avg_top5_time, 'avg_accuracy': avg_overall_accuracy})

    avg_metrics = df.groupby('generation').apply(calculate_custom_metrics).reset_index()

    if 'fitness' in df.columns and df['fitness'].notna().all():
        df_sorted = df.sort_values(by=['generation', 'fitness'], ascending=[True, False])
    else:
        df_sorted = df.sort_values(by=['generation', 'time'], ascending=[True, True])

    best_individuals = df_sorted.drop_duplicates('generation', keep='first')

    stats_df = pd.merge(best_individuals[['generation', 'time', 'accuracy']], avg_metrics, on='generation')
    stats_df.rename(columns={'time': 'best_time', 'accuracy': 'best_accuracy'}, inplace=True)
    stats_df.sort_values('generation', inplace=True)

    return df, stats_df


def plot_performance_evolution(stats_df: pd.DataFrame, scenario: str, output_dir: Path, show_plot: bool):
    fig, ax1 = plt.subplots(figsize=(7, 5))
    ax2 = ax1.twinx()

    line1, = ax1.plot(stats_df['generation'], stats_df['best_time'] / 1000, 'o-', color='tab:blue', label='最优执行时间 (秒)')
    ax1.set_xlabel('进化代数 (Generation)', fontsize=14)
    ax1.set_ylabel('执行时间 (秒)', fontsize=14)
    ax1.tick_params(axis='y')
    ax1.grid(True, which='both', linestyle='--', linewidth=1)

    line2, = ax2.plot(stats_df['generation'], stats_df['best_accuracy'] * 100, 's--', color='tab:red', label='最优准确率 (%)')
    ax2.set_ylabel('准确率 (%)', fontsize=14)
    ax2.tick_params(axis='y')
    ax2.set_ylim(min(90, (stats_df['best_accuracy'].min() * 100) - 5), 101)

    ax1.set_title('最优个体性能进化曲线', fontsize=16)
    ax1.set_xticks(np.arange(0, 11, 1))
    lines = [line1, line2]
    ax1.legend(lines, [l.get_label() for l in lines], loc='lower center', fontsize=14)
    plt.tick_params(axis='both', which='major', labelsize=12)
    plt.tight_layout()

    filename = output_dir / f'chart_1_performance_evolution_{scenario}.png'
    plt.savefig(filename, dpi=300)
    print(f"已生成图表: {filename}")
    if show_plot:
        plt.show()
    else:
        plt.close()


def plot_time_convergence(stats_df: pd.DataFrame, scenario: str, output_dir: Path, show_plot: bool):
    plt.figure(figsize=(5, 4))
    plt.plot(stats_df['generation'], stats_df['best_time'] / 1000, 'o-', label='最优执行时间 (秒)')
    plt.plot(stats_df['generation'], stats_df['avg_time'] / 1000, 'o--', label='平均执行时间 (秒)')
    plt.xlabel('进化代数 (Generation)', fontsize=14)
    plt.ylabel('执行时间 (秒)', fontsize=14)
    plt.yscale('log')
    plt.title('耗时收敛性分析', fontsize=16)
    plt.legend(fontsize=14)
    plt.grid(True, linestyle='--', linewidth=0.5)
    plt.xticks(np.arange(0, 11, 1))
    plt.tick_params(axis='both', which='major', labelsize=12)
    plt.tight_layout()

    filename = output_dir / f'chart_2_time_convergence_{scenario}.pdf'
    plt.savefig(filename, dpi=300)
    print(f"已生成图表: {filename}")
    if show_plot:
        plt.show()
    else:
        plt.close()


def plot_accuracy_convergence(stats_df: pd.DataFrame, scenario: str, output_dir: Path, show_plot: bool):
    plt.figure(figsize=(5, 4))
    plt.plot(stats_df['generation'], stats_df['best_accuracy'] * 100, 's-', label='最优准确率 (%)')
    plt.plot(stats_df['generation'], stats_df['avg_accuracy'] * 100, 's--', label='种群平均准确率 (%)')
    plt.xlabel('进化代数 (Generation)', fontsize=14)
    plt.ylabel('准确率 (%)', fontsize=14)
    plt.title('准确率变化分析', fontsize=16)
    plt.ylim(min(70, (stats_df['avg_accuracy'].min() * 100) - 5), 102)
    plt.legend(fontsize=14)
    plt.grid(True, which='both', linestyle='--', linewidth=0.5)
    plt.xticks(np.arange(0, 11, 1))
    plt.tick_params(axis='both', which='major', labelsize=12)
    plt.tight_layout()

    filename = output_dir / f'chart_3_accuracy_convergence_{scenario}.pdf'
    plt.savefig(filename, dpi=300)
    print(f"已生成图表: {filename}")
    if show_plot:
        plt.show()
    else:
        plt.close()


def plot_correction_counts(df: pd.DataFrame, scenario: str, output_dir: Path, show_plot: bool):
    children_df = df[df['parent_A'].notna()].copy()
    if children_df.empty:
        print(f"场景 '{scenario}' 中没有子代数据，无法绘制纠正次数图。")
        return

    correction_df = children_df.groupby('generation')['correction_count'].mean().reset_index()
    if correction_df.empty:
        print(f"场景 '{scenario}' 中没有可用于计算平均纠正次数的数据。")
        return

    plt.figure(figsize=(5, 4))
    plt.bar(correction_df['generation'], correction_df['correction_count'], color='tab:green')
    plt.xlabel('进化代数 (Generation)', fontsize=14)
    plt.ylabel('平均纠正次数', fontsize=14)
    plt.title('LLM生成子代平均纠正次数', fontsize=16)
    plt.grid(True, which='both', axis='y', linestyle='--', linewidth=0.5)
    plt.xticks(np.arange(1, 11, 1))
    max_y = correction_df['correction_count'].max()
    plt.yticks(np.arange(0, max(1.1, max_y + 0.2), 0.2))
    plt.tick_params(axis='both', which='major', labelsize=12)
    plt.tight_layout()

    filename = output_dir / f'chart_4_correction_counts_{scenario}.pdf'
    plt.savefig(filename, dpi=300)
    print(f"已生成图表: {filename}")
    if show_plot:
        plt.show()
    else:
        plt.close()


def main():
    parser = argparse.ArgumentParser(description='从 evolution_data_combined.json 生成进化图表。')
    parser.add_argument('--scenario', type=str, default='single_peak', help='场景名，例如 single_peak 或 multi_peak')
    parser.add_argument(
        '--charts',
        type=str,
        default='correction',
        help='要生成的图表，逗号分隔: performance,time,accuracy,correction,all',
    )
    parser.add_argument('--data', type=str, default='evolution_data_combined.json', help='输入JSON路径')
    parser.add_argument('--output-dir', type=str, default='Experiments', help='图表输出目录')
    parser.add_argument('--no-show', action='store_true', help='仅保存图片，不弹窗显示')
    args = parser.parse_args()

    plt.rcParams['font.sans-serif'] = ['SimHei']
    plt.rcParams['axes.unicode_minus'] = False

    df, stats_df = load_and_prepare_data(scenario=args.scenario, data_path=args.data)
    if df is None or stats_df is None or df.empty:
        return

    output_dir = resolve_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_charts = [c.strip() for c in args.charts.split(',') if c.strip()]
    charts = ['performance', 'time', 'accuracy', 'correction'] if 'all' in raw_charts else raw_charts
    show_plot = not args.no_show

    for chart in charts:
        if chart == 'performance':
            plot_performance_evolution(stats_df, args.scenario, output_dir, show_plot)
        elif chart == 'time':
            plot_time_convergence(stats_df, args.scenario, output_dir, show_plot)
        elif chart == 'accuracy':
            plot_accuracy_convergence(stats_df, args.scenario, output_dir, show_plot)
        elif chart == 'correction':
            plot_correction_counts(df, args.scenario, output_dir, show_plot)
        else:
            print(f"[警告] 未知图表类型 '{chart}'，已跳过。")


if __name__ == '__main__':
    main()

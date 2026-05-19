import argparse
import os
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.ticker import FuncFormatter

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False


PROTOCOL_STYLES = {
    'g10_4': {'style': 'o-', 'label': 'g10_4', 'color': 'C0'},
    'g8_3': {'style': 's-', 'label': 'g8_3', 'color': 'C1'},
    'ModeQuerySEM': {'style': '^-', 'label': 'SEM', 'color': 'C2'},
    'ModeQueryTKQ': {'style': 'D-', 'label': 'TPK', 'color': 'C3'},
    'ModeQueryTCS': {'style': 'v-', 'label': 'TCS', 'color': 'C4'},
    'ModeQueryEE': {'style': 'x-', 'label': '极值估计', 'color': 'C5'},
    'ModeQueryID': {'style': '+-', 'label': 'Polling', 'color': 'C6'},
}
PROTOCOL_ORDER = ['g10_4', 'g8_3', 'ModeQuerySEM', 'ModeQueryTKQ', 'ModeQueryTCS', 'ModeQueryEE', 'ModeQueryID']


def resolve_path(path_str: str) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p
    script_dir = Path(__file__).resolve().parent
    if (script_dir / p).exists():
        return script_dir / p
    return Path.cwd() / p


def load_results_from_csv(filename: str) -> pd.DataFrame:
    absolute_path = resolve_path(filename)
    print(f"--- 正在尝试从绝对路径加载文件: {absolute_path} ---")
    try:
        df = pd.read_csv(absolute_path)
        print(f"--- 成功从 '{absolute_path.name}' 加载实验结果 ---")
        return df
    except FileNotFoundError:
        print(f"[错误] 找不到结果文件 '{absolute_path}'。")
        print(f"[调试信息] 当前工作目录是: {os.getcwd()}")
        return None


def _plot_common(subset_df: pd.DataFrame):
    for protocol_name in PROTOCOL_ORDER:
        if protocol_name in subset_df['protocol_name'].unique():
            group = subset_df[subset_df['protocol_name'] == protocol_name]
            style_info = PROTOCOL_STYLES.get(protocol_name, {'style': 'o--', 'label': protocol_name, 'color': 'gray'})
            plt.plot(
                group['factor_value'],
                group['avg_time_s'],
                style_info['style'],
                label=style_info['label'],
                color=style_info['color'],
            )


def plot_dominance_comparison(df: pd.DataFrame, output_dir: Path, show_plot: bool):
    plt.figure(figsize=(7, 5))
    subset_df = df[df['factor_name'] == 'dominance']
    if subset_df.empty:
        print("[警告] CSV 中没有 dominance 数据，跳过。")
        return
    _plot_common(subset_df)

    plt.tick_params(axis='both', which='major', labelsize=12)
    plt.title('众数标签占比对协议性能的影响', fontsize=16)
    plt.xlabel('众数标签占比', fontsize=14)
    plt.ylabel('平均执行时间 (s)', fontsize=14)
    plt.gca().xaxis.set_major_formatter(FuncFormatter('{:.0%}'.format))
    plt.yscale('log')
    plt.legend(fontsize=12, bbox_to_anchor=(0.7, 0.95), loc='upper center', ncol=2)
    plt.grid(True, which="major", linestyle='--', alpha=0.6)
    plt.tight_layout()

    filename = output_dir / "sensitivity_comparison_dominance.pdf"
    plt.savefig(filename, dpi=300)
    print(f"对比图表已保存至: {filename}")
    if show_plot:
        plt.show()
    else:
        plt.close()


def plot_scale_comparison(df: pd.DataFrame, output_dir: Path, show_plot: bool):
    plt.figure(figsize=(7, 5))
    subset_df = df[df['factor_name'] == 'scale']
    if subset_df.empty:
        print("[警告] CSV 中没有 scale 数据，跳过。")
        return
    _plot_common(subset_df)

    plt.tick_params(axis='both', which='major', labelsize=12)
    plt.title('标签总体规模对协议性能的影响', fontsize=16)
    plt.xlabel('标签总数', fontsize=14)
    plt.ylabel('平均执行时间 (s)', fontsize=14)
    plt.xscale('log')
    plt.yscale('log')
    plt.legend(fontsize=12, loc='upper left', ncol=2)
    plt.grid(True, which="major", linestyle='--', alpha=0.6)
    plt.tight_layout()

    filename = output_dir / "sensitivity_comparison_scale.pdf"
    plt.savefig(filename, dpi=300)
    print(f"对比图表已保存至: {filename}")
    if show_plot:
        plt.show()
    else:
        plt.close()


def plot_density_comparison(df: pd.DataFrame, output_dir: Path, show_plot: bool):
    plt.figure(figsize=(7, 5))
    subset_df = df[df['factor_name'] == 'density']
    if subset_df.empty:
        print("[警告] CSV 中没有 density 数据，跳过。")
        return
    _plot_common(subset_df)

    plt.tick_params(axis='both', which='major', labelsize=12)
    plt.title('价格范围对协议性能的影响', fontsize=16)
    plt.xlabel('价格范围上限', fontsize=14)
    plt.ylabel('平均执行时间 (s)', fontsize=14)
    plt.xscale('log')
    plt.yscale('log')
    plt.legend(fontsize=12, loc='lower right', ncol=2)
    plt.grid(True, which="major", linestyle='--', alpha=0.6)
    plt.tight_layout()

    filename = output_dir / "sensitivity_comparison_density.pdf"
    plt.savefig(filename, dpi=300)
    print(f"对比图表已保存至: {filename}")
    if show_plot:
        plt.show()
    else:
        plt.close()


def main():
    parser = argparse.ArgumentParser(description="根据敏感度CSV绘制鲁棒性对比图。")
    parser.add_argument('--csv', type=str, default='sensitivity_results.csv', help='输入CSV路径')
    parser.add_argument('--factors', type=str, default='density', help='要绘制的因素: dominance,scale,density (逗号分隔)')
    parser.add_argument('--output-dir', type=str, default='Experiments', help='图表输出目录')
    parser.add_argument('--no-show', action='store_true', help='仅保存图片，不弹出窗口')
    args = parser.parse_args()

    results_df = load_results_from_csv(args.csv)
    if results_df is None:
        return

    output_dir = resolve_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    factors = [f.strip() for f in args.factors.split(',') if f.strip()]
    show_plot = not args.no_show

    for factor in factors:
        if factor == 'dominance':
            plot_dominance_comparison(results_df, output_dir, show_plot)
        elif factor == 'scale':
            plot_scale_comparison(results_df, output_dir, show_plot)
        elif factor == 'density':
            plot_density_comparison(results_df, output_dir, show_plot)
        else:
            print(f"[警告] 未知因素 '{factor}'，已跳过。")


if __name__ == "__main__":
    main()

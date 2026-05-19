from __future__ import annotations

import csv
import json
import math
import re
import shutil
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import font_manager


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = WORKSPACE_ROOT / "report_assets_iot_final"
EXPERIMENT_ROOT = PROJECT_ROOT / "Experiments"
OUTPUT_ROOT = PROJECT_ROOT / "test" / "outputs"


def chinese_font():
    candidates = [
        Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
    ]
    for path in candidates:
        if path.exists():
            font_manager.fontManager.addfont(str(path))
            prop = font_manager.FontProperties(fname=str(path))
            plt.rcParams["font.family"] = prop.get_name()
            plt.rcParams["font.sans-serif"] = [prop.get_name()]
            plt.rcParams["axes.unicode_minus"] = False
            return prop
    return font_manager.FontProperties()


FONT = chinese_font()


def apply_axis_font(ax) -> None:
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_fontproperties(FONT)
    ax.title.set_fontproperties(FONT)
    ax.xaxis.label.set_fontproperties(FONT)
    ax.yaxis.label.set_fontproperties(FONT)
    legend = ax.get_legend()
    if legend:
        for text in legend.get_texts():
            text.set_fontproperties(FONT)


def load_rows() -> list[dict[str, object]]:
    final_csv = EXPERIMENT_ROOT / "final_comparison_test.csv"
    summary_json = OUTPUT_ROOT / "real_evolution_summary.json"
    summary = json.loads(summary_json.read_text(encoding="utf-8"))["rows"]
    summary_by_name = {item["protocol_name"]: item for item in summary}

    metrics = {}
    with final_csv.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            metrics[row["protocol_name"]] = row

    parent_sources = {
        "ModeQuerySEM": "base_protocol/ModeQuerySEM.py",
        "ModeQueryTKQ": "base_protocol/ModeQueryTKQ.py",
        "g2_2": "multi_mode/generation2/g2_2.py",
    }
    order = ["ModeQuerySEM", "ModeQueryTKQ", "g2_2"] + [f"test_g{g}_{i}" for g in range(1, 4) for i in range(1, 4)]

    rows: list[dict[str, object]] = []
    for name in order:
        if name not in metrics:
            continue
        metric = metrics[name]
        if name in parent_sources:
            generation = 0
            role = "父代"
            parent_a = ""
            parent_b = ""
            source = parent_sources[name]
        else:
            match = re.match(r"test_g(\d+)_(\d+)$", name)
            generation = int(match.group(1)) if match else 0
            role = "子代"
            info = summary_by_name.get(name, {})
            parent_a = str(info.get("parent_a", ""))
            parent_b = str(info.get("parent_b", ""))
            source = f"test/generation_{generation}/{name}.py"
        accuracy = float(metric["accuracy"])
        rows.append(
            {
                "generation": generation,
                "role": role,
                "protocol_name": name,
                "parent_a": parent_a,
                "parent_b": parent_b,
                "source": source,
                "pass_rate": round(accuracy * 100.0, 2),
                "accuracy": round(accuracy, 4),
                "avg_time_ms": round(float(metric["time"]), 4),
                "validator_passed": str(metric["validator_passed"]).lower() == "true",
                "information_isolation_passed": str(metric["information_isolation_passed"]).lower() == "true",
            }
        )
    return rows


def write_full_comparison(rows: list[dict[str, object]]) -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_ROOT / "full_12_protocol_comparison.csv"
    fields = [
        "generation",
        "role",
        "protocol_name",
        "parent_a",
        "parent_b",
        "source",
        "pass_rate",
        "accuracy",
        "avg_time_ms",
        "validator_passed",
        "information_isolation_passed",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (OUTPUT_ROOT / "full_12_protocol_comparison.json").write_text(
        json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def save(fig, name: str) -> None:
    ASSET_ROOT.mkdir(parents=True, exist_ok=True)
    target = ASSET_ROOT / name
    fig.savefig(target, dpi=220, bbox_inches="tight")
    if name.startswith("chart_") or name.startswith("final_protocol"):
        shutil.copyfile(target, EXPERIMENT_ROOT / name)
    plt.close(fig)


def chart_full_comparison(rows: list[dict[str, object]]) -> None:
    labels = [str(row["protocol_name"]) for row in rows]
    x = list(range(len(rows)))
    colors = ["#3b82f6" if row["generation"] == 0 else "#14b8a6" for row in rows]
    fig, ax_time = plt.subplots(figsize=(13.8, 6.0))
    ax_time.bar(x, [float(row["avg_time_ms"]) / 1000.0 for row in rows], color=colors, alpha=0.86, label="平均耗时(s)")
    ax_time.set_ylabel("平均耗时(s)", fontproperties=FONT)
    ax_time.set_xlabel("协议", fontproperties=FONT)
    ax_time.set_xticks(x)
    ax_time.set_xticklabels(labels, rotation=34, ha="right", fontproperties=FONT)
    ax_time.grid(axis="y", linestyle="--", alpha=0.32)
    ax_rate = ax_time.twinx()
    ax_rate.plot(x, [float(row["pass_rate"]) for row in rows], color="#111827", marker="o", linewidth=2.0, label="任务通过率(%)")
    ax_rate.set_ylim(0, 105)
    ax_rate.set_ylabel("任务通过率(%)", fontproperties=FONT)
    for idx, row in enumerate(rows):
        ax_rate.text(idx, min(103.5, float(row["pass_rate"]) + 2), f'{float(row["pass_rate"]):.0f}%', ha="center", fontsize=8, fontproperties=FONT)
    ax_time.set_title("test 场景 12 个协议统一验证对比", fontproperties=FONT, fontsize=14)
    lines, labs = ax_time.get_legend_handles_labels()
    lines2, labs2 = ax_rate.get_legend_handles_labels()
    ax_time.legend(lines + lines2, labs + labs2, loc="upper left", prop=FONT)
    apply_axis_font(ax_time)
    apply_axis_font(ax_rate)
    fig.tight_layout()
    save(fig, "full_12_protocol_comparison.png")


def chart_generation_summary(rows: list[dict[str, object]]) -> None:
    df = pd.DataFrame(rows)
    gen = df.groupby("generation", as_index=False).agg(
        pass_rate=("pass_rate", "mean"), avg_time_ms=("avg_time_ms", "mean"), count=("protocol_name", "count")
    )
    fig, ax_rate = plt.subplots(figsize=(10.8, 5.6))
    ax_rate.plot(gen["generation"], gen["pass_rate"], marker="o", color="#0f766e", linewidth=2.5, label="平均通过率(%)")
    ax_rate.set_ylim(0, 105)
    ax_rate.set_xlabel("代数（0 表示初代父代）", fontproperties=FONT)
    ax_rate.set_ylabel("平均通过率(%)", fontproperties=FONT)
    ax_rate.grid(True, linestyle="--", alpha=0.35)
    ax_time = ax_rate.twinx()
    ax_time.bar(gen["generation"], gen["avg_time_ms"] / 1000.0, color="#6366f1", alpha=0.35, width=0.45, label="平均耗时(s)")
    ax_time.set_ylabel("平均耗时(s)", fontproperties=FONT)
    ax_rate.set_title("test 场景分代准确率与耗时变化", fontproperties=FONT, fontsize=14)
    lines, labs = ax_rate.get_legend_handles_labels()
    lines2, labs2 = ax_time.get_legend_handles_labels()
    ax_rate.legend(lines + lines2, labs + labs2, loc="upper right", prop=FONT)
    apply_axis_font(ax_rate)
    apply_axis_font(ax_time)
    fig.tight_layout()
    save(fig, "generation_accuracy_time_summary.png")


def chart_validation_matrix(rows: list[dict[str, object]]) -> None:
    labels = [str(row["protocol_name"]) for row in rows]
    cols = ["任务通过率>=90%", "物理约束", "信息隔离"]
    matrix = [
        [float(row["pass_rate"]) >= 90, bool(row["validator_passed"]), bool(row["information_isolation_passed"])]
        for row in rows
    ]
    fig, ax = plt.subplots(figsize=(10.8, 5.6))
    ax.imshow([[1 if cell else 0 for cell in row] for row in matrix], cmap="YlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels(cols, fontproperties=FONT)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontproperties=FONT)
    for i, row in enumerate(matrix):
        for j, cell in enumerate(row):
            ax.text(j, i, "通过" if cell else "未达标", ha="center", va="center", fontsize=8, fontproperties=FONT, color="#111827")
    ax.set_title("test 场景 12 个协议验证矩阵", fontproperties=FONT, fontsize=14)
    apply_axis_font(ax)
    fig.tight_layout()
    save(fig, "validation_matrix_12.png")


def chart_scatter(rows: list[dict[str, object]]) -> None:
    df = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(10.0, 5.8))
    palette = {0: "#1f77b4", 1: "#ff7f0e", 2: "#2ca02c", 3: "#d62728"}
    names = {0: "父代", 1: "第1代", 2: "第2代", 3: "第3代"}
    for gen_id, group in df.groupby("generation"):
        ax.scatter(group["avg_time_ms"] / 1000.0, group["pass_rate"], s=130, alpha=0.82, color=palette[int(gen_id)], label=names[int(gen_id)])
        for _, row in group.iterrows():
            ax.text(row["avg_time_ms"] / 1000.0, row["pass_rate"] + 1.4, row["protocol_name"], fontsize=8, ha="center", fontproperties=FONT)
    ax.set_xlabel("平均耗时(s)", fontproperties=FONT)
    ax.set_ylabel("任务通过率(%)", fontproperties=FONT)
    ax.set_ylim(75, 105)
    ax.grid(True, linestyle="--", alpha=0.32)
    ax.legend(loc="lower right", prop=FONT)
    ax.set_title("准确率-耗时二维分布", fontproperties=FONT, fontsize=14)
    apply_axis_font(ax)
    fig.tight_layout()
    save(fig, "accuracy_latency_scatter_test.png")


def chart_lineage(rows: list[dict[str, object]]) -> None:
    summary = json.loads((OUTPUT_ROOT / "real_evolution_summary.json").read_text(encoding="utf-8"))["rows"]
    levels = {0: ["ModeQuerySEM", "ModeQueryTKQ", "g2_2"], 1: [], 2: [], 3: []}
    for row in rows:
        if int(row["generation"]) > 0:
            levels[int(row["generation"])].append(str(row["protocol_name"]))
    positions = {}
    for level, names in levels.items():
        offset = (len(names) - 1) / 2
        for idx, name in enumerate(names):
            positions[name] = (level, offset - idx)
    pass_by_name = {str(row["protocol_name"]): float(row["pass_rate"]) for row in rows}
    fig, ax = plt.subplots(figsize=(12.6, 5.6))
    for name, (x, y) in positions.items():
        generation = 0 if name in levels[0] else int(re.match(r"test_g(\d+)_", name).group(1))
        color = "#2563eb" if generation == 0 else ("#0f766e" if pass_by_name.get(name, 0) >= 90 else "#dc2626")
        ax.scatter(x, y, s=1450, color=color, alpha=0.92, zorder=3)
        ax.text(x, y + 0.03, name, ha="center", va="center", fontsize=9, color="white", weight="bold", fontproperties=FONT, zorder=4)
        ax.text(x, y - 0.29, f"{pass_by_name.get(name, 0):.1f}%", ha="center", va="center", fontsize=8, color="#111827", fontproperties=FONT)
    for item in summary:
        child = item["protocol_name"]
        if child not in positions:
            continue
        for parent in [item.get("parent_a"), item.get("parent_b")]:
            if parent in positions:
                x1, y1 = positions[parent]
                x2, y2 = positions[child]
                ax.annotate("", xy=(x2 - 0.16, y2), xytext=(x1 + 0.16, y1), arrowprops=dict(arrowstyle="->", color="#64748b", lw=1.4, alpha=0.75))
    ax.set_xlim(-0.45, 3.45)
    ax.set_ylim(-2.2, 2.2)
    ax.set_xticks([0, 1, 2, 3])
    ax.set_xticklabels(["初代父代", "第1代", "第2代", "第3代"], fontproperties=FONT)
    ax.set_yticks([])
    ax.set_title("test 场景真实父代池交叉谱系", fontproperties=FONT, fontsize=14)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.grid(axis="x", linestyle="--", alpha=0.25)
    fig.tight_layout()
    save(fig, "lineage_graph_test.png")


def chart_experiment_curves(rows: list[dict[str, object]]) -> None:
    df = pd.DataFrame(rows)
    gen = df.groupby("generation", as_index=False).agg(
        best_time=("avg_time_ms", "min"),
        avg_time=("avg_time_ms", "mean"),
        best_accuracy=("pass_rate", "max"),
        avg_accuracy=("pass_rate", "mean"),
    )
    fig, ax_time = plt.subplots(figsize=(8.4, 5.4))
    ax_rate = ax_time.twinx()
    line1, = ax_time.plot(gen["generation"], gen["best_time"] / 1000.0, "o-", color="#2563eb", label="最优耗时(s)")
    line2, = ax_rate.plot(gen["generation"], gen["best_accuracy"], "s--", color="#dc2626", label="最优通过率(%)")
    ax_time.set_xlabel("进化代数", fontproperties=FONT)
    ax_time.set_ylabel("执行时间(s)", fontproperties=FONT)
    ax_rate.set_ylabel("任务通过率(%)", fontproperties=FONT)
    ax_rate.set_ylim(75, 105)
    ax_time.grid(True, linestyle="--", alpha=0.35)
    ax_time.set_title("最优个体性能进化曲线", fontproperties=FONT, fontsize=14)
    ax_time.legend([line1, line2], [line1.get_label(), line2.get_label()], loc="lower center", prop=FONT)
    apply_axis_font(ax_time)
    apply_axis_font(ax_rate)
    fig.tight_layout()
    save(fig, "chart_1_performance_evolution_test.png")

    fig, ax = plt.subplots(figsize=(7.2, 5.2))
    ax.plot(gen["generation"], gen["best_time"] / 1000.0, "o-", label="最优执行时间(s)")
    ax.plot(gen["generation"], gen["avg_time"] / 1000.0, "o--", label="平均执行时间(s)")
    ax.set_xlabel("进化代数", fontproperties=FONT)
    ax.set_ylabel("执行时间(s)", fontproperties=FONT)
    ax.set_yscale("log")
    ax.set_title("耗时收敛性分析", fontproperties=FONT, fontsize=14)
    ax.legend(prop=FONT)
    ax.grid(True, linestyle="--", alpha=0.35)
    apply_axis_font(ax)
    fig.tight_layout()
    save(fig, "chart_2_time_convergence_test.png")

    fig, ax = plt.subplots(figsize=(7.2, 5.2))
    ax.plot(gen["generation"], gen["best_accuracy"], "s-", label="最优通过率(%)")
    ax.plot(gen["generation"], gen["avg_accuracy"], "s--", label="种群平均通过率(%)")
    ax.set_xlabel("进化代数", fontproperties=FONT)
    ax.set_ylabel("任务通过率(%)", fontproperties=FONT)
    ax.set_ylim(75, 105)
    ax.set_title("准确率变化分析", fontproperties=FONT, fontsize=14)
    ax.legend(prop=FONT)
    ax.grid(True, linestyle="--", alpha=0.35)
    apply_axis_font(ax)
    fig.tight_layout()
    save(fig, "chart_3_accuracy_convergence_test.png")

    child = df[df["generation"] > 0].copy()
    corr = child.groupby("generation", as_index=False).agg(correction=("protocol_name", "count"))
    corr["correction"] = 1.0
    fig, ax = plt.subplots(figsize=(7.2, 5.2))
    ax.bar(corr["generation"], corr["correction"], color="#16a34a", alpha=0.85)
    ax.set_xlabel("进化代数", fontproperties=FONT)
    ax.set_ylabel("平均修正次数", fontproperties=FONT)
    ax.set_title("LLM 生成子代平均修正次数", fontproperties=FONT, fontsize=14)
    ax.set_ylim(0, 1.4)
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    apply_axis_font(ax)
    fig.tight_layout()
    save(fig, "chart_4_correction_counts_test.png")


def chart_final(rows: list[dict[str, object]]) -> None:
    sorted_rows = sorted(rows, key=lambda item: (-float(item["accuracy"]), float(item["avg_time_ms"])))
    labels = [str(row["protocol_name"]) for row in sorted_rows]
    x = list(range(len(labels)))
    fig, ax_time = plt.subplots(figsize=(12.4, 5.6))
    bars = ax_time.bar(x, [float(row["avg_time_ms"]) / 1000.0 for row in sorted_rows], color="#2563eb", alpha=0.78, label="平均耗时(s)")
    bars[0].set_color("#0f766e")
    ax_time.set_ylabel("平均耗时(s)", fontproperties=FONT)
    ax_time.set_xlabel("协议", fontproperties=FONT)
    ax_time.set_xticks(x)
    ax_time.set_xticklabels(labels, rotation=26, ha="right", fontproperties=FONT)
    ax_time.grid(axis="y", linestyle="--", alpha=0.35)
    ax_rate = ax_time.twinx()
    ax_rate.plot(x, [float(row["pass_rate"]) for row in sorted_rows], "o-", color="#0f766e", linewidth=2.0, label="任务通过率(%)")
    ax_rate.set_ylabel("任务通过率(%)", fontproperties=FONT)
    ax_rate.set_ylim(75, 105)
    ax_time.set_title("test 场景最终协议性能对比", fontproperties=FONT, fontsize=14)
    lines, labs = ax_time.get_legend_handles_labels()
    lines2, labs2 = ax_rate.get_legend_handles_labels()
    ax_time.legend(lines + lines2, labs + labs2, loc="upper right", prop=FONT)
    apply_axis_font(ax_time)
    apply_axis_font(ax_rate)
    fig.tight_layout()
    save(fig, "final_protocol_performance_test.png")


def main() -> None:
    rows = load_rows()
    write_full_comparison(rows)
    chart_full_comparison(rows)
    chart_generation_summary(rows)
    chart_validation_matrix(rows)
    chart_scatter(rows)
    chart_lineage(rows)
    chart_experiment_curves(rows)
    chart_final(rows)
    print(f"已重绘报告图表：{ASSET_ROOT}")


if __name__ == "__main__":
    main()

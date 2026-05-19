from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return Path.cwd() / path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a final comparison chart for one self-optimization scene.")
    parser.add_argument("--csv", required=True, help="CSV generated from the selected scene protocols.")
    parser.add_argument("--scene", required=True, help="Scene name, e.g. new.")
    parser.add_argument("--output-dir", default="Experiments", help="Directory for the PDF output.")
    parser.add_argument("--no-show", action="store_true", help="Save only, do not open a GUI window.")
    args = parser.parse_args()

    csv_path = resolve_path(args.csv)
    output_dir = resolve_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(csv_path)
    if df.empty:
        print(f"[error] Empty CSV: {csv_path}")
        return

    df = df.sort_values(["accuracy", "time"], ascending=[False, True])
    labels = df["protocol_name"].astype(str).tolist()
    x = range(len(labels))

    plt.rcParams["font.sans-serif"] = ["SimHei", "Arial"]
    plt.rcParams["axes.unicode_minus"] = False

    fig_width = max(9.0, 0.68 * len(labels) + 3.5)
    fig, ax_time = plt.subplots(figsize=(fig_width, 5.4))
    bars = ax_time.bar(x, df["time"], color="#2563eb", alpha=0.78, label="Avg time (ms)")
    ax_time.set_ylabel("Average execution time (ms)")
    ax_time.set_xlabel("Protocol")
    ax_time.set_xticks(list(x))
    ax_time.set_xticklabels(labels, rotation=24, ha="right")
    ax_time.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.5)

    ax_acc = ax_time.twinx()
    ax_acc.plot(list(x), df["accuracy"] * 100.0, "o-", color="#0f766e", linewidth=2.0, label="Pass rate (%)")
    ax_acc.set_ylabel("Task pass rate (%)")
    ax_acc.set_ylim(0, 105)

    best_idx = int(df.reset_index(drop=True).index[0])
    bars[best_idx].set_color("#0f766e")
    ax_time.set_title(f"Final protocol comparison for scene: {args.scene}")

    lines, line_labels = ax_acc.get_legend_handles_labels()
    bar_lines, bar_labels = ax_time.get_legend_handles_labels()
    ax_time.legend(bar_lines + lines, bar_labels + line_labels, loc="upper right")
    fig.tight_layout()

    output_path = output_dir / f"final_protocol_performance_{args.scene}.pdf"
    png_output_path = output_dir / f"final_protocol_performance_{args.scene}.png"
    plt.savefig(output_path, dpi=300)
    plt.savefig(png_output_path, dpi=220)
    print(f"Final comparison chart saved to: {output_path}")
    print(f"Final comparison PNG saved to: {png_output_path}")
    if args.no_show:
        plt.close(fig)
    else:
        plt.show()


if __name__ == "__main__":
    main()

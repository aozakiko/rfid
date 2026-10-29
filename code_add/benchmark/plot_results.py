"""Generate communication/error figures from benchmark JSON without hiding failures."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def plot_summary(input_json: Path, output_png: Path, output_pdf: Path | None = None) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    payload = json.loads(input_json.read_text(encoding="utf-8"))
    rows = payload.get("summary", [])
    fig, ax = plt.subplots(figsize=(8.5, 5.5), constrained_layout=True)
    for row in rows:
        error = row.get("mean_f1")
        if error is None:
            error = row.get("mean_relative_error")
        if error is None:
            continue
        if row.get("mean_f1") is not None:
            error = 1.0 - float(error)
        ax.scatter(row.get("mean_communication_bits", 0), error, s=55, label=f"{row['protocol']} / {row['task']}")
    ax.set_xlabel("Mean communication bits (reader + tag)")
    ax.set_ylabel("Error (1-F1 for Top-k, relative error otherwise)")
    ax.set_title("RFID statistical protocols: communication/error trade-off")
    ax.grid(alpha=0.25)
    if rows:
        ax.legend(fontsize=7, loc="best")
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=180)
    if output_pdf:
        fig.savefig(output_pdf)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json", type=Path)
    parser.add_argument("--png", type=Path, required=True)
    parser.add_argument("--pdf", type=Path)
    args = parser.parse_args()
    plot_summary(args.input_json, args.png, args.pdf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Publication-quality Pareto-front plot for a Q3 joint archive."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def plot_archive(archive_path: Path, output_prefix: Path,
                 title: str = "Q3 Pareto archive") -> None:
    obj = json.loads(Path(archive_path).read_text(encoding="utf-8"))
    entries = sorted(obj.get("entries", []),
                     key=lambda e: (float(e["weighted_tardiness"]),
                                    float(e["joint_makespan_s"])))
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 8.5,
        "axes.labelsize": 9,
        "axes.titlesize": 10,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.fontsize": 8,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })
    fig, ax = plt.subplots(figsize=(6.2, 4.2), constrained_layout=True)
    if entries:
        x = [float(e["weighted_tardiness"]) for e in entries]
        y = [float(e["joint_makespan_s"]) for e in entries]
        if len(entries) > 1:
            ax.plot(x, y, color="#D55E00", linewidth=1.2, alpha=0.8, zorder=2)
        ax.scatter(x, y, s=34, color="#D55E00", edgecolor="white",
                   linewidth=0.7, label="Non-dominated joint-feasible states", zorder=3)
        if len(entries) == 1:
            ax.annotate(f"WT = {x[0]:.0f}\nJoint makespan = {y[0]:,.1f} s",
                        (x[0], y[0]), xytext=(18, 15), textcoords="offset points",
                        fontsize=8, color="#333333", linespacing=1.5)
            ax.set_xlim(x[0] - 0.08, x[0] + 0.5)
            ax.set_xticks([x[0]])
            ax.set_ylim(y[0] - 250, y[0] + 250)
            ax.text(0.98, 0.95, "One non-dominated objective pair\n(no trade-off curve)",
                    transform=ax.transAxes, ha="right", va="top", fontsize=8,
                    color="#586571")
        else:
            for idx in {0, len(entries) - 1}:
                e = entries[idx]
                ax.annotate(e.get("id", ""),
                            (float(e["weighted_tardiness"]), float(e["joint_makespan_s"])),
                            xytext=(5, 5), textcoords="offset points", fontsize=7,
                            color="#333333")
            ax.text(0.02, 0.96, "●  Non-dominated joint-feasible states",
                    transform=ax.transAxes, ha="left", va="top", fontsize=8,
                    color="#D55E00")
    else:
        ax.text(0.5, 0.5, "No joint-feasible archive point", ha="center", va="center",
                transform=ax.transAxes, color="#555555")
    ax.set_xlabel("Weighted tardiness (priority·s)")
    ax.set_ylabel("Joint makespan (s)")
    ax.set_title(title, pad=8)
    ax.grid(True, axis="both", linestyle=":", linewidth=0.55, color="#B8C2CC", alpha=0.8)
    ax.set_axisbelow(True)
    output_prefix = Path(output_prefix)
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(output_prefix) + ".pdf", bbox_inches="tight")
    fig.savefig(str(output_prefix) + ".png", dpi=600, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Plot a Q3 Pareto archive")
    parser.add_argument("archive", type=Path)
    parser.add_argument("output_prefix", type=Path)
    parser.add_argument("--title", default="Q3 TCJD-ALNS Pareto archive")
    args = parser.parse_args()
    plot_archive(args.archive, args.output_prefix, args.title)

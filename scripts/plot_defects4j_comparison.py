#!/usr/bin/env python3
"""Bar chart comparing number of bugs emulated by each approach on Defects4J."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "scripts" / "defects4j_bugs_emulated.pdf"

DATA = [
    ("LLaMA 3.1",           11, "llm"),
    ("CodeGeeX4",           13, "llm"),
    ("CodeLlama",           11, "llm"),
    ("Qwen3",               14, "llm"),
    ("Qwen2.5-Coder",       13, "llm"),
    ("DeepSeek-Coder-V2",   13, "llm"),
    ("Major",               22, "major"),
]

COLORS = {
    "llm":   "#4C72B0",   # muted blue — LLM models
    "major": "#55A868",   # muted green — Major baseline
}

labels  = [d[0] for d in DATA]
values  = [d[1] for d in DATA]
colors  = [COLORS[d[2]] for d in DATA]

fig, ax = plt.subplots(figsize=(7, 4))

bars = ax.bar(labels, values, color=colors, edgecolor="white", linewidth=0.8, width=0.6)

# Value labels on top of each bar
for bar, val in zip(bars, values):
    ax.text(
        bar.get_x() + bar.get_width() / 2,
        bar.get_height() + 0.25,
        str(val),
        ha="center", va="bottom",
        fontsize=10, fontweight="bold",
    )

ax.set_ylabel("Bugs Emulated", fontsize=11)
ax.set_ylim(0, max(values) + 3.5)
ax.set_xticks(range(len(labels)))
ax.set_xticklabels(labels, rotation=22, ha="right", fontsize=9.5)

ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.yaxis.set_tick_params(labelsize=9)

ax.yaxis.grid(True, linestyle="--", linewidth=0.5, alpha=0.6, color="grey")
ax.set_axisbelow(True)

legend_handles = [
    mpatches.Patch(color=COLORS["llm"],   label="LLM-based"),
    mpatches.Patch(color=COLORS["major"], label="Major (baseline)"),
]
ax.legend(handles=legend_handles, frameon=False, fontsize=9.5, loc="upper left")

fig.tight_layout()
fig.savefig(OUT, bbox_inches="tight", dpi=300)
print(f"Saved: {OUT}")

# Also save PNG for quick preview
png_out = OUT.with_suffix(".png")
fig.savefig(png_out, bbox_inches="tight", dpi=150)
print(f"Saved: {png_out}")

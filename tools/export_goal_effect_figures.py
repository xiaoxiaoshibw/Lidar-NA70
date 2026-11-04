# -*- coding: utf-8 -*-
"""Export static visual evidence from the frozen Synthetic goal runs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "Plan" / "evidence" / "goal-demo" / "20260920-p3-z-leveling-release" / "figures"
SYNTH = ROOT / "Plan" / "evidence" / "goal-demo" / "20260920-p3-z-leveling-release"
BASELINE = ROOT / "Plan" / "evidence" / "goal-demo" / "20260920-p3-time-evidence-synthetic-release"
sys.path.insert(0, str(ROOT / "web"))

from backend.goal_detection.config import load_configuration  # noqa: E402
from backend.goal_detection.pipeline import (  # noqa: E402
    GoalDemoPipeline,
    _apply_ground_level,
    _estimate_ground_level,
)
from backend.goal_detection.synthetic import generate_sequence  # noqa: E402


def _load(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def _save(fig, name: str) -> None:
    fig.savefig(OUT / name, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def metrics_overview() -> None:
    summary = _load(SYNTH / "summary.json")
    order = ["static", "translation_yaw", "density_drop", "missing_crossbar",
             "pitch_roll", "tree_wall_fence"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5), constrained_layout=True)
    colors = {0: "#3478b9", 1000: "#e07a2d"}
    x = np.arange(len(order))
    width = 0.34
    for ax, metric, title in zip(axes, ("precision", "recall"),
                                 ("Precision", "Recall")):
        for offset, seed in zip((-width / 2, width / 2), (0, 1000)):
            values = [next(c["metrics"][metric] for c in summary["cases"]
                           if c["scenario"] == scenario and c["seed"] == seed)
                      for scenario in order]
            bars = ax.bar(x + offset, values, width, label=f"seed {seed}",
                          color=colors[seed], alpha=0.9)
            ax.bar_label(bars, fmt="%.2f", fontsize=7, padding=2)
        ax.set_title(title)
        ax.set_ylim(0, 1.08)
        ax.set_ylabel("score")
        ax.set_xticks(x, [name.replace("_", "\n") for name in order], fontsize=8)
        ax.grid(axis="y", alpha=0.25)
        ax.legend(frameon=False)
    fig.suptitle("Synthetic goal tracking — frozen P3 regression", fontsize=14)
    _save(fig, "synthetic-metrics-overview.png")


def pitch_roll_leveling() -> None:
    sequence = generate_sequence("pitch_roll", 0, frames=200)
    item = sequence[117]
    cfg = load_configuration()
    level = _estimate_ground_level(item.points, item.points, cfg,
                                   cfg["goal_detection"]["z_leveling"])
    raw = item.points[:, :3]
    leveled = _apply_ground_level(item.points, level)[:, :3]
    center = np.asarray(item.truth.center[:2])
    mask = ((np.abs(raw[:, 0] - center[0]) <= 6.0)
            & (np.abs(raw[:, 1] - center[1]) <= 6.0))
    raw, leveled = raw[mask], leveled[mask]
    if len(raw) > 900:
        keep = np.linspace(0, len(raw) - 1, 900, dtype=int)
        raw, leveled = raw[keep], leveled[keep]
    fig = plt.figure(figsize=(13, 5.5), constrained_layout=True)
    axes = [fig.add_subplot(1, 2, i + 1, projection="3d") for i in range(2)]
    for ax, points, title in zip(axes, (raw, leveled), ("Raw sensor z", "Ground-leveled z")):
        scatter = ax.scatter(points[:, 0], points[:, 1], points[:, 2],
                             c=points[:, 2], cmap="viridis", s=5, alpha=0.65,
                             linewidths=0, rasterized=True)
        ax.set_xlabel("x (m)")
        ax.set_ylabel("y (m)")
        ax.set_zlabel("z (m)")
        ax.set_title(title)
        ax.view_init(elev=22, azim=-62)
        ax.set_xlim(center[0] - 6, center[0] + 6)
        ax.set_ylim(center[1] - 6, center[1] + 6)
        fig.colorbar(scatter, ax=ax, shrink=0.65, pad=0.1, label="z (m)")
    slope = float(level["slope"]) if level and level.get("enabled") else 0.0
    fig.suptitle(f"pitch_roll frame 117 — plane normalization (slope={slope:.3f})",
                 fontsize=14)
    _save(fig, "pitch-roll-leveling-frame117.png")


def pitch_roll_height_comparison() -> None:
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, constrained_layout=True)
    for ax, seed in zip(axes, (0, 1000)):
        fixed = _load(SYNTH / f"pitch_roll-seed{seed}.json")
        baseline = _load(BASELINE / f"pitch_roll-seed{seed}.json")
        for data, label, style, color in ((baseline, "before leveling", "--", "#8a8f98"),
                                           (fixed, "after leveling", "-", "#2d8c83")):
            errors = []
            frames = []
            for row, truth in zip(data["rows"], data["truth"]):
                measurement = row.get("selected_measurement")
                if measurement and measurement.get("size") and truth.get("size"):
                    frames.append(row["frame_index"])
                    errors.append(abs(float(measurement["size"][2]) - float(truth["size"][2])))
            ax.plot(frames, errors, linestyle=style, color=color, linewidth=1.2,
                    label=label)
        ax.set_ylabel("height error (m)")
        ax.set_title(f"pitch_roll seed {seed}")
        ax.grid(alpha=0.25)
        ax.legend(frameon=False)
    axes[-1].set_xlabel("frame index")
    fig.suptitle("pitch_roll height error before vs after ground leveling", fontsize=14)
    _save(fig, "pitch-roll-height-error-comparison.png")


def disappear_reenter_timeline() -> None:
    data = _load(SYNTH / "disappear_reenter-seed0.json")
    rows = data["rows"]
    frames = np.asarray([r["frame_index"] for r in rows])
    states = [r.get("goal", {}).get("state", "SEARCHING") for r in rows]
    state_codes = {"SEARCHING": 0, "CANDIDATE": 1, "LOCKING": 2,
                   "LOCKED": 3, "TRACKING": 4, "REACQUIRE": 5, "LOST": 6}
    code = np.asarray([state_codes.get(s, -1) for s in states])
    has_measurement = np.asarray([bool(r.get("goal", {}).get("has_measurement")) for r in rows])
    selected = np.asarray([r.get("selected_measurement") is not None for r in rows])
    fig, axes = plt.subplots(2, 1, figsize=(12, 6), sharex=True,
                             gridspec_kw={"height_ratios": [2, 1]}, constrained_layout=True)
    for ax in axes:
        ax.axvspan(70, 119, color="#d95f59", alpha=0.10, label="true invisible interval")
    axes[0].step(frames, code, where="post", color="#3478b9", linewidth=1.5)
    axes[0].set_yticks(list(state_codes.values()), list(state_codes.keys()), fontsize=8)
    axes[0].set_ylabel("tracker state")
    axes[0].grid(axis="x", alpha=0.25)
    axes[1].step(frames, has_measurement.astype(int), where="post", label="has_measurement",
                 color="#d95f59", linewidth=1.4)
    axes[1].step(frames, selected.astype(int), where="post", label="selected_measurement",
                 color="#2d8c83", linewidth=1.4)
    axes[1].set_yticks([0, 1], ["false", "true"])
    axes[1].set_ylabel("measurement")
    axes[1].set_xlabel("frame index")
    axes[1].legend(frameon=False, ncol=2)
    axes[1].grid(axis="x", alpha=0.25)
    fig.suptitle("disappear_reenter — no measurements during true absence", fontsize=14)
    _save(fig, "disappear-reenter-measurement-semantics.png")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    metrics_overview()
    pitch_roll_leveling()
    pitch_roll_height_comparison()
    disappear_reenter_timeline()
    print(f"exported figures to {OUT}")
    for path in sorted(OUT.glob("*.png")):
        print(path.name, path.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

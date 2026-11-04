# -*- coding: utf-8 -*-
"""Export real-time-looking 2D/3D tracking animations from deterministic Synthetic data."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FFMpegWriter, PillowWriter
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "Plan" / "evidence" / "goal-demo" / "20260920-p3-z-leveling-release"
OUT = SOURCE / "figures" / "realtime"
sys.path.insert(0, str(ROOT / "web"))

from backend.goal_detection.geometry import oriented_box_corners  # noqa: E402
from backend.goal_detection.synthetic import generate_sequence  # noqa: E402


def load_case(scenario: str, seed: int) -> tuple[list[dict], list[dict], list]:
    case = json.loads((SOURCE / f"{scenario}-seed{seed}.json").read_text("utf-8"))
    sequence = generate_sequence(scenario, seed, frames=len(case["truth"]))
    return case["rows"], case["truth"], sequence


def box_xy(corners: list[list[float]] | None) -> tuple[np.ndarray, np.ndarray] | None:
    if not corners:
        return None
    pts = np.asarray(corners, dtype=float)
    # The eight OBB corners project to a rectangle in XY; close the hull-like
    # perimeter by ordering around its centroid.
    xy = pts[:, :2]
    center = xy.mean(axis=0)
    order = np.argsort(np.arctan2(xy[:, 1] - center[1], xy[:, 0] - center[0]))
    ordered = xy[order]
    return ordered[:, 0], ordered[:, 1]


def _draw_xy_box(ax, corners, **kwargs):
    projected = box_xy(corners)
    if projected is None:
        return
    x, y = projected
    ax.plot(np.r_[x, x[0]], np.r_[y, y[0]], **kwargs)


def _draw_3d_box(ax, corners, **kwargs):
    if not corners:
        return
    pts = np.asarray(corners, dtype=float)
    edges = ((0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3),
             (2, 6), (3, 7), (4, 5), (4, 6), (5, 7), (6, 7))
    for left, right in edges:
        ax.plot(pts[[left, right], 0], pts[[left, right], 1],
                pts[[left, right], 2], **kwargs)


def export_3d_translation() -> None:
    rows, truths, sequence = load_case("translation_yaw", 0)
    frame_count = 120
    fig = plt.figure(figsize=(9, 7), constrained_layout=True)
    ax = fig.add_subplot(111, projection="3d")
    writer = FFMpegWriter(fps=10, metadata={"title": "NA70 Synthetic 3D goal tracking"})
    snapshot = 80
    with writer.saving(fig, str(OUT / "translation-yaw-realtime-3d.mp4"), dpi=130):
        for index in range(frame_count):
            ax.clear()
            points = sequence[index].points[:, :3]
            truth = truths[index]
            # Keep the moving target and local context readable.
            center = np.asarray(truth["center"][:2])
            keep = ((np.abs(points[:, 0] - center[0]) <= 10)
                    & (np.abs(points[:, 1] - center[1]) <= 10)
                    & (points[:, 2] >= -1.5) & (points[:, 2] <= 4.5))
            local = points[keep]
            if len(local) > 700:
                local = local[np.linspace(0, len(local) - 1, 700, dtype=int)]
            ax.scatter(local[:, 0], local[:, 1], local[:, 2], s=5, alpha=0.55,
                       c=local[:, 2], cmap="viridis", linewidths=0, rasterized=True)
            # Truth is dashed; selected measurement is solid and carries the
            # tracker state in the title.
            truth_box = {"center": truth["center"], "size": truth["size"],
                         "yaw": truth["yaw"]}
            _draw_3d_box(ax, oriented_box_corners(truth_box["center"],
                                                    truth_box["size"], truth_box["yaw"]),
                         color="#6b7280", linestyle="--", linewidth=1.0, alpha=0.8)
            measurement = rows[index].get("selected_measurement")
            if measurement:
                _draw_3d_box(ax, measurement.get("corners"), color="#d95f59",
                             linestyle="-", linewidth=1.8)
            goal = rows[index].get("goal", {})
            history = rows[:index + 1]
            track = [r["goal"]["center"] for r in history
                     if r.get("goal", {}).get("center")]
            if track:
                path = np.asarray(track, dtype=float)
                ax.plot(path[:, 0], path[:, 1], path[:, 2], color="#2d8c83",
                        linewidth=1.5, alpha=0.9)
            ax.set_xlim(center[0] - 10, center[0] + 10)
            ax.set_ylim(center[1] - 10, center[1] + 10)
            ax.set_zlim(-1.5, 4.5)
            ax.set_xlabel("x (m)")
            ax.set_ylabel("y (m)")
            ax.set_zlabel("z (m)")
            ax.view_init(elev=22, azim=-62)
            ax.set_title(
                f"translation_yaw · frame {index:03d} · "
                f"state={goal.get('state', 'SEARCHING')} · "
                f"measurement={'yes' if goal.get('has_measurement') else 'no'}")
            writer.grab_frame()
            if index == snapshot:
                fig.savefig(OUT / "translation-yaw-realtime-3d-frame080.png",
                            dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def export_2d_disappear() -> None:
    rows, truths, sequence = load_case("disappear_reenter", 0)
    frame_count = 120
    fig, ax = plt.subplots(figsize=(8, 7), constrained_layout=True)
    writer = PillowWriter(fps=10)
    with writer.saving(fig, str(OUT / "disappear-reenter-realtime-2d.gif"), dpi=120):
        for index in range(frame_count):
            ax.clear()
            points = sequence[index].points[:, :3]
            truth = truths[index]
            center = np.asarray(truth["center"][:2] if truth["center"] else [75.0, 0.0])
            keep = ((np.abs(points[:, 0] - center[0]) <= 10)
                    & (np.abs(points[:, 1] - center[1]) <= 10))
            local = points[keep]
            if len(local) > 900:
                local = local[np.linspace(0, len(local) - 1, 900, dtype=int)]
            ax.scatter(local[:, 0], local[:, 1], s=7, alpha=0.5,
                       c=local[:, 2], cmap="viridis", linewidths=0)
            measurement = rows[index].get("selected_measurement")
            if measurement:
                _draw_xy_box(ax, measurement.get("corners"), color="#d95f59",
                             linewidth=2.0, label="selected measurement")
            goal = rows[index].get("goal", {})
            track = [r["goal"]["center"] for r in rows[:index + 1]
                     if r.get("goal", {}).get("center")]
            if track:
                path = np.asarray(track, dtype=float)
                ax.plot(path[:, 0], path[:, 1], color="#2d8c83", linewidth=1.8,
                        label="track center")
            ax.set_xlim(center[0] - 10, center[0] + 10)
            ax.set_ylim(center[1] - 10, center[1] + 10)
            ax.set_xlabel("x (m)")
            ax.set_ylabel("y (m)")
            ax.set_aspect("equal", adjustable="box")
            ax.grid(alpha=0.25)
            ax.set_title(
                f"disappear_reenter · frame {index:03d} · "
                f"state={goal.get('state', 'SEARCHING')} · "
                f"measurement={'yes' if goal.get('has_measurement') else 'no'}")
            if index == 80:
                fig.savefig(OUT / "disappear-reenter-realtime-2d-frame080.png",
                            dpi=180, bbox_inches="tight", facecolor="white")
            writer.grab_frame()
    plt.close(fig)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    export_3d_translation()
    export_2d_disappear()
    print(f"exported realtime media to {OUT}")
    for path in sorted(OUT.iterdir()):
        print(path.name, path.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

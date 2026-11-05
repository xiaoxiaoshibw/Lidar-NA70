"""Compare FAST-LIO replay trajectories from their TUM-style CSV exports.

Canonical tool behind the replay-evidence comparison table and figure: given
two or more ``trajectory.csv`` files (columns ``t_s,x,y,z,qx,qy,qz,qw`` as
recorded by the WSL2 FAST-LIO replay), it recomputes the internal statistics
exactly as tabulated in the evidence REPORTs and renders the three-view
trajectory figure.

Boundary: every run lives in its own map frame.  The trajectories are NOT in a
shared coordinate system, so this comparison supports internal statistics only
-- never accuracy attribution or improvement claims.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

COLORS = ("#c1272d", "#1f6fb2", "#2e8b57", "#7b4397", "#b8860b")

VIEW_PAIRS = ((0, 1, "Top: X-Y"), (0, 2, "Side: X-Z"), (1, 2, "Front: Y-Z"))
BOUNDARY_NOTE = ("各轨迹位于各自地图系，不共坐标系；仅内部统计，"
                 "不归因参数、不宣称精度改善 (ACCURACY_NOT_EVALUATED)")


def parse_run(spec: str):
    label, _, path = spec.partition("=")
    if not label or not path:
        raise argparse.ArgumentTypeError(f"run must be LABEL=PATH, got {spec!r}")
    return label, Path(path)


def load_trajectory(path: Path) -> np.ndarray:
    rows = []
    with path.open("r", encoding="ascii") as stream:
        header = stream.readline().strip().split(",")
        if header[:8] != ["t_s", "x", "y", "z", "qx", "qy", "qz", "qw"]:
            raise ValueError(f"{path}: unexpected trajectory header {header}")
        for line in stream:
            values = line.strip().split(",")
            if len(values) != 8:
                continue
            rows.append([float(value) for value in values])
    if len(rows) < 2:
        raise ValueError(f"{path}: needs at least two pose rows")
    trajectory = np.asarray(rows, dtype=np.float64)
    if not np.isfinite(trajectory).all():
        raise ValueError(f"{path}: contains non-finite values")
    if (np.diff(trajectory[:, 0]) < 0).any():
        raise ValueError(f"{path}: timestamps must be non-decreasing")
    return trajectory


def trajectory_statistics(trajectory: np.ndarray) -> dict:
    times = trajectory[:, 0]
    positions = trajectory[:, 1:4]
    gaps = np.diff(times)
    steps = np.linalg.norm(np.diff(positions, axis=0), axis=1)
    radii = np.linalg.norm(positions, axis=1)
    return {
        "pose_rows": int(len(trajectory)),
        "time_span_s": float(times[-1] - times[0]),
        "path_length_m": float(steps.sum()),
        "max_gap_s": float(gaps.max()),
        "median_gap_s": float(np.median(gaps)),
        "max_radius_m": float(radii.max()),
        "max_step_m": float(steps.max()),
    }


def save_figure(path: Path, runs, title: str | None) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, axes = plt.subplots(1, 3, figsize=(16.5, 5.6), constrained_layout=True)
    for index, (label, trajectory, _stats) in enumerate(runs):
        color = COLORS[index % len(COLORS)]
        positions = trajectory[:, 1:4]
        for ax, (a, b, view_title) in zip(axes, VIEW_PAIRS):
            ax.plot(positions[:, a], positions[:, b], color=color, lw=1.2,
                    alpha=0.85, label=label)
            ax.scatter(positions[0, a], positions[0, b], marker="^", c=color,
                       s=70, zorder=3, edgecolors="black", linewidths=0.6)
            ax.scatter(positions[-1, a], positions[-1, b], marker="v", c=color,
                       s=70, zorder=3, edgecolors="black", linewidths=0.6)
            ax.set(xlabel="XYZ"[a] + " (m)", ylabel="XYZ"[b] + " (m)",
                   title=view_title, aspect="equal")
            ax.grid(alpha=0.25)
    axes[0].legend(loc="upper right", fontsize=9)
    fig.suptitle((title or "Replay trajectory comparison")
                 + "  |  ▲ start  ▼ end  |  " + BOUNDARY_NOTE, fontsize=9)
    fig.savefig(path, dpi=170)
    plt.close(fig)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", type=parse_run,
                        required=True, metavar="LABEL=PATH",
                        help="trajectory.csv export (repeatable, >= 2)")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--title", default=None,
                        help="figure title prefix")
    parser.add_argument("--no-figure", action="store_true",
                        help="write the JSON report only")
    args = parser.parse_args(argv)
    if len(args.run) < 2:
        parser.error("at least two runs are required for a comparison")

    runs = []
    statistics = {}
    for label, path in args.run:
        trajectory = load_trajectory(path)
        stats = trajectory_statistics(trajectory)
        statistics[label] = {"source": str(path),
                             "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                             **stats}
        runs.append((label, trajectory, stats))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "mode": "DIAGNOSTIC_ONLY",
        "boundary": BOUNDARY_NOTE,
        "runs": statistics,
        "notes": ["位姿间隙与路径长度只描述记录本身，不描述设备真实运动",
                  "两轨迹不共坐标系；起点对齐只是绘图惯例"],
    }
    (args.out_dir / "replay-comparison.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf8")
    if not args.no_figure:
        save_figure(args.out_dir / "replay-comparison.png", runs, args.title)
    print(json.dumps({label: stats for label, _, stats in runs},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

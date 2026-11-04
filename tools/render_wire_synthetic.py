"""Render three-view figures for the SYNTHETIC wire scenario matrix.

Every figure carries a `SYNTHETIC` marker and a "code-behaviour evidence
only" caption. Sampling is capped for figure clarity only; it does not
change the algorithm inputs or labels.

Usage:
  ./web/.venv/Scripts/python.exe tools/render_wire_synthetic.py \
      --output Plan/evidence/hv-line/20261003-synthetic-matrix-v4/figures \
      [--scenario single_wire_50m] [--seed 0] [--max-points 4000]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT))

from na70_core.wire import SCENARIO_BUILDERS, build_scenario, load_wire_config  # noqa: E402
from na70_core.wire.fitting import fit_line_ransac  # noqa: E402
from na70_core.wire.proposals import make_manual_roi_proposal  # noqa: E402

MARKER = "SYNTHETIC"


def _seed_arrays(scene, max_points: int):
    batch = scene.batch
    valid = batch.valid_return & batch.direction_known
    rays = np.flatnonzero(valid)
    if len(rays) > max_points:
        sampled = rays[np.linspace(0, len(rays) - 1, max_points).astype(int)]
    else:
        sampled = rays
    xyz = batch.points[sampled, :3]
    return xyz, rays, sampled


def _fit_line(scene, cfg):
    proposal = make_manual_roi_proposal(scene.batch, tuple(scene.roi_aabb_m))
    fit = fit_line_ransac(scene.batch, proposal, cfg.fit)
    return fit


def render(scene_name: str, seed: int, output: Path, max_points: int):
    scene = build_scenario(scene_name, seed)
    cfg = load_wire_config()
    fit = _fit_line(scene, cfg)

    xyz, rays, sampled = _seed_arrays(scene, max_points)
    fig, axes = plt.subplots(1, 3, figsize=(21, 7), dpi=110)
    fig.suptitle(f"{MARKER} · {scene_name} · seed={seed}", fontsize=13)

    for ax, (xy, labels) in zip(axes, (
            ((0, 1), ("x/m", "y/m (top view)")),
            ((0, 2), ("x/m", "z/m (side view)")),
            ((1, 2), ("y/m", "z/m (front view)")))):
        ax.scatter(xyz[:, xy[0]], xyz[:, xy[1]], s=1, alpha=0.6, c="#8aa8b5", label="sampled valid rays")
        roi = np.asarray(scene.roi_aabb_m, dtype=float)
        ax.add_patch(plt.Rectangle((roi[xy[0] * 2], roi[xy[1] * 2]),
                                    roi[xy[0] * 2 + 1] - roi[xy[0] * 2],
                                    roi[xy[1] * 2 + 1] - roi[xy[1] * 2],
                                    fill=False, edgecolor="#d9a85c", linewidth=1.2,
                                    label="manual ROI"))
        if fit.ok and fit.direction is not None and fit.point_on_line is not None:
            direction = np.asarray(fit.direction, float)
            origin = np.asarray(fit.point_on_line, float)
            t_vals = np.linspace(-80.0, 80.0, 4)
            line_pts = origin[None, :] + t_vals[:, None] * direction[None, :]
            accept_mask = np.all((line_pts >= np.asarray(scene.roi_aabb_m)[::2]) &
                                 (line_pts <= np.asarray(scene.roi_aabb_m)[1::2]), axis=1) \
                if True else np.zeros(len(line_pts), bool)
            ax.plot(line_pts[:, xy[0]], line_pts[:, xy[1]], "-", color="#f27e65",
                    linewidth=1.4, alpha=0.9, label="best-fit line (extension)")
            inliers = scene.batch.points[fit.inlier_ray_indices, :3]
            ax.scatter(inliers[:, xy[0]], inliers[:, xy[1]], s=4, alpha=0.9,
                       c="#f27e65", label="selected inliers")
        ax.set_xlabel(labels[0])
        ax.set_ylabel(labels[1])
        ax.set_aspect("equal", adjustable="datalim")
        ax.grid(True, alpha=0.25)

    handles, labels_ = axes[1].get_legend_handles_labels()
    fig.legend(handles, labels_, loc="lower center", ncol=4, fontsize=10,
               frameon=False)
    fig.text(0.99, 0.01, MARKER + " · code-behaviour evidence only; not real-world wire capability",
             ha="right", va="bottom", fontsize=9, color="#d9a85c")
    fig.tight_layout(rect=(0.0, 0.035, 1.0, 0.97))
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"{scene_name}.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scenario", action="append", default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-points", type=int, default=4000)
    args = parser.parse_args(argv)

    names = args.scenario or list(SCENARIO_BUILDERS)
    written = []
    for i, name in enumerate(names):
        path = render(name, args.seed + i * 1000, args.output, args.max_points)
        written.append(str(path))
        print(json.dumps({"scenario": name, "figure": str(path)}, ensure_ascii=False))
    print(json.dumps({"output": str(args.output), "figures": written},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()

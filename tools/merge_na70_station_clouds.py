"""Merge multiple static station clouds into one model with trimmed ICP.

P2 support tool: each station is a static accumulated cloud in its own sensor
frame (e.g. produced by the classroom-static pipeline).  Station 0 anchors the
merged frame; later stations are ICP-aligned to the merged-so-far cloud via a
deterministic yaw-grid multistart and refused when fitness/RMSE gates fail.
Outputs an ascii PLY, a JSON report and optionally a three-view PNG.

Real-world caveat (measured on the 20261105 classroom static cloud): in
periodic indoor scenes the yaw grid alone may land every seed in a
self-consistent WRONG lock (fitness 0.998).  Supply --initial-yaw-deg /
--initial-translation when the relative station pose is roughly known, and
always audit merge-views.png against the seed table in merge-report.json
before trusting a merge.

Honesty: the merged frame is the first station's sensor frame (arbitrary);
fitness/RMSE are self-consistency numbers only.  No accuracy claim is possible
without independent truth or calibrated extrinsics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "na70_core"))
sys.path.insert(0, str(ROOT / "web"))

from na70_core.registration import (LIMITATIONS, StationMerge, VoxelCloud,
                                    merge_stations, voxel_average)


def parse_station(spec: str):
    label, _, path = spec.partition("=")
    if not label or not path:
        raise argparse.ArgumentTypeError(f"station must be LABEL=PATH, got {spec!r}")
    return label, Path(path)


def load_ascii_ply(path: Path):
    """Read an ascii PLY with x/y/z and an optional observations property."""
    with path.open("rb") as source:
        header = []
        while True:
            line = source.readline()
            if not line:
                raise ValueError(f"{path}: PLY header ended unexpectedly")
            text = line.decode("ascii", errors="replace").strip()
            header.append(text)
            if text == "end_header":
                break
    if not header or header[0] != "ply" or "format ascii 1.0" not in header:
        return None
    count = None
    properties = []
    for line in header:
        if line.startswith("element vertex "):
            count = int(line.split()[2])
        elif line.startswith("property ") and count is not None:
            properties.append(line.split()[2])
    if count is None or "x" not in properties or "y" not in properties or "z" not in properties:
        return None
    columns = {name: index for index, name in enumerate(properties)}
    xyz = np.empty((count, 3), dtype=np.float64)
    weights = np.ones(count, dtype=np.float64)
    with path.open("r", encoding="ascii") as stream:
        for _ in range(len(header)):
            next(stream)
        for row, line in enumerate(stream):
            if row >= count:
                break
            values = line.split()
            xyz[row] = [float(values[columns[axis]]) for axis in "xyz"]
            if "observations" in columns:
                weights[row] = float(values[columns["observations"]])
    if "observations" in columns and (weights <= 0).any():
        raise ValueError(f"{path}: observations must be positive")
    return xyz, weights


def load_station(path: Path):
    loaded = load_ascii_ply(path)
    if loaded is not None:
        xyz, weights = loaded
    else:
        from na70_core.map_quality import load_ply
        structured = load_ply(path)
        xyz = np.column_stack([structured["x"], structured["y"], structured["z"]])
        weights = None
    return xyz, weights


def save_merged_ply(path: Path, cloud: VoxelCloud, voxel: float, labels) -> None:
    counts = np.rint(cloud.weights).astype(np.int64)
    header = (
        "ply\nformat ascii 1.0\n"
        "comment DIAGNOSTIC merged station cloud; frame = first station sensor frame (arbitrary)\n"
        f"comment stations: {','.join(labels)}\n"
        f"comment voxel {voxel:.4f} m mean-preserving; fitness gates in merge-report.json\n"
        "element vertex %d\nproperty float x\nproperty float y\nproperty float z\n"
        "property uint observations\nend_header\n" % cloud.size
    )
    with path.open("w", encoding="ascii", newline="\n") as stream:
        stream.write(header)
        for (x, y, z), n in zip(cloud.means, counts):
            stream.write("%.4f %.4f %.4f %d\n" % (x, y, z, n))


def save_views(path: Path, cloud: VoxelCloud, labels, voxel: float) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    weights = np.log10(cloud.weights + 1.0)
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), constrained_layout=True)
    pairs = [(0, 1, "Top: X-Y"), (0, 2, "Side: X-Z"), (1, 2, "Front: Y-Z")]
    for ax, (a, b, title) in zip(axes, pairs):
        ax.scatter(cloud.means[:, a], cloud.means[:, b], c=weights, s=1,
                   cmap="viridis", rasterized=True)
        ax.set(xlabel="XYZ"[a] + " (m)", ylabel="XYZ"[b] + " (m)", title=title,
               aspect="equal")
        ax.grid(alpha=0.25)
    fig.suptitle(
        "SYNTHETIC-TOOL-DIAGNOSTIC merged " + "+".join(labels)
        + f" | voxel {voxel:.2f} m | frame = station '{labels[0]}' (arbitrary) |"
          " color = log10(observations + 1); no accuracy claim",
        fontsize=9)
    fig.savefig(path, dpi=170)
    plt.close(fig)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", action="append", type=parse_station,
                        required=True, metavar="LABEL=PATH",
                        help="station cloud, ascii or binary PLY (repeatable)")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--voxel", type=float, default=0.05,
                        help="merge voxel size in metres (default 0.05)")
    parser.add_argument("--schedule", default="1.0,0.35,0.12",
                        help="comma-separated ICP voxel schedule, strictly decreasing")
    parser.add_argument("--trim", type=float, default=0.1,
                        help="residual trim fraction (default 0.1; strong trim "
                             "can bias toward crop edges)")
    parser.add_argument("--correspondence-factor", type=float, default=3.0)
    parser.add_argument("--fitness-gate", type=float, default=0.3)
    parser.add_argument("--rmse-gate", type=float, default=None,
                        help="optional RMSE gate in metres")
    parser.add_argument("--yaw-starts", type=int, default=8,
                        help="deterministic yaw-grid seeds per station "
                             "alignment (default 8)")
    parser.add_argument("--initial-yaw-deg", type=float, default=None,
                        help="operator yaw prior applied to every non-anchor "
                             "station before the seed grid")
    parser.add_argument("--initial-translation", type=float, nargs=3,
                        default=None, metavar=("X", "Y", "Z"),
                        help="operator translation prior (metres) for every "
                             "non-anchor station")
    parser.add_argument("--views", action="store_true",
                        help="also write merge-views.png")
    args = parser.parse_args(argv)

    if len(args.station) < 2:
        parser.error("at least two stations are required for a merge")
    schedule = [float(size) for size in args.schedule.split(",")]
    initial = None
    if args.initial_yaw_deg is not None or args.initial_translation is not None:
        yaw = args.initial_yaw_deg or 0.0
        translation = np.array(args.initial_translation or [0.0, 0.0, 0.0])
        angle = np.radians(yaw)
        initial = (np.array([[np.cos(angle), -np.sin(angle), 0.0],
                             [np.sin(angle), np.cos(angle), 0.0],
                             [0.0, 0.0, 1.0]]), translation)

    clouds = []
    inputs = []
    for label, path in args.station:
        xyz, weights = load_station(path)
        clouds.append(voxel_average(xyz, args.voxel, weights=weights))
        inputs.append({"label": label, "path": str(path),
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "points": int(len(xyz)),
                       "voxels": int(clouds[-1].size)})

    merge: StationMerge = merge_stations(
        clouds, labels=[label for label, _ in args.station],
        voxel=args.voxel, fitness_gate=args.fitness_gate,
        rmse_gate=args.rmse_gate, voxel_schedule=schedule,
        trim_fraction=args.trim,
        correspondence_factor=args.correspondence_factor,
        yaw_starts=args.yaw_starts, initials=None if initial is None
        else [initial] * (len(args.station) - 1))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    labels = [label for label, _ in args.station]
    save_merged_ply(args.out_dir / "merged.ply", merge.merged, args.voxel, labels)
    if args.views:
        save_views(args.out_dir / "merge-views.png", merge.merged, labels,
                   args.voxel)
    report = {
        "mode": "DIAGNOSTIC_ONLY",
        "inputs": inputs,
        "parameters": {"voxel_m": args.voxel, "icp_voxel_schedule": schedule,
                       "trim_fraction": args.trim,
                       "correspondence_factor": args.correspondence_factor,
                       "fitness_gate": args.fitness_gate,
                       "rmse_gate_m": args.rmse_gate},
        "accepted": merge.accepted,
        "refusals": merge.refusals,
        "registrations": {label: reg.describe()
                          for label, reg in merge.registrations.items()},
        "merged": {"voxel_m": merge.voxel, "voxels": int(merge.merged.size),
                   "total_observations": float(merge.merged.weights.sum()),
                   "frame": merge.frame_note},
        "limitations": list(LIMITATIONS),
    }
    (args.out_dir / "merge-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf8")
    print(json.dumps({"accepted": merge.accepted,
                      "refusals": [r["label"] for r in merge.refusals],
                      "merged_voxels": int(merge.merged.size),
                      "output": str(args.out_dir)}, ensure_ascii=False))
    if len(merge.accepted) < 2:
        print("fewer than two stations accepted; merged.ply is the anchor "
              "cloud only", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

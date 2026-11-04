"""Read-only REAL_PCAP diagnostics; ablations are not accepted detections.

Replay selected raw windows through the existing geometry implementation and
record what each gate removes. Never updates production config or annotations.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import platform
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
from backend import decoder
from backend.goal_detection.accumulation import _voxel_downsample
from backend.goal_detection.bev import bev_regions
from backend.goal_detection.config import load_configuration, configuration_version
from backend.goal_detection.detector import GoalDetector
from backend.goal_detection.pipeline import _estimate_ground_level, _apply_ground_level
from run_goal_demo_offline import sha256_file, source_hashes


def summarize_run(path):
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    reasons = defaultdict(Counter)
    counts = Counter()
    measured_frames = []
    candidate_rows = []
    for row in rows:
        counts["frames"] += 1
        counts["state:" + row["goal"]["state"]] += 1
        if row.get("selected_measurement"):
            measured_frames.append(row["frame_index"])
        if row.get("accumulated_best"):
            candidate_rows.append({"frame": row["frame_index"],
                                   "candidate": row["accumulated_best"]})
        for stage in row["execution_trace"]["accumulated"]:
            name = stage["stage"]
            counts[name + ":" + stage["status"]] += 1
            if stage.get("output_entities", 0) > 0:
                counts[name + ":nonzero_calls"] += 1
            if name == "measurement_freshness":
                counts["fresh_candidates"] += stage.get("output_entities", 0)
            reasons[name].update(stage.get("reason_counts", {}))
            reason = stage.get("reason") or stage.get("reject_reason")
            if reason:
                reasons[name][reason] += 1
    return {"counts": counts, "reasons": reasons,
            "selected_measurement_frames": measured_frames,
            "candidate_rows": candidate_rows}


def candidate_summary(candidate):
    public = candidate.as_dict()
    return {key: public[key] for key in
            ("center", "size", "yaw", "confidence", "crossbar", "reject_reason")}


def probe(points, cfg, frames_in_window):
    detector = GoalDetector(cfg["goal_detection"])
    detector.set_range(cfg["accumulation"]["range"]["min"],
                       cfg["accumulation"]["range"]["max"])
    candidates = detector.detect(points)
    diag = {}
    regions = bev_regions(points[:, :3], frames_in_window, cfg["bev"],
                          cfg["goal_detection"]["frame"], diagnostics=diag)
    return {"points": len(points), "bev": diag,
            "regions": [r.as_dict() for r in regions],
            "detector_trace": detector.last_trace,
            "candidates": [candidate_summary(c) for c in candidates],
            "rejected_pairs": [candidate_summary(c) for c in detector.last_rejected]}


def density_stats(points, frames, cfg):
    keys = np.floor(points[:, :2] / cfg["bev"]["cell_size"]).astype(np.int64)
    _, counts = np.unique(keys, axis=0, return_counts=True)
    peak = int(counts.max()) if len(counts) else 0
    return {"max_cell_count": peak, "max_density": peak / frames,
            "required_cell_count": max(cfg["bev"]["min_cell_points"],
                                        cfg["bev"]["min_cell_density"] * frames)}


def plot_windows(windows, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(len(windows), 2, figsize=(13, 3.4 * len(windows)),
                             squeeze=False, constrained_layout=True)
    for row, (name, points) in enumerate(windows):
        for col, (a, b, xlabel, ylabel) in enumerate(
                [(1, 2, "sensor Y (m)", "sensor Z (m)"),
                 (1, 0, "sensor Y (m)", "sensor X (m)")]):
            ax = axes[row, col]
            ax.scatter(points[:, a], points[:, b], s=2, c=points[:, 2],
                       cmap="viridis", vmin=-2, vmax=4, rasterized=True)
            if col == 0:
                ax.axhline(0.3, color="red", linestyle="--", label="z gate = 0.3 m")
                ax.set_ylim(-2, 6)
                ax.legend(loc="upper left", fontsize=8)
            else:
                ax.set_ylim(84, 104)
            ax.set_xlim(-36, -17)
            ax.set(xlabel=xlabel, ylabel=ylabel,
                   title=f"{name}: {len(points)} raw ROI returns")
            ax.grid(alpha=0.2)
    fig.suptitle("REAL_PCAP / raw sensor coordinates / no motion compensation")
    fig.savefig(output, dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcap", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ends", default="83,199,327,595,650,754,892,1123")
    parser.add_argument("--windows", default="20,200")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    cfg = load_configuration()
    cfg["accumulation"]["roi"] = list(cfg["webui"]["demo_roi"])
    bounds = np.asarray(cfg["accumulation"]["roi"]).reshape(3, 2)
    ends = [int(v) for v in args.ends.split(",")]
    sizes = [int(v) for v in args.windows.split(",")]
    if not ends or min(ends) < 0 or not sizes or min(sizes) < 1:
        parser.error("ends must be nonnegative; windows must be positive")
    scan = decoder.scan_pcap(args.pcap)
    cropped = []
    current_raw = {}
    raw_counts = []
    with args.pcap.open("rb") as handle:
        for frame in scan.frames:
            points = decoder.decode_frame(args.pcap, frame, source=handle)
            if frame["index"] in ends:
                current_raw[frame["index"]] = points.copy()
            xyz = points[:, :3]
            keep = np.isfinite(xyz).all(axis=1)
            keep &= np.all((xyz >= bounds[:, 0]) & (xyz <= bounds[:, 1]), axis=1)
            cropped.append(points[keep])
            raw_counts.append({"frame": frame["index"], "roi_points": int(keep.sum())})
    results, plots = [], []
    for end in ends:
        for size in sizes:
            if end >= len(cropped):
                continue
            start = max(0, end - size + 1)
            raw = np.concatenate(cropped[start:end + 1])
            if not len(raw):
                continue
            xyzi, intensity = _voxel_downsample(raw[:, :3], raw[:, 3], .5, 200000)
            cloud = np.column_stack((xyzi, intensity))
            leveling = _estimate_ground_level(current_raw[end], cloud, cfg,
                                              cfg["goal_detection"]["z_leveling"])
            leveled = _apply_ground_level(cloud, leveling)
            current_cfg = deepcopy(cfg)
            current_cfg["accumulation"]["window_size"] = end - start + 1
            variations = {
                "production_geometry": (leveled, current_cfg),
                "raw_no_voxel": (raw, deepcopy(current_cfg)),
            }
            width_cfg = deepcopy(current_cfg)
            width_cfg["goal_detection"]["frame"]["width"] = [6.5, 8.0]
            variations["width_6p5_to_8_only"] = (leveled, width_cfg)
            # Deliberate diagnostic coordinate intervention, not a fitted plane
            # or a recommended production setting. The legacy box base is -1.3 m.
            shifted = cloud.copy()
            shifted[:, 2] += 1.3
            variations["diagnostic_z_plus_1p3_only"] = (shifted, deepcopy(current_cfg))
            variations["diagnostic_z_plus_1p3_and_width"] = (shifted, width_cfg)
            raw_shifted = raw.copy()
            raw_shifted[:, 2] += 1.3
            variations["diagnostic_z_plus_1p3_raw"] = (raw_shifted, deepcopy(current_cfg))
            fine_xyz, fine_i = _voxel_downsample(raw[:, :3], raw[:, 3], .1, 200000)
            fine = np.column_stack((fine_xyz, fine_i))
            fine[:, 2] += 1.3
            variations["diagnostic_z_plus_1p3_voxel_0p1"] = (fine, deepcopy(current_cfg))
            # Inspect an independent window-ground estimate, while preserving
            # the actual single-frame production estimate above.
            window_leveling = _estimate_ground_level(np.empty((0, 4)), raw, cfg,
                                                     cfg["goal_detection"]["z_leveling"])
            entry = {"start": start, "end": end, "window_frames": end-start+1,
                     "raw_roi_points": len(raw), "voxel_points": len(cloud),
                     "raw_z_quantiles": np.quantile(raw[:, 2], [0, .1, .5, .9, 1]).tolist(),
                     "raw_above_z_gate": int((raw[:, 2] > .3).sum()),
                     "production_leveling": leveling,
                     "window_ground_probe": window_leveling, "ablations": {}}
            entry["raw_bev_density"] = density_stats(raw, end-start+1, cfg)
            entry["voxel_bev_density"] = density_stats(cloud, end-start+1, cfg)
            for name, (input_cloud, config) in variations.items():
                entry["ablations"][name] = probe(input_cloud, config, end-start+1)
            results.append(entry)
            np.savez_compressed(args.output / f"raw-roi-{start}-{end}.npz", points=raw)
            if (end, size) in {(83, 20), (199, 200), (327, 20), (650, 20), (1123, 20)}:
                plots.append((f"frames {start}-{end}", raw))
            print(f"window {start}-{end}: raw={len(raw)} voxel={len(cloud)}", flush=True)
    summary = {"label": "REAL_PCAP_DIAGNOSTIC_NOT_ACCEPTANCE",
               "command": [sys.executable, *sys.argv],
               "environment": {"python": sys.version, "platform": platform.platform(),
                               "numpy": np.__version__},
               "pcap": str(args.pcap), "pcap_sha256": sha256_file(args.pcap),
               "config": cfg, "config_version": configuration_version(cfg),
               "source_hashes": {**source_hashes(),
                                 "tools/audit_goal_failure.py": sha256_file(Path(__file__))},
               "baseline": summarize_run(args.baseline / "goal_run.jsonl"),
               "diagnostic_interventions": {
                   "z_shift_m": 1.3, "width_band_m": [6.5, 8.0],
                   "voxel_sizes_m": [0.5, 0.1],
                   "meaning": "Explicit counterfactual probes, not tuned production settings"},
               "raw_roi_counts": raw_counts, "windows": results,
               "limitations": ["No reviewed ground truth; hypotheses are not confirmed goals.",
                               "Raw/no-voxel bypasses production leveling to inspect absolute gates.",
                               "Z+1.3 and width interventions are diagnostics, not recommended thresholds.",
                               "ROI point counts include clutter; longer windows are uncompensated."]}
    (args.output / "audit.json").write_text(json.dumps(summary, indent=2), "utf-8")
    plot_windows(plots, args.output / "raw-window-comparison.png")


if __name__ == "__main__":
    main()

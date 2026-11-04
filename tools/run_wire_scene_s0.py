"""Generate a SYNTHETIC outdoor scene and audit real NA70 scan directions.

Example from the repository root:
  web/.venv/Scripts/python.exe tools/run_wire_scene_s0.py --pcap radar_capture_data/1.pcap \
      --output Plan/evidence/thin-wire/s0-example --frames 20
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
from backend import decoder  # noqa: E402
from backend.wire_scene import (  # noqa: E402
    coverage_intervals, load_scene, nominal_world_directions,
    ray_directions_from_codes, ray_wire_hits, sample_geometry, wire_curve,
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def save_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def render(scene: dict, points: np.ndarray, labels: np.ndarray, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(1, 3, figsize=(15, 5), constrained_layout=True)
    colors = {"ground": "#b2b8aa", "support": "#555555", "wire_truth": "#d93030",
              "tree": "#4c925d", "fence": "#b17e40", "branch": "#7e613d"}
    views = [(0, 1, "XY top"), (1, 2, "YZ wire front"), (0, 2, "XZ side")]
    path_world = np.asarray(scene["uav"]["path_world"])
    for ax, (i, j, name) in zip(axes, views):
        for label, color in colors.items():
            mask = labels == label
            if np.any(mask):
                ax.scatter(points[mask, i], points[mask, j], s=2 if label != "wire_truth" else 9,
                           c=color, alpha=.55 if label == "ground" else .9, label=label)
        ax.plot(path_world[:, i], path_world[:, j], color="#1976a3", linewidth=2, linestyle="--")
        ax.scatter(path_world[:, i], path_world[:, j], c="#1976a3", s=35)
        ax.set_title(name)
        ax.set_xlabel("xyz"[i] + " / m")
        ax.set_ylabel("xyz"[j] + " / m")
        ax.set_aspect("equal", adjustable="box")
        ax.grid(alpha=.2)
    fig.suptitle("SYNTHETIC geometry only — no NA70 wire returns", fontsize=17)
    fig.savefig(path, dpi=140)
    plt.close(fig)


def audit(pcap: Path, scene: dict, frame_limit: int, install_direction: int) -> tuple[list[dict], dict]:
    scan = decoder.scan_pcap(pcap)
    frames = scan.frames[:frame_limit]
    if not frames:
        raise ValueError("No frames available")
    curve = wire_curve(scene)
    enabled = scene["wire"].get("enabled", True)
    origins = {"path_start": np.asarray(scene["uav"]["path_world"][0], dtype=float),
               "path_end": np.asarray(scene["uav"]["path_world"][1], dtype=float)}
    rows = []
    all_hits = {key: [] for key in origins}
    all_dirs = {key: [] for key in origins}
    times = []
    with pcap.open("rb") as source:
        for frame in frames:
            codes = decoder.decode_frame_codes(pcap, frame, source=source)
            sensor = ray_directions_from_codes(codes, frame, install_direction)
            if install_direction != 1:
                raise ValueError("Synthetic mounting is defined only for install_direction=1")
            directions = nominal_world_directions(
                sensor, scene["sensor_model"]["synthetic_mount_pitch_down_deg"])
            # Some zero-range records have both angles zero. Their emitted
            # direction cannot be recovered from this packet, so exclude them.
            known_direction = (codes[:, 0] != 0) | (codes[:, 1] != 0) | (codes[:, 2] != 0)
            if len(codes) != 200 * frame["packet_count"]:
                raise ValueError("Packet/point count mismatch")
            hit_by_pose = {}
            for pose, origin in origins.items():
                hits = np.zeros(len(codes), dtype=bool)
                if enabled and np.any(known_direction):
                    hits[known_direction] = ray_wire_hits(
                        directions[known_direction], origin, curve,
                        scene["wire"]["diameter_m"] / 2)[0]
                hit_by_pose[pose] = hits
                all_hits[pose].append(np.flatnonzero(hits) + sum(len(d) for d in all_dirs[pose]))
                all_dirs[pose].append(directions)
            for packet in range(frame["packet_count"]):
                part = slice(packet * 200, (packet + 1) * 200)
                stamp = frame["packet_device_times"][packet]
                times.append(stamp)
                rows.append({
                    "frame_index": frame["index"], "packet_index": frame["packet_start_index"] + packet,
                    "device_time": stamp, "capture_time": frame["packet_capture_times"][packet],
                    "ray_count": 200, "zero_distance_code_count": int(np.count_nonzero(codes[part, 0] == 0)),
                    "known_direction_count": int(np.count_nonzero(known_direction[part])),
                    "unknown_direction_count": int(np.count_nonzero(~known_direction[part])),
                    "geometric_opportunities": {pose: int(np.count_nonzero(hits[part]))
                                                for pose, hits in hit_by_pose.items()},
                    "opportunities_with_zero_distance_code": {
                        pose: int(np.count_nonzero(hits[part] & (codes[part, 0] == 0)))
                        for pose, hits in hit_by_pose.items()},
                    "opportunities_with_nonzero_distance_code_unattributed": {
                        pose: int(np.count_nonzero(hits[part] & (codes[part, 0] != 0)))
                        for pose, hits in hit_by_pose.items()},
                    "confirmed_wire_returns": None,
                })
    summary = {"scan_total_frames": len(scan.frames), "audited_frames": len(frames),
               "audited_packets": len(rows), "audited_rays": len(rows) * 200,
               "zero_distance_code_count": sum(row["zero_distance_code_count"] for row in rows),
               "known_direction_count": sum(row["known_direction_count"] for row in rows),
               "unknown_direction_count": sum(row["unknown_direction_count"] for row in rows),
               "device_time_start": times[0], "device_time_end": times[-1],
               "device_time_valid": all(t is not None and np.isfinite(t) for t in times),
               "device_time_monotonic": all(a is not None and b is not None and b >= a
                                            for a, b in zip(times, times[1:])),
               "poses": {}, "causal_windows": []}
    for pose, origin in origins.items():
        index = np.concatenate(all_hits[pose]) if all_hits[pose] else np.empty(0, dtype=int)
        directions = np.concatenate(all_dirs[pose])
        coverage = coverage_intervals(index, directions, origin, curve)
        summary["poses"][pose] = {"origin_world_m": origin.tolist(),
                                  "geometric_opportunity_rays": int(len(index)),
                                  "opportunities_with_zero_distance_code": sum(
                                      row["opportunities_with_zero_distance_code"][pose] for row in rows),
                                  "opportunities_with_nonzero_distance_code_unattributed": sum(
                                      row["opportunities_with_nonzero_distance_code_unattributed"][pose]
                                      for row in rows),
                                  "packets_with_opportunity": sum(row["geometric_opportunities"][pose] > 0 for row in rows),
                                  **coverage}
    if summary["device_time_valid"] and summary["device_time_monotonic"]:
        end = float(times[-1])
        for duration in (None, 0.5, 1.0, 2.0, 5.0):
            start_packet = len(rows) - frames[-1]["packet_count"] if duration is None else next(
                (i for i, stamp in enumerate(times) if stamp > end - duration), len(rows))
            selected = rows[start_packet:]
            window = {"requested_duration_s": duration, "packet_count": len(selected),
                      "actual_span_s": float(times[-1] - times[start_packet]) if selected else None,
                      "partial_window": duration is not None and float(times[0]) > end - duration,
                      "poses": {}}
            for pose, origin in origins.items():
                index = np.concatenate(all_hits[pose]) if all_hits[pose] else np.empty(0, dtype=int)
                index = index[index >= start_packet * 200]
                dirs = np.concatenate(all_dirs[pose])
                window["poses"][pose] = {"geometric_opportunity_rays": int(len(index)),
                                          **coverage_intervals(index, dirs, origin, curve)}
            summary["causal_windows"].append(window)
    return rows, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, default=ROOT / "tools/scenarios/uav_single_wire_outdoor_v1.json")
    parser.add_argument("--pcap", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--install-direction", type=int, default=1)
    parser.add_argument("--wire-range-m", type=float, default=None,
                        help="Synthetic horizontal start-to-wire distance; moves wire and supports")
    parser.add_argument("--wire-absent", action="store_true", help="Negative-control scene")
    args = parser.parse_args()
    if args.frames <= 0:
        parser.error("--frames must be positive")
    if args.output.exists():
        parser.error("Output already exists; use a fresh directory")
    scene = load_scene(args.scene)
    if args.wire_range_m is not None:
        if not np.isfinite(args.wire_range_m) or args.wire_range_m <= 13.0:
            parser.error("--wire-range-m must be finite and > 13 m")
        old_x = float(scene["wire"]["endpoints"][0][0])
        shift = args.wire_range_m - old_x
        for point in scene["wire"]["endpoints"]:
            point[0] += shift
        for pole in scene["supports"]:
            pole["base"][0] += shift
        scene["uav"]["path_world"][1][0] = args.wire_range_m - 13.0
        scene["ground"]["x_range"][1] = max(scene["ground"]["x_range"][1], args.wire_range_m + 20.0)
    if args.wire_absent:
        scene["wire"]["enabled"] = False
    if args.install_direction != 1:
        parser.error("Only the declared synthetic install_direction=1 mounting is supported")
    if not args.pcap.is_file():
        parser.error("PCAP does not exist")
    points, labels = sample_geometry(scene, args.seed)
    rows, summary = audit(args.pcap, scene, args.frames, args.install_direction)
    args.output.mkdir(parents=True)
    save_json(args.output / "effective_scene.json", scene)
    np.savez_compressed(args.output / "geometry_world.npz", points=points, labels=labels)
    render(scene, points, labels, args.output / "scene_views.png")
    (args.output / "packet_opportunities.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows), encoding="utf-8")
    manifest = {"label": "SYNTHETIC_GEOMETRY_AND_REAL_NA70_SCAN_PATTERN",
                "wire_return_status": "NOT_EVALUATED", "geometric_hit_is_return": False,
                "scene_file": str(args.scene.resolve()), "scene_sha256": sha256(args.scene),
                "effective_scene_sha256": sha256(args.output / "effective_scene.json"),
                "pcap_file": str(args.pcap.resolve()), "pcap_sha256": sha256(args.pcap),
                "code_sha256": {name: sha256(path) for name, path in {
                    "runner": Path(__file__), "geometry": ROOT / "web/backend/wire_scene.py",
                    "decoder": ROOT / "web/backend/decoder.py"}.items()},
                "seed": args.seed, "frame_limit": args.frames, "install_direction": args.install_direction,
                "wire_absent": args.wire_absent, "wire_range_m": args.wire_range_m,
                "mounting_assumption": "decoded install_direction=1 -Y -> world +X, synthetic pitch down from scene; uncalibrated",
                "outputs": ["effective_scene.json", "geometry_world.npz", "scene_views.png",
                            "packet_opportunities.jsonl", "summary.json"]}
    save_json(args.output / "scene_manifest.json", manifest)
    save_json(args.output / "summary.json", summary)
    print(json.dumps({"output": str(args.output), "summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()

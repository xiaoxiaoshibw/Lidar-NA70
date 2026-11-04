"""Read-only no-IMU background registration probe for goal-window alignment.

The specified target ROIs are excluded from fitting, so a goal cannot supply
its own pose. Candidate transforms remain diagnostics until independent checks
establish their accuracy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))

from backend import decoder  # noqa: E402
from backend.mapping import configuration, preprocess  # noqa: E402
from backend.registration_se2 import register_se2  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_range(value: str) -> tuple[int, int]:
    start, end = (int(item) for item in value.split(":"))
    if start < 0 or end < start:
        raise ValueError("frame range must be START:END with 0 <= START <= END")
    return start, end


def parse_roi(value: str) -> np.ndarray:
    bounds = np.asarray([float(item) for item in value.split(",")], dtype=np.float64)
    if bounds.shape != (6,) or not np.isfinite(bounds).all():
        raise ValueError("ROI must contain six finite coordinates")
    result = bounds.reshape(3, 2)
    if np.any(result[:, 0] >= result[:, 1]):
        raise ValueError("ROI minimum must be below maximum")
    return result


def read_window(path: Path, frames: list[dict], start: int, end: int,
                exclusions: list[np.ndarray]) -> tuple[np.ndarray, int]:
    blocks = []
    raw_count = 0
    with path.open("rb") as source:
        for frame in frames[start:end + 1]:
            points = decoder.decode_frame(path, frame, source=source)
            raw_count += len(points)
            keep = np.isfinite(points).all(axis=1)
            keep &= np.linalg.norm(points[:, :3], axis=1) > 0
            for roi in exclusions:
                keep &= ~np.all((points[:, :3] >= roi[:, 0])
                                & (points[:, :3] <= roi[:, 1]), axis=1)
            blocks.append(points[keep])
    return (np.vstack(blocks) if blocks else np.empty((0, 4), dtype=np.float32)), raw_count


def pose_summary(pose) -> dict | None:
    if pose is None:
        return None
    array = np.asarray(pose, dtype=np.float64)
    return {"x_m": float(array[0, 3]), "y_m": float(array[1, 3]),
            "yaw_deg": float(np.degrees(np.arctan2(array[1, 0], array[0, 0])))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcap", required=True, type=Path)
    parser.add_argument("--pair", nargs=2, action="append", required=True,
                        metavar=("TARGET_START:END", "SOURCE_START:END"))
    parser.add_argument("--exclude-roi", action="append", default=[],
                        help="xmin,xmax,ymin,ymax,zmin,zmax; may repeat")
    parser.add_argument("--max-range", type=float, default=130.0)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not np.isfinite(args.max_range) or args.max_range <= 0:
        parser.error("--max-range must be positive and finite")
    exclusions = [parse_roi(item) for item in args.exclude_roi]
    pairs = [(parse_range(a), parse_range(b)) for a, b in args.pair]
    scan = decoder.scan_pcap(args.pcap)
    if max(end for pair in pairs for _, end in pair) >= len(scan.frames):
        parser.error("pair extends beyond PCAP frame count")
    cfg = configuration()
    cfg["max_range"] = args.max_range
    windows = {}
    for pair in pairs:
        for window in pair:
            if window not in windows:
                raw, raw_count = read_window(args.pcap, scan.frames, *window, exclusions)
                cloud, info = preprocess(raw, cfg)
                windows[window] = (cloud, {"raw_points": raw_count,
                                           "background_points": len(raw),
                                           "preprocessed_points": len(cloud),
                                           "preprocess": info})
    rows = []
    for target_range, source_range in pairs:
        target, target_info = windows[target_range]
        source, source_info = windows[source_range]
        methods = {}
        for method in ("point_to_plane", "gicp", "ndt"):
            pose, quality = register_se2(source, target, np.eye(4), cfg, method)
            methods[method] = {
                "state": quality.get("state"), "reason": quality.get("reason"),
                "accepted_pose": pose_summary(pose),
                "candidate_pose": pose_summary(quality.get("candidate_pose")),
                "rmse": quality.get("rmse"), "overlap": quality.get("overlap"),
            }
        rows.append({"target": list(target_range), "source": list(source_range),
                     "target_info": target_info, "source_info": source_info,
                     "methods": methods})
        print(f"{target_range} <- {source_range}: "
              + ", ".join(f"{name}={item['state']}" for name, item in methods.items()), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"label": "REAL_PCAP_DIAGNOSTIC_NOT_ACCEPTANCE",
               "command": [sys.executable, *sys.argv],
               "environment": {"python": platform.python_version(), "numpy": np.__version__},
               "input": {"path": str(args.pcap), "sha256": sha256(args.pcap),
                         "frames": len(scan.frames)},
               "source_hashes": {name: sha256(ROOT / name) for name in (
                   "tools/probe_goal_ego_motion.py", "web/backend/decoder.py",
                   "web/backend/mapping.py", "web/backend/registration_se2.py",
                   "web/backend/mapping.yaml")},
               "max_range": args.max_range,
               "excluded_rois": [roi.reshape(-1).tolist() for roi in exclusions],
               "methods": ["point_to_plane", "gicp", "ndt"], "pairs": rows}
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                                      default=lambda obj: obj.tolist() if isinstance(obj, np.ndarray) else
                                      float(obj) if isinstance(obj, np.floating) else
                                      int(obj) if isinstance(obj, np.integer) else str(obj)),
                           encoding="utf-8")


if __name__ == "__main__":
    main()

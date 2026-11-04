"""Fit a planar target in a NA70 PCAP frame range and compare a reference distance.

The ROI is expressed in the decoded LiDAR frame, so the operator/AI must supply
a crop that only contains the target.  The fitted normal distance includes the
unknown optical origin; without an independent origin measurement the intercept
is *not* a range-bias estimate.
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
from backend import decoder  # noqa: E402
from na70_core.calibration import planes  # noqa: E402


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_roi(text):
    if text is None:
        return None
    parts = [float(value) for value in text.split(",")]
    if len(parts) != 6:
        raise ValueError("--roi needs six comma-separated numbers")
    if not all(np.isfinite(parts)):
        raise ValueError("ROI values must be finite")
    if not (parts[0] < parts[1] and parts[2] < parts[3] and parts[4] < parts[5]):
        raise ValueError("ROI min must be smaller than max on every axis")
    return parts


def collect(pcap, start_frame, end_frame, roi, max_points, install_direction):
    scan = decoder.scan_pcap(pcap)
    if not 0 <= start_frame < end_frame <= len(scan.frames):
        raise ValueError(f"frame range must satisfy 0 <= start < end <= {len(scan.frames)}")
    selected = []
    total = 0
    first_time = None
    with pcap.open("rb") as source:
        for frame_index in range(start_frame, end_frame):
            frame = scan.frames[frame_index]
            if first_time is None and frame.get("packet_capture_times"):
                first_time = float(frame["packet_capture_times"][0])
            points = decoder.decode_frame(pcap, frame, install_direction, source=source)
            xyz = points[:, :3].astype(np.float64)
            valid = np.isfinite(xyz).all(axis=1) & (np.linalg.norm(xyz, axis=1) > 0)
            total += int(valid.sum())
            if roi is not None:
                valid &= ((xyz[:, 0] >= roi[0]) & (xyz[:, 0] <= roi[1])
                          & (xyz[:, 1] >= roi[2]) & (xyz[:, 1] <= roi[3])
                          & (xyz[:, 2] >= roi[4]) & (xyz[:, 2] <= roi[5]))
            if valid.any():
                selected.append(xyz[valid])
    points = np.concatenate(selected, axis=0) if selected else np.empty((0, 3))
    stride = 1
    if max_points and len(points) > max_points:
        stride = int(np.ceil(len(points) / max_points))
        points = points[::stride]
    return points, {"frames": end_frame - start_frame, "valid_points": total,
                    "stride": stride, "first_capture_time": first_time,
                    "scan_duration_s": scan.duration}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcap", required=True, type=Path)
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--end-frame", required=True, type=int)
    parser.add_argument("--roi", help="xmin,xmax,ymin,ymax,zmin,zmax in LiDAR frame")
    parser.add_argument("--reference-distance-m", type=float)
    parser.add_argument("--origin-offset-m", type=float, default=0.0)
    parser.add_argument("--max-points", type=int, default=400000)
    parser.add_argument("--max-residual-m", type=float, default=0.05)
    parser.add_argument("--install-direction", type=int, choices=(1, 2), default=1)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        roi = parse_roi(args.roi)
        points, meta = collect(args.pcap, args.start_frame, args.end_frame, roi,
                               args.max_points, args.install_direction)
        if len(points) < 3:
            raise ValueError("fewer than three selected points")
        plane = planes.plane_measurement(points, reference_distance_m=args.reference_distance_m,
                                        origin_offset_m=args.origin_offset_m,
                                        max_residual_m=args.max_residual_m)
        result = {
            "schema": "na70-lidar-plane-run-v1",
            "status": plane["status"],
            "source": {"pcap": str(args.pcap), "sha256": sha256(args.pcap)},
            "selection": {**meta, "roi": roi, "selected_points": int(len(points)),
                          "install_direction": args.install_direction},
            "plane": plane,
            "claim": "平面自洽与参考距离对比；不构成内部参数重标定或角度真值验收",
        }
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = {"schema": "na70-lidar-plane-run-v1", "status": "INVALID_DATA", "error": str(exc)}
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        destination.write(encoded)
    print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if result["status"] == "FIT" else 2


if __name__ == "__main__":
    raise SystemExit(main())
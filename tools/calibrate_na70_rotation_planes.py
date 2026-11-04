"""Estimate R_IL and a LiDAR-IMU time offset from a dominant wall sequence.

Windows-native alternative to LIO-based calibration: per-frame RANSAC plane
normal + device quaternion.  Requires a large flat wall facing the sensor and a
slow three-axis rotation.  Returns a rotation/time candidate only; translation
still needs a mechanical measurement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "na70_core"))
sys.path.insert(0, str(ROOT / "web"))
from backend import decoder  # noqa: E402
from na70_core.calibration import plane_extrinsic  # noqa: E402
from na70_core.calibration.frames import load_imu_frames  # noqa: E402


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
    if len(parts) != 6 or not all(math.isfinite(value) for value in parts):
        raise ValueError("--roi needs six finite numbers")
    if not (parts[0] < parts[1] and parts[2] < parts[3] and parts[4] < parts[5]):
        raise ValueError("ROI min must be smaller than max on every axis")
    return parts


def collect_planes(pcap, *, start_frame, end_frame, frame_step, min_range, max_range,
                   roi, threshold_m, iterations, min_inlier_ratio, max_points):
    scan = decoder.scan_pcap(pcap)
    if not 0 <= start_frame < end_frame <= len(scan.frames):
        raise ValueError("invalid frame range")
    normals = []
    times = []
    details = []
    previous = None
    jumps = 0
    with Path(pcap).open("rb") as source:
        for index in range(start_frame, end_frame, frame_step):
            frame = scan.frames[index]
            points = decoder.decode_frame(pcap, frame, source=source)[:, :3].astype(np.float64)
            valid = np.isfinite(points).all(axis=1)
            ranges = np.linalg.norm(points, axis=1)
            valid &= (ranges >= min_range) & (ranges <= max_range)
            if roi is not None:
                valid &= ((points[:, 0] >= roi[0]) & (points[:, 0] <= roi[1])
                          & (points[:, 1] >= roi[2]) & (points[:, 1] <= roi[3])
                          & (points[:, 2] >= roi[4]) & (points[:, 2] <= roi[5]))
            sample = points[valid]
            if len(sample) < 100:
                continue
            if max_points and len(sample) > max_points:
                sample = sample[::int(np.ceil(len(sample) / max_points))]
            try:
                plane = plane_extrinsic.ransac_plane(sample, threshold_m=threshold_m,
                                                     iterations=iterations, seed=index)
            except ValueError:
                continue
            if plane["inlier_ratio"] < min_inlier_ratio:
                continue
            if plane["residual_p95_m"] > 0.1:
                continue
            normal = plane["normal"]
            if previous is not None:
                cosine = float(np.clip(np.abs(np.dot(previous, normal)), -1.0, 1.0))
                if math.degrees(math.acos(cosine)) > 45.0:
                    jumps += 1
            previous = normal
            normals.append(normal)
            times.append(float(frame["packet_capture_times"][0]))
            details.append({"frame": index, "t_unix": times[-1],
                            "inlier_ratio": plane["inlier_ratio"],
                            "residual_p95_m": plane["residual_p95_m"],
                            "distance_m": float(np.dot(plane["normal"], plane["centroid"]))})
    return np.asarray(normals), np.asarray(times), details, jumps


def solve_with_offset(normals, frame_times, imu, *, max_offset, step, convention):
    best = None
    offsets = np.arange(-max_offset, max_offset + step * 0.5, step)
    for offset in offsets:
        rotations = plane_extrinsic.interpolate_rotations(
            imu["unix"], imu["quaternions"], frame_times - offset)
        if convention == "inverse":
            rotations = np.transpose(rotations, (0, 2, 1))
        try:
            result = plane_extrinsic.solve_rotation(normals, rotations)
        except ValueError:
            continue
        score = result["residual"]["rms_deg"]
        if best is None or score < best[0]:
            best = (score, float(offset), result)
    return best


def calibrate(pcap, imu_frames, *, start_frame=0, end_frame=None, frame_step=5,
              min_range=1.0, max_range=30.0, roi=None, threshold_m=0.05,
              iterations=150, min_inlier_ratio=0.15, max_points=8000,
              max_offset=0.2, step=0.01):
    imu = load_imu_frames(imu_frames)
    if imu.get("quaternions") is None:
        raise ValueError("IMU frames need quaternion output")
    scan = decoder.scan_pcap(pcap)
    if end_frame is None:
        end_frame = len(scan.frames)
    normals, times, details, jumps = collect_planes(
        pcap, start_frame=start_frame, end_frame=end_frame, frame_step=frame_step,
        min_range=min_range, max_range=max_range, roi=roi, threshold_m=threshold_m,
        iterations=iterations, min_inlier_ratio=min_inlier_ratio, max_points=max_points)
    if len(normals) < 6:
        return {"schema": "na70-plane-extrinsic-run-v1", "status": "INSUFFICIENT_DATA",
                "reason": f"only {len(normals)} usable planes",
                "planes": details, "plane_jumps": jumps}
    if times.min() < imu["unix"][0] - 0.1 or times.max() > imu["unix"][-1] + 0.1:
        return {"schema": "na70-plane-extrinsic-run-v1", "status": "INSUFFICIENT_DATA",
                "reason": "IMU does not cover the scan span", "planes": details}
    best = None
    for convention in ("body_to_world", "inverse"):
        candidate = solve_with_offset(normals, times, imu, max_offset=max_offset,
                                      step=step, convention=convention)
        if candidate is not None and (best is None or candidate[0] < best[0][0]):
            best = (candidate, convention)
    if best is None:
        return {"schema": "na70-plane-extrinsic-run-v1", "status": "INVALID_DATA",
                "reason": "no offset/convention produced a solution"}
    (score, offset, solution), convention = best
    scatter = normals.T @ normals / len(normals)
    eigenvalues = np.sort(np.linalg.eigvalsh(scatter))[::-1]
    coverage = float(eigenvalues[-1] / max(eigenvalues[0], 1e-12))
    return {
        "schema": "na70-plane-extrinsic-run-v1",
        "status": solution["status"],
        "source": {"pcap": str(pcap), "pcap_sha256": sha256(pcap),
                   "imu_frames": str(imu_frames),
                   "imu_frames_sha256": sha256(imu_frames)},
        "selection": {"start_frame": start_frame, "end_frame": end_frame,
                      "frame_step": frame_step, "planes": len(normals),
                      "plane_jumps": jumps, "roi": roi,
                      "normal_scatter_eigenvalues": eigenvalues.tolist(),
                      "axis_coverage": coverage},
        "convention": convention,
        "time_offset_s": offset,
        "time_offset_resolution_s": step,
        "solution": solution,
        "planes": details,
        "translation": {"value": None,
                        "note": "t_IL 仍需光学原点到 IMU 的机械测量；本工具不填零"},
        "limitations": list(plane_extrinsic.LIMITATIONS),
        "claims": {"rotation_candidate_only": True, "translation_solved": False,
                   "holdout_validated": False, "time_sync_validated": False},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcap", required=True, type=Path)
    parser.add_argument("--imu-frames", required=True, type=Path)
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--end-frame", type=int)
    parser.add_argument("--frame-step", type=int, default=5)
    parser.add_argument("--min-range", type=float, default=1.0)
    parser.add_argument("--max-range", type=float, default=30.0)
    parser.add_argument("--roi")
    parser.add_argument("--ransac-threshold", type=float, default=0.05)
    parser.add_argument("--ransac-iterations", type=int, default=150)
    parser.add_argument("--min-inlier-ratio", type=float, default=0.15)
    parser.add_argument("--max-points", type=int, default=8000)
    parser.add_argument("--max-offset", type=float, default=0.2)
    parser.add_argument("--step", type=float, default=0.01)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = calibrate(args.pcap, args.imu_frames, start_frame=args.start_frame,
                           end_frame=args.end_frame, frame_step=args.frame_step,
                           min_range=args.min_range, max_range=args.max_range,
                           roi=parse_roi(args.roi), threshold_m=args.ransac_threshold,
                           iterations=args.ransac_iterations,
                           min_inlier_ratio=args.min_inlier_ratio,
                           max_points=args.max_points, max_offset=args.max_offset,
                           step=args.step)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = {"schema": "na70-plane-extrinsic-run-v1", "status": "INVALID_DATA",
                  "error": str(exc)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with args.output.open("x", encoding="utf-8") as destination:
        destination.write(encoded)
    print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if result["status"] == "SOLVED_CANDIDATE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
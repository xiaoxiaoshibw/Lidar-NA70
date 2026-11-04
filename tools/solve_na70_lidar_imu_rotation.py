"""Estimate R_IL and LiDAR-IMU time offset from paired angular rates.

Input is an auditable ``.npz`` bundle (``allow_pickle=False``):

    t_lidar (N), omega_lidar (N,3) [rad/s]
    t_imu   (M), omega_imu   (M,3) [rad/s]

The LiDAR series must come from a traceable registration or truth source; this
tool neither derives it from raw scans nor fabricates a translation.  A weak
single-axis motion is reported as ``INSUFFICIENT_MOTION`` rather than solved.
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
from na70_core.calibration import rotation  # noqa: E402

KEYS = ("t_lidar", "omega_lidar", "t_imu", "omega_imu")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_bundle(path):
    with np.load(path, allow_pickle=False) as archive:
        missing = [key for key in KEYS if key not in archive]
        if missing:
            raise ValueError(f"bundle missing {','.join(missing)}")
        return {key: np.array(archive[key], dtype=np.float64, copy=True) for key in KEYS}


def solve(path, *, max_offset_s=0.5, coarse_step_s=0.002, fine_step_s=0.0001,
          max_relative_residual=0.35):
    arrays = load_bundle(path)
    result = rotation.solve_lidar_imu_rotation(
        arrays["t_lidar"], arrays["omega_lidar"], arrays["t_imu"], arrays["omega_imu"],
        max_offset_s=max_offset_s, coarse_step_s=coarse_step_s, fine_step_s=fine_step_s,
        max_relative_residual=max_relative_residual)
    result["input"] = {
        "path": str(path),
        "sha256": sha256(path),
        "lidar_samples": int(len(arrays["t_lidar"])),
        "imu_samples": int(len(arrays["t_imu"])),
        "lidar_span_s": float(arrays["t_lidar"][-1] - arrays["t_lidar"][0]),
        "imu_span_s": float(arrays["t_imu"][-1] - arrays["t_imu"][0]),
        "units": "rad/s (declared by bundle producer; not re-derived here)",
    }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-offset", type=float, default=0.5)
    parser.add_argument("--coarse-step", type=float, default=0.002)
    parser.add_argument("--fine-step", type=float, default=0.0001)
    parser.add_argument("--max-relative-residual", type=float, default=0.35)
    args = parser.parse_args(argv)
    try:
        result = solve(args.pairs, max_offset_s=args.max_offset, coarse_step_s=args.coarse_step,
                       fine_step_s=args.fine_step, max_relative_residual=args.max_relative_residual)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = {"schema": "na70-lidar-imu-rotation-v1", "status": "INVALID_DATA",
                  "error": str(exc)}
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        destination.write(encoded)
    print(json.dumps({"status": result.get("status"), "output": str(args.output)}, ensure_ascii=False))
    return 0 if result.get("status") == "SOLVED_CANDIDATE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
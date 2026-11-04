"""Compose a calibration candidate from a solved rotation and a measured lever arm.

Rotation/time offset come from ``solve_na70_lidar_imu_rotation.py``.  The
translation of the LiDAR optical origin in the IMU frame must come from a
physical measurement; this tool never invents it.  Output is a *candidate*,
not a frozen calibration.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "na70_core"))
from na70_core.calibration.extrinsic import compose_candidate  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rotation", required=True, type=Path)
    parser.add_argument("--translation-mm", help="x,y,z in millimetres, LiDAR origin in IMU frame")
    parser.add_argument("--translation-m", help="x,y,z in metres, alternative to --translation-mm")
    parser.add_argument("--translation-uncertainty-mm", type=float)
    parser.add_argument("--source-note")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        rotation_report = json.loads(args.rotation.read_text(encoding="utf-8"))
        if bool(args.translation_mm) == bool(args.translation_m):
            raise ValueError("supply exactly one of --translation-mm and --translation-m")
        if args.translation_mm:
            translation = np.array([float(value) for value in args.translation_mm.split(",")]) / 1000.0
        else:
            translation = np.array([float(value) for value in args.translation_m.split(",")])
        if translation.shape != (3,):
            raise ValueError("translation needs three comma-separated values")
        uncertainty = None if args.translation_uncertainty_mm is None else args.translation_uncertainty_mm / 1000.0
        result = compose_candidate(rotation_report, translation, translation_uncertainty_m=uncertainty,
                                   source_note=args.source_note, rotation_source=str(args.rotation))
    except (OSError, ValueError, TypeError) as exc:
        result = {"schema": "na70-calibration-candidate-v1", "status": "INVALID_DATA",
                  "error": str(exc)}
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        destination.write(encoded)
    print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if result["status"] == "CANDIDATE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
"""Fit a range scale/offset model from multiple wall-measurement JSONs.

Each input is the output of ``analyze_na70_lidar_planes.py`` (or a compatible
object) carrying a reference distance and a measured normal distance.  Fewer
than three distances are reported as ``NEEDS_MORE_DATA``; the intercept still
mixes the unknown optical origin and must not be called a range bias.
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
from na70_core.calibration import planes  # noqa: E402


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _extract(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") == "na70-lidar-plane-run-v1":
        plane = data.get("plane") or {}
        reference = plane.get("reference") or {}
        reference_m = reference.get("reference_distance_m")
        measured_m = plane.get("normal_distance_m")
        residual = plane.get("residual_rms_m")
        normal = plane.get("normal_unit")
    else:
        reference_m = data.get("reference_distance_m")
        measured_m = data.get("measured_distance_m", data.get("normal_distance_m"))
        residual = data.get("residual_rms_m")
        normal = data.get("normal_unit")
    if not isinstance(reference_m, (int, float)) or not isinstance(measured_m, (int, float)):
        raise ValueError(f"{path}: missing numeric reference/measured distance")
    return {
        "path": str(path),
        "sha256": sha256(path),
        "reference_distance_m": float(reference_m),
        "measured_distance_m": float(measured_m),
        "plane_residual_rms_m": None if residual is None else float(residual),
        "normal_unit": normal,
    }


def _normal_consistency(inputs):
    normals = []
    for item in inputs:
        normal = item.get("normal_unit")
        if normal is None:
            continue
        array = np.asarray(normal, dtype=np.float64).reshape(-1)
        if array.shape != (3,) or not np.isfinite(array).all():
            raise ValueError(f"{item['path']}: normal_unit must be three finite numbers")
        norm = np.linalg.norm(array)
        if norm <= 0:
            raise ValueError(f"{item['path']}: normal_unit must be non-zero")
        normals.append(array / norm)
    if len(normals) < 2:
        return None
    angles = []
    for first in range(len(normals)):
        for second in range(first + 1, len(normals)):
            dot = float(np.clip(np.dot(normals[first], normals[second]), -1.0, 1.0))
            angle = float(np.degrees(np.arccos(dot)))
            angles.append(min(angle, 180.0 - angle))
    return {"pairs": len(angles), "max_deg": max(angles), "mean_deg": float(np.mean(angles)),
            "note": "仅同一靶面的角域一致性；无独立角度基准时不宣称绝对角度校准"}


def fit(paths, *, min_points=3):
    inputs = sorted((_extract(path) for path in paths),
                    key=lambda item: item["reference_distance_m"])
    result = planes.multi_range_fit(
        [item["reference_distance_m"] for item in inputs],
        [item["measured_distance_m"] for item in inputs], min_points=min_points)
    return {
        "schema": "na70-range-model-v1",
        "status": result["status"],
        "inputs": inputs,
        "fit": result,
        "angular_consistency": _normal_consistency(inputs),
        "notes": ["截距混合光学原点与测距零偏；原点未测时不能单独解释为测距零偏",
                  "至少 3 个独立距离才给出斜率/截距；本结果不等于内部参数重标定",
                  "角域一致性只用于同一靶面的重复性检查，不是绝对角度校准"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--planes", nargs="+", required=True, type=Path)
    parser.add_argument("--min-points", type=int, default=3)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = fit(args.planes, min_points=args.min_points)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = {"schema": "na70-range-model-v1", "status": "INVALID_DATA", "error": str(exc)}
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        destination.write(encoded)
    print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if result["status"] == "FIT" else (1 if result["status"] == "NEEDS_MORE_DATA" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
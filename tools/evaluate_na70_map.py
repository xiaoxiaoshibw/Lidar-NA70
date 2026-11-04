"""Evaluate a NA70 map against a reference map, planes, or control points.

The map must already be in the same frame as the reference; this tool does not
register or rescale.  It reports consistency/residuals, not absolute accuracy.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "na70_core"))
from na70_core import map_quality  # noqa: E402


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def map_points(path):
    data = map_quality.load_ply(path)
    return np.column_stack([data["x"], data["y"], data["z"]]).astype(np.float64)


def read_planes(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    planes = data.get("planes") if isinstance(data, dict) else data
    if not isinstance(planes, list) or not planes:
        raise ValueError("planes JSON must be a non-empty list or {'planes': [...]}")
    return planes


def read_control_points(path):
    rows = []
    with Path(path).open(newline="", encoding="utf-8-sig") as source:
        reader = csv.reader(source)
        for index, row in enumerate(reader):
            if not row or row[0].strip().startswith("#"):
                continue
            try:
                values = [float(value) for value in row[:3]]
            except ValueError:
                if index == 0:
                    continue  # header
                raise ValueError(f"invalid control point at row {index}")
            if len(values) != 3:
                raise ValueError(f"control point row {index} needs three values")
            rows.append(values)
    if not rows:
        raise ValueError("empty control point file")
    return np.asarray(rows, dtype=np.float64)


def evaluate(map_path, *, reference=None, planes=None, control_points=None, threshold_m=0.1):
    if reference is None and planes is None and control_points is None:
        raise ValueError("at least one reference source is required")
    points = map_points(map_path)
    report = {
        "schema": "na70-map-evaluation-v1",
        "status": "EVALUATED",
        "map": {"path": str(map_path), "sha256": sha256(map_path), "points": int(len(points))},
        "results": {},
    }
    if reference is not None:
        report["results"]["map_consistency"] = map_quality.map_consistency(
            points, map_points(reference), threshold_m=threshold_m)
        report["results"]["map_consistency"]["reference"] = {
            "path": str(reference), "sha256": sha256(reference)}
    if planes is not None:
        report["results"]["planes"] = map_quality.plane_evaluation(
            points, read_planes(planes), threshold_m=threshold_m)
    if control_points is not None:
        report["results"]["control_points"] = map_quality.control_point_evaluation(
            points, read_control_points(control_points), threshold_m=threshold_m)
    report["claims"] = {"absolute_accuracy_validated": False, "registration_applied": False}
    report["limits"] = list(map_quality.LIMITATIONS)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", required=True, type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--planes", type=Path)
    parser.add_argument("--control-points", type=Path)
    parser.add_argument("--threshold-m", type=float, default=0.1)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = evaluate(args.map, reference=args.reference, planes=args.planes,
                          control_points=args.control_points, threshold_m=args.threshold_m)
    except (OSError, ValueError, TypeError) as exc:
        report = {"schema": "na70-map-evaluation-v1", "status": "INVALID_DATA",
                  "error": str(exc)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with args.output.open("x", encoding="utf-8") as destination:
        destination.write(encoded)
    print(json.dumps({"status": report["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if report["status"] == "EVALUATED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
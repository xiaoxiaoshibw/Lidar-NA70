"""Solve accelerometer bias/scale from six declared static faces.

The operator records one window per face; this tool only computes.  It refuses
to solve when a window moves or a face is missing, and never writes a
calibration.yaml.  A ``SOLVED_CANDIDATE`` result still needs hold-out checks.
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
from na70_core.calibration import six_face  # noqa: E402
from na70_core.calibration.frames import load_imu_frames  # noqa: E402
from na70_core.calibration.imu import static_windows  # noqa: E402

DEFAULT_CONFIG = ROOT / "tools/lio/static_check.yaml"
FACE_DIRECTIONS = {
    "+x": (1, 0, 0), "-x": (-1, 0, 0),
    "+y": (0, 1, 0), "-y": (0, -1, 0),
    "+z": (0, 0, 1), "-z": (0, 0, -1),
}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def normalise_face(name):
    if not isinstance(name, str):
        raise ValueError("face must be a string")
    text = name.strip().lower().replace(" ", "")
    if text in FACE_DIRECTIONS:
        return text
    if len(text) == 2 and text[1] in "+-" and text[0] in "xyz":
        swapped = text[1] + text[0]
        if swapped in FACE_DIRECTIONS:
            return swapped
    raise ValueError(f"unknown face {name!r}; use +x/-x/+y/-y/+z/-z")


def evaluate(path, spec, config, *, min_samples=None):
    windows = spec.get("windows")
    if not isinstance(windows, list) or not windows:
        raise ValueError("windows must be a non-empty list")
    min_samples = int(config["min_samples"] if min_samples is None else min_samples)
    faces = []
    seen = set()
    for index, window in enumerate(windows):
        face = normalise_face(window.get("face"))
        if face in seen:
            raise ValueError(f"duplicate face {face}")
        seen.add(face)
        segment = window.get("segment")
        if segment is not None and (not isinstance(segment, int) or segment < 0):
            raise ValueError(f"window {index}: segment must be a non-negative integer")
        data = load_imu_frames(path, start_s=window.get("start_s"), end_s=window.get("end_s"),
                               segment=segment)
        span = float(data["times"][-1] - data["times"][0])
        if len(data["times"]) < min_samples or span < config["min_span_s"]:
            faces.append({"face": face, "status": "INSUFFICIENT_DATA",
                          "samples": int(len(data["times"])), "host_span_s": span})
            continue
        # A span-length window would put the final sample exactly on the next
        # boundary; nudge it so one declared window stays one window.
        check_config = {**config, "window_s": math.nextafter(span, math.inf)}
        rows = static_windows(data["times"], data["segments"], data["accel"], data["gyro"], check_config)
        window_result = rows[0] if len(rows) == 1 else None
        if window_result is None or window_result["status"] != "STATIC_LIKE":
            faces.append({"face": face, "status": "REJECTED",
                          "samples": int(len(data["times"])), "host_span_s": span,
                          "failed_checks": [] if window_result is None else window_result["failed_checks"],
                          "window": window_result})
            continue
        faces.append({
            "face": face,
            "status": "STATIC_LIKE",
            "samples": int(len(data["times"])),
            "host_span_s": span,
            "mean_accel_m_s2": data["accel"].mean(axis=0).tolist(),
            "std_accel_m_s2": data["accel"].std(axis=0, ddof=1).tolist(),
            "mean_gyro_deg_s": data["gyro"].mean(axis=0).tolist(),
            "std_gyro_deg_s": data["gyro"].std(axis=0, ddof=1).tolist(),
        })
    rejected = [row["face"] for row in faces if row["status"] == "REJECTED"]
    insufficient = [row["face"] for row in faces if row["status"] == "INSUFFICIENT_DATA"]
    report = {
        "schema": "na70-six-face-run-v1",
        "source": str(path),
        "source_sha256": sha256(path),
        "windows": faces,
        "static_config": config,
        "limitations": list(six_face.LIMITATIONS),
        "claims": {"calibration_validated": False, "hardware_sync_validated": False},
    }
    reasons = []
    if insufficient:
        reasons.append("window_insufficient:" + ",".join(insufficient))
    if rejected:
        reasons.append("window_not_static:" + ",".join(rejected))
    if reasons:
        report.update(status="INSUFFICIENT_DATA", reasons=reasons)
        return report
    accepted = [row for row in faces if row["status"] == "STATIC_LIKE"]
    means = np.array([row["mean_accel_m_s2"] for row in accepted])
    directions = np.array([FACE_DIRECTIONS[row["face"]] for row in accepted], dtype=np.float64)
    solved = six_face.solve(means, directions)
    report["solution"] = solved
    report["status"] = solved["status"]
    report["reasons"] = solved["reasons"]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", required=True, type=Path)
    parser.add_argument("--windows", required=True, type=Path,
                        help="JSON: {'windows':[{'face':'+z','segment':0,'start_s':0,'end_s':20}]}")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        spec = json.loads(args.windows.read_text(encoding="utf-8"))
        config = json.loads(args.config.read_text(encoding="utf-8"))
        report = evaluate(args.frames, spec, config)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        report = {"schema": "na70-six-face-run-v1", "status": "INVALID_DATA", "error": str(exc)}
    encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        destination.write(encoded)
    print(json.dumps({"status": report.get("status"), "output": str(args.output)}, ensure_ascii=False))
    return 0 if report.get("status") == "SOLVED_CANDIDATE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
"""Solve six-face accelerometer parameters from separate capture sessions.

The batch capture records one session per face (task ``CAL-FACE-+z`` etc.) and
writes a batch manifest.  This tool reads each session, checks it stayed
static-like, and solves the same ``a = M (g d) + b`` model as
``solve_na70_six_face.py``.
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


def evaluate(batch_path, config):
    batch = json.loads(Path(batch_path).read_text(encoding="utf-8"))
    faces = (batch.get("sessions") or {}).get("FACES")
    if not isinstance(faces, dict) or not faces:
        raise ValueError("batch manifest has no sessions.FACES")
    reports = []
    means = []
    directions = []
    for face, entry in sorted(faces.items()):
        if face not in FACE_DIRECTIONS:
            raise ValueError(f"unknown face {face}")
        session = Path(entry["local"])
        frames_path = session / "imu_frames.jsonl"
        data = load_imu_frames(frames_path)
        span = float(data["times"][-1] - data["times"][0])
        if span < config["min_span_s"]:
            reports.append({"face": face, "status": "INSUFFICIENT_DATA",
                            "host_span_s": span, "samples": len(data["times"])})
            continue
        check = {**config, "window_s": math.nextafter(span, math.inf)}
        windows = static_windows(data["times"], data["segments"], data["accel"], data["gyro"], check)
        window = windows[0] if len(windows) == 1 else None
        if window is None or window["status"] != "STATIC_LIKE":
            reports.append({"face": face, "status": "REJECTED",
                            "host_span_s": span, "samples": len(data["times"]),
                            "failed_checks": [] if window is None else window["failed_checks"]})
            continue
        mean = data["accel"].mean(axis=0)
        means.append(mean)
        directions.append(FACE_DIRECTIONS[face])
        reports.append({"face": face, "status": "STATIC_LIKE",
                        "host_span_s": span, "samples": len(data["times"]),
                        "mean_accel_m_s2": mean.tolist(),
                        "std_accel_m_s2": data["accel"].std(axis=0, ddof=1).tolist(),
                        "source_sha256": sha256(frames_path)})
    result = {
        "schema": "na70-six-face-sessions-v1",
        "source": str(batch_path),
        "source_sha256": sha256(batch_path),
        "windows": reports,
        "static_config": config,
        "limitations": list(six_face.LIMITATIONS),
        "claims": {"calibration_validated": False, "hardware_sync_validated": False},
    }
    rejected = [row["face"] for row in reports if row["status"] == "REJECTED"]
    insufficient = [row["face"] for row in reports if row["status"] == "INSUFFICIENT_DATA"]
    if rejected or insufficient:
        reasons = []
        if rejected:
            reasons.append("window_not_static:" + ",".join(rejected))
        if insufficient:
            reasons.append("window_insufficient:" + ",".join(insufficient))
        result.update(status="INSUFFICIENT_DATA", reasons=reasons)
        return result
    solved = six_face.solve(np.asarray(means), np.asarray(directions, dtype=np.float64))
    result["solution"] = solved
    result["status"] = solved["status"]
    result["reasons"] = solved["reasons"]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        result = evaluate(args.batch, config)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = {"schema": "na70-six-face-sessions-v1", "status": "INVALID_DATA",
                  "error": str(exc)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with args.output.open("x", encoding="utf-8") as destination:
        destination.write(encoded)
    print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if result["status"] == "SOLVED_CANDIDATE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
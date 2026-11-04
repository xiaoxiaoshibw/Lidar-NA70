"""Report observed IMU ranges and saturation candidates from saved frames.

Without a datasheet range this can only flag numeric full-scale values and
repeated extreme plateaus; it does not prove physical saturation.  Units are
the recorded ones (accel m/s^2, gyro deg/s inferred).
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
from na70_core.calibration import imu  # noqa: E402
from na70_core.calibration.frames import load_imu_frames  # noqa: E402


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def audit(path, *, gyro_range=None, accel_range=None):
    data = load_imu_frames(path)
    reports = []
    events = []
    for segment in sorted(set(int(value) for value in data["segments"])):
        mask = data["segments"] == segment
        report = imu.range_summary(data["times"][mask], data["accel"][mask], data["gyro"][mask],
                                   gyro_range=gyro_range, accel_range=accel_range)
        report["segment"] = segment
        for event in report.pop("events"):
            events.append({"segment": segment, **event})
        reports.append(report)
    status = "SATURATION_SUSPECTED" if events else "OK_NO_SATURATION"
    return {
        "schema": "na70-imu-range-run-v1",
        "status": status,
        "source": str(path),
        "source_sha256": sha256(path),
        "configured_ranges": {"gyro_dps": gyro_range, "accel_m_s2": accel_range},
        "segments": reports,
        "events": events[:100],
        "event_count": len(events),
        "limits": ["未提供厂家量程时只能报告数值满量程/重复极值，不能证明物理饱和",
                   "角速度单位仍为推断 deg/s；阈值是开发诊断参数",
                   "饱和不改变原始帧；本工具不裁剪、不替换数值"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", required=True, type=Path)
    parser.add_argument("--gyro-range-dps", type=float)
    parser.add_argument("--accel-range-m-s2", type=float)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = audit(args.frames, gyro_range=args.gyro_range_dps,
                       accel_range=args.accel_range_m_s2)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = {"schema": "na70-imu-range-run-v1", "status": "INVALID_DATA", "error": str(exc)}
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        destination.write(encoded)
    print(json.dumps({"status": result["status"], "events": result.get("event_count", 0),
                      "output": str(args.output)}, ensure_ascii=False))
    return 0 if result["status"] == "OK_NO_SATURATION" else (1 if result["status"] == "SATURATION_SUSPECTED" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
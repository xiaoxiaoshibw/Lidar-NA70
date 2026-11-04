"""Verify the recorded gyro unit against the device's own quaternion output.

A device-internal consistency check: it can confirm deg/s (or suggest rad/s) and
the body-frame convention, but it is not an external angular reference and does
not measure scale accuracy or bias.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "na70_core"))
from na70_core.calibration import gyro_unit  # noqa: E402
from na70_core.calibration.frames import load_imu_frames  # noqa: E402


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check(path, *, max_dt=0.2, min_rate_deg_s=0.05, stride=1):
    data = load_imu_frames(path)
    result = gyro_unit.verify(data, max_dt=max_dt, min_rate_deg_s=min_rate_deg_s, stride=stride)
    result["source"] = str(path)
    result["source_sha256"] = sha256(path)
    result["config"] = {"max_dt_s": max_dt, "min_rate_deg_s": min_rate_deg_s, "stride": stride}
    result["units"] = {"recorded_gyro": "unknown; tested against deg/s",
                       "quaternion": "(w,x,y,z) assumed"}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", required=True, type=Path)
    parser.add_argument("--max-dt", type=float, default=0.2)
    parser.add_argument("--min-rate-deg-s", type=float, default=0.05)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = check(args.frames, max_dt=args.max_dt, min_rate_deg_s=args.min_rate_deg_s,
                       stride=args.stride)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = {"schema": "na70-imu-gyro-unit-v1", "status": "INVALID_DATA", "error": str(exc)}
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        destination.write(encoded)
    print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if result["status"] == "DEG_S_CONFIRMED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
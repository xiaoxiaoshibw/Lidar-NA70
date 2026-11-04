"""Diagnostic static-consistency check on saved IMU frames, not calibration."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "na70_core"))
from na70_core.calibration.imu import overall_status, static_windows  # noqa: E402

DEFAULT_CONFIG = Path(__file__).resolve().parent / "lio/static_check.yaml"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check(path, config):
    path = Path(path)
    times = []
    segments = []
    accel = []
    gyro = []
    try:
        with path.open(encoding="utf-8") as f:
            for line_number, line in enumerate(f):
                row = json.loads(line)
                t, segment = row["t_mono"], row["segment"]
                a, g = row["accel"], row["gyro"]
                if not isinstance(segment, int) or segment < 0 or len(a) != 3 or len(g) != 3:
                    raise ValueError(f"invalid fields at row {line_number}")
                if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in [t, *a, *g]):
                    raise ValueError(f"nonfinite or invalid value at row {line_number}")
                times.append(t)
                segments.append(segment)
                accel.append(a)
                gyro.append(g)
        if not times:
            raise ValueError("empty IMU frame file")
        windows = static_windows(times, segments, accel, gyro, config)
        status = overall_status(windows, segments)
        return {"schema": "na70-imu-static-v1", "status": status, "sample_count": len(times),
                "segments": sorted(set(segments)), "windows": windows, "config": config,
                "source_sha256": sha256(path),
                "limits": ["仅静置一致性诊断，不能证明物理静止或校准成功", "阈值为开发阈值，非厂家指标",
                           "角速度单位仍为推断，时间为主机接收时间", "不验证流完整性、同步、外参或 Allan 噪声参数"]}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return {"schema": "na70-imu-static-v1", "status": "INVALID_DATA", "error": str(exc)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frames", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, help="保存结果，禁止覆盖已有文件")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    result = check(args.frames, config)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as destination:
            destination.write(encoded)
    else:
        print(encoded, end="")
    return 0 if result["status"] == "STATIC_LIKE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
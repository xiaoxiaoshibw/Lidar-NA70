"""Export a device-time IMU CSV only when every frame carries ``sample_us``.

The capture service always keeps ``imu_host_receive.csv``.  When a firmware or
configuration starts emitting the YESENSE 0x51 sample timestamp, this tool
converts the saved frames into the strict ``device_time_s,gx,gy,gz,ax,ay,az``
contract used by ``prepare_lio_session.py``.

It refuses to run when any frame lacks ``sample_us``, when a segment resets, or
when the timestamp is not strictly increasing after 32-bit unwrapping.  Gyro and
accelerometer values are copied in their recorded units; no unit conversion or
host-time fallback happens here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LIMITATIONS = (
    "device_time_s 是所选 segment 内首个 0x51 样本起的设备时间，不是主机时间",
    "设备时间语义（上电/绝对/RTC）和时钟漂移仍需厂家资料与实测确认",
    "导出只证明字段存在且单调，不证明与雷达同步",
    "陀螺单位沿用采集记录（推断 deg/s），未在此转换",
)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path):
    rows = []
    with Path(path).open(encoding="utf-8") as source:
        for line_number, line in enumerate(source):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at row {line_number}: {exc}") from exc
            rows.append(row)
    if not rows:
        raise ValueError("empty frames file")
    return rows


def _finite_vector(row, key, row_index):
    value = row.get(key)
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"row {row_index} missing {key}[3]")
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in value):
        raise ValueError(f"row {row_index} has non-finite {key}")
    return value


def export(frames_path, output_csv, *, segment=None):
    rows = _load(frames_path)
    segments = sorted({row.get("segment") for row in rows})
    if any(not isinstance(value, int) or value < 0 for value in segments):
        raise ValueError("every frame needs a non-negative integer segment")
    if segment is None:
        if len(segments) != 1:
            raise ValueError(f"multiple segments {segments}; export one with --segment")
        segment = segments[0]
    selected = [index for index, row in enumerate(rows) if row.get("segment") == segment]
    if not selected:
        raise ValueError(f"segment {segment} not present")
    missing = [index for index in selected if rows[index].get("sample_us") is None]
    if missing:
        raise ValueError(f"{len(missing)} frames in segment {segment} lack sample_us")

    unwrapped = []
    wraps = 0
    last_raw = None
    for index in selected:
        value = rows[index]["sample_us"]
        if not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
            raise ValueError(f"row {index} sample_us must be an unsigned 32-bit integer")
        if last_raw is not None:
            if value <= last_raw:
                if last_raw > 0.75 * 2 ** 32 and value < 0.25 * 2 ** 32:
                    wraps += 1
                else:
                    raise ValueError(
                        f"sample_us went backwards at row {index} ({last_raw} -> {value}); "
                        "split the recording instead of fabricating time")
        unwrapped.append(value + wraps * 2 ** 32)
        last_raw = value

    origin = unwrapped[0]
    times = [(value - origin) / 1e6 for value in unwrapped]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("device time is not strictly increasing after unwrapping")

    lines = ["device_time_s,gx,gy,gz,ax,ay,az"]
    for index, time in zip(selected, times):
        row = rows[index]
        gyro = _finite_vector(row, "gyro", index)
        accel = _finite_vector(row, "accel", index)
        lines.append(f"{time:.6f}," + ",".join(f"{value:.6f}" for value in gyro + accel))
    meta = {
        "schema": "na70-imu-device-csv-v1",
        "status": "EXPORTED",
        "source": {"frames": str(frames_path), "sha256": sha256(frames_path)},
        "segment": segment,
        "rows": len(selected),
        "first_sample_us": int(unwrapped[0] % (2 ** 32)),
        "last_sample_us": int(unwrapped[-1] % (2 ** 32)),
        "wrap_count": wraps,
        "duration_s": times[-1],
        "time_origin": "first 0x51 sample in the selected segment",
        "units": {"device_time_s": "s", "gyro": "deg/s (recorded, not converted)",
                  "accel": "m/s^2 (recorded, not converted)"},
        "limitations": list(LIMITATIONS),
        "claims": {"device_time_semantics_validated": False, "sync_validated": False},
    }
    output_csv = Path(output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("x", encoding="utf-8", newline="") as destination:
        destination.write("\n".join(lines) + "\n")
    meta_path = output_csv.with_suffix(".meta.json")
    with meta_path.open("x", encoding="utf-8") as destination:
        destination.write(json.dumps(meta, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return meta


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--segment", type=int)
    args = parser.parse_args(argv)
    try:
        result = export(args.frames, args.output, segment=args.segment)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "REFUSED", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": result["status"], "rows": result["rows"],
                      "duration_s": result["duration_s"], "output": str(args.output)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
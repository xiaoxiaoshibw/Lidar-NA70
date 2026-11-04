"""Audit the IMU time base from saved chunks and frames (no raw decode).

The current YESENSE stream has no device sample timestamp, and USB reads deliver
several frames per host instant.  This tool quantifies that structure:

* how many frames share one host receive instant (batch size);
* the interval between unique receive instants, and therefore a conservative
  bound on any frame's true sampling time inside its batch;
* whether ``sample_us`` / ``ready_us`` device-time fields are present;
* tid continuity, expected 60000->1 boundary, and missing-frame estimates.

It does **not** create a device clock, interpolate sample times, or prove
synchronization.  A host-time-only result is time level C from the SLAM plan.
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
from na70_core.capture.yesense import SAMPLE_TIME_GROUP, READY_TIME_GROUP, tid_follows  # noqa: E402


LIMITATIONS = (
    "主机接收时间不是设备采样时间；批内真实采样时刻未知",
    "批量间隔只给出保守上界，不是可用的时间同步误差",
    "tid 连续只说明序号未断，不证明没有丢帧或时钟正确",
    "没有 sample_us/ready_us 时不得生成 device_time_s",
)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_jsonl(path):
    rows = []
    with Path(path).open(encoding="utf-8") as source:
        for line_number, line in enumerate(source):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}: invalid JSON at row {line_number}: {exc}") from exc
            rows.append(row)
    if not rows:
        raise ValueError(f"{path.name} is empty")
    return rows


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


def _batch_stats(times):
    unique, counts = np.unique(np.asarray(times, dtype=np.float64), return_counts=True)
    intervals = np.diff(unique)
    positive = intervals[intervals > 0]
    return {
        "frames": int(len(times)),
        "unique_host_times": int(len(unique)),
        "frames_per_batch_min": int(counts.min()),
        "frames_per_batch_median": float(np.median(counts)),
        "frames_per_batch_max": int(counts.max()),
        "batch_interval_median_s": float(np.median(positive)) if len(positive) else 0.0,
        "batch_interval_p95_s": float(np.percentile(positive, 95)) if len(positive) else 0.0,
        "batch_interval_max_s": float(positive.max()) if len(positive) else 0.0,
        "host_span_s": float(unique[-1] - unique[0]),
    }


def _sequence(rows):
    events = []
    gap_events = 0
    missing = 0
    resets = 0
    by_segment = {}
    for row in rows:
        by_segment.setdefault(int(row["segment"]), []).append(int(row["tid"]))
    for segment, tids in sorted(by_segment.items()):
        for previous, current in zip(tids, tids[1:]):
            if tid_follows(previous, current):
                if previous == 60000 and current == 1:
                    resets += 1
                continue
            gap_events += 1
            expected_missing = (current - previous - 1) & 0xFFFF
            missing += expected_missing
            if len(events) < 20:
                events.append({"segment": segment, "previous": previous, "current": current,
                               "estimated_missing": expected_missing})
    return {"tid_gap_events": gap_events, "events": events,
            "estimated_missing_frames": missing, "device_boundary_60000_to_1": resets}


def audit(frames_path, chunks_path):
    frames = _load_jsonl(frames_path)
    chunks = _load_jsonl(chunks_path)
    frame_times = []
    segment_rows = []
    for index, row in enumerate(frames):
        for key in ("t_mono", "t_unix", "segment", "tid", "accel", "gyro"):
            if key not in row:
                raise ValueError(f"frame {index} missing {key}")
        t = _number(row["t_mono"], f"frame {index} t_mono")
        _number(row["t_unix"], f"frame {index} t_unix")
        if not isinstance(row["segment"], int) or row["segment"] < 0:
            raise ValueError(f"frame {index} segment must be a non-negative integer")
        if not isinstance(row["tid"], int) or not 0 <= row["tid"] <= 0xFFFF:
            raise ValueError(f"frame {index} tid must be 0..65535")
        frame_times.append(t)
        segment_rows.append(row["segment"])
    if np.any(np.diff(frame_times) < 0):
        raise ValueError("frame host time moved backwards")

    chunk_times = []
    for index, chunk in enumerate(chunks):
        for key in ("t_mono", "t_unix", "bytes", "segment"):
            if key not in chunk:
                raise ValueError(f"chunk {index} missing {key}")
        t = _number(chunk["t_mono"], f"chunk {index} t_mono")
        _number(chunk["t_unix"], f"chunk {index} t_unix")
        if not isinstance(chunk["bytes"], int) or chunk["bytes"] <= 0:
            raise ValueError(f"chunk {index} bytes must be a positive integer")
        if not isinstance(chunk["segment"], int) or chunk["segment"] < 0:
            raise ValueError(f"chunk {index} segment must be a non-negative integer")
        chunk_times.append(t)
    if np.any(np.diff(chunk_times) <= 0):
        raise ValueError("chunk host time must strictly increase")

    unique = np.unique(np.asarray(frame_times, dtype=np.float64))
    chunk_array = np.asarray(chunk_times, dtype=np.float64)
    unmatched = 0
    for t in unique:
        position = int(np.searchsorted(chunk_array, t))
        candidates = [position - 1, position]
        if not any(0 <= i < len(chunk_array) and abs(chunk_array[i] - t) <= 1e-9 for i in candidates):
            unmatched += 1
    if unmatched:
        raise ValueError(f"{unmatched} unique frame times do not match any chunk time")

    device_time_fields = {
        "sample_us_present": sum(1 for row in frames if row.get("sample_us") is not None),
        "ready_us_present": sum(1 for row in frames if row.get("ready_us") is not None),
        "sample_tag": hex(SAMPLE_TIME_GROUP),
        "ready_tag": hex(READY_TIME_GROUP),
    }
    device_time = (device_time_fields["sample_us_present"] > 0
                   or device_time_fields["ready_us_present"] > 0)
    batches = _batch_stats(frame_times)
    segments = {}
    for row, time in zip(frames, frame_times):
        entry = segments.setdefault(int(row["segment"]), {"frames": 0, "first_s": time, "last_s": time})
        entry["frames"] += 1
        entry["last_s"] = time
    for segment, entry in segments.items():
        subset = [t for t, s in zip(frame_times, segment_rows) if s == segment]
        entry.update(_batch_stats(subset))
        entry["host_span_s"] = float(entry["last_s"] - entry["first_s"])

    result = {
        "schema": "na70-imu-timebase-v1",
        "status": "OK_DEVICE_TIME_PRESENT" if device_time else "OK_HOST_TIME_ONLY",
        "time_level": "DECLARED_DEVICE_FIELD_PRESENT" if device_time else "C_HOST_RECEIVE_ONLY",
        "source": {"frames": str(frames_path), "frames_sha256": sha256(frames_path),
                   "chunks": str(chunks_path), "chunks_sha256": sha256(chunks_path)},
        "device_time": device_time_fields,
        "batches": batches,
        "segments": [{"segment": key, **value} for key, value in sorted(segments.items())],
        "sequence": _sequence(frames),
        "host_time_bound": {
            "conservative_s": batches["batch_interval_max_s"],
            "median_batch_interval_s": batches["batch_interval_median_s"],
            "meaning": "批内帧的真实采样时刻可能落在上一批到达至本批到达之间；这是上界，不是同步误差",
        },
        "checks": {
            "frame_time_monotonic": True,
            "chunk_time_monotonic": True,
            "all_frame_times_match_chunk": True,
            "device_time_absent": not device_time,
        },
        "limits": list(LIMITATIONS),
        "claims": {"time_sync_validated": False, "device_time_validated": False},
    }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", required=True, type=Path)
    parser.add_argument("--chunks", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = audit(args.frames, args.chunks)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = {"schema": "na70-imu-timebase-v1", "status": "INVALID_DATA", "error": str(exc)}
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        destination.write(encoded)
    print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if result["status"].startswith("OK_") else 2


if __name__ == "__main__":
    raise SystemExit(main())
"""Report host-time, gap, static-window and Allan statistics for one IMU run.

This is a diagnostic baseline for B1/B2.  It does not prove physical stillness,
does not convert host receive time into device sample time, and does not turn a
short recording into a bias-stability result.
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

DEFAULT_CONFIG = ROOT / "tools/lio/static_check.yaml"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def analyse(path, config, *, allan_segment=None, min_allan_s=60.0, start_s=None, end_s=None):
    if start_s is None and end_s is None:
        data = load_imu_frames(path)
    else:
        data = load_imu_frames(path, start_s=start_s, end_s=end_s)
    times = data["times"]
    segments = data["segments"]
    windows = imu.static_windows(times, segments, data["accel"], data["gyro"], config)
    status = imu.overall_status(windows, segments)
    summary = []
    for segment in sorted(set(int(value) for value in segments)):
        mask = segments == segment
        segment_times = times[mask]
        steps = np.diff(segment_times)
        # Multiple frames can share one host receive instant (USB batch).  A raw
        # median would be 0 and every arrival gap would look anomalous, so rate
        # and gaps are computed on positive intervals and duplicates are counted.
        positive = steps[steps > 0]
        median = float(np.median(positive)) if len(positive) else 0.0
        spans = segment_times[-1] - segment_times[0]
        summary.append({
            "segment": segment,
            "samples": int(mask.sum()),
            "host_span_s": float(spans),
            "median_positive_dt_s": median,
            "effective_arrival_hz": float(1.0 / median) if median > 0 else None,
            "duplicate_host_times": int((steps == 0).sum()),
            "max_gap_s": float(positive.max()) if len(positive) else 0.0,
            "gaps_over_1_5_median": int((positive > 1.5 * median).sum()) if median > 0 else 0,
        })
    result = {
        "schema": "na70-imu-run-v1",
        "status": status,
        "source": str(path),
        "source_sha256": sha256(path),
        "config": config,
        "sample_count": int(len(times)),
        "segments": summary,
        "windows": windows,
        "allan": None,
        "limits": ["仅诊断；不证明物理静止、同步、校准或硬件健康",
                   "Allan 仅为候选分析；短录或运动段不出偏置稳定度",
                   "时间为设备/主机口径由采集清单声明，本工具不推断",
                   "同一主机时刻可批量到达多帧；duplicate_host_times 不是丢帧证明"],
    }
    target = allan_segment
    if target is None and len(summary) == 1:
        target = summary[0]["segment"]
    if target is not None:
        segment_windows = [window for window in windows if window["segment"] == target]
        static = bool(segment_windows) and all(window["status"] == "STATIC_LIKE"
                                               for window in segment_windows)
        mask = segments == target
        span = float(times[mask][-1] - times[mask][0]) if mask.any() else 0.0
        if not static:
            result["allan"] = {"status": "NOT_EVALUATED", "reason": "segment_windows_not_static_like"}
        elif np.any(np.diff(times[mask]) <= 0):
            if span < min_allan_s:
                result["allan"] = {"status": "NOT_EVALUATED", "reason": "record_too_short",
                                   "host_span_s": span, "required_s": min_allan_s}
            else:
                result["allan"] = imu.host_batch_allan(
                    times[mask], np.column_stack((data["gyro"][mask], data["accel"][mask])),
                    axis_names=["gx", "gy", "gz", "ax", "ay", "az"])
        elif span < min_allan_s:
            result["allan"] = {"status": "NOT_EVALUATED", "reason": "record_too_short",
                               "host_span_s": span, "required_s": min_allan_s}
        else:
            try:
                result["allan"] = imu.allan_deviation(times[mask], np.column_stack(
                    (data["gyro"][mask], data["accel"][mask])),
                    axis_names=["gx", "gy", "gz", "ax", "ay", "az"])
            except ValueError as exc:
                result["allan"] = {"status": "INVALID", "reason": str(exc)}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frames", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--allan-segment", type=int)
    parser.add_argument("--min-allan-s", type=float, default=60.0)
    parser.add_argument("--start-s", type=float, help="relative to the first accepted sample")
    parser.add_argument("--end-s", type=float)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    result = analyse(args.frames, config, allan_segment=args.allan_segment,
                     min_allan_s=args.min_allan_s, start_s=args.start_s, end_s=args.end_s)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        destination.write(encoded)
    print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if result["status"] == "STATIC_LIKE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
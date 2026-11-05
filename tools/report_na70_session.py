"""One-command diagnostic report for a captured NA70 + IMU session.

Runs the time-base audit, range/saturation audit, static-window analysis and
(if a PCAP is present) host-time overlap.  It reports facts and a conservative
verdict; it never promotes host time to device time or calls a moving session a
static baseline.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "tools/lio/static_check.yaml"
sys.path.insert(0, str(ROOT / "packages" / "na70_core"))
from na70_core.calibration import gyro_unit  # noqa: E402
from na70_core.calibration.frames import load_imu_frames  # noqa: E402


def sha256(path):
    digest = hashlib.sha256()
    if not Path(path).is_file():
        return None
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_tool(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find(session, *candidates):
    for relative in candidates:
        path = Path(session) / relative
        if path.is_file():
            return path
    return None


def report(session, *, config_path=DEFAULT_CONFIG, skip_overlap=False):
    session = Path(session)
    if not session.is_dir():
        raise ValueError(f"session directory not found: {session}")
    frames = find(session, "imu_frames.jsonl")
    chunks = find(session, "imu_chunks.jsonl", "raw/imu_chunks.jsonl")
    pcap = find(session, "lidar.pcap", "raw/lidar.pcap")
    if frames is None or chunks is None:
        raise ValueError("session needs imu_frames.jsonl and imu_chunks.jsonl")

    timebase = load_tool("timebase_audit", "audit_na70_imu_timebase.py")
    ranges = load_tool("range_audit", "audit_na70_imu_ranges.py")
    run_tool = load_tool("imu_run", "analyze_na70_imu_run.py")

    timebase_result = timebase.audit(frames, chunks)
    ranges_result = ranges.audit(frames)
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    run_result = run_tool.analyse(frames, config)
    try:
        gyro_result = gyro_unit.verify(load_imu_frames(frames))
    except (ValueError, TypeError) as exc:
        gyro_result = {"status": "NOT_EVALUATED", "reason": str(exc)}

    overlap_result = None
    if pcap is not None and not skip_overlap:
        overlap_tool = load_tool("overlap_audit", "audit_na70_overlap.py")
        overlap_result = overlap_tool.audit(pcap, chunks)
    elif pcap is None:
        overlap_result = {"status": "SKIPPED_NO_PCAP"}

    invalid = (timebase_result.get("status") == "INVALID_DATA"
               or ranges_result.get("status") == "INVALID_DATA"
               or run_result.get("status") == "INVALID_DATA")
    static_like = run_result.get("status") == "STATIC_LIKE"
    saturation = ranges_result.get("status") == "SATURATION_SUSPECTED"
    device_time = timebase_result.get("status") == "OK_DEVICE_TIME_PRESENT"
    if invalid:
        verdict = "INVALID"
    elif static_like:
        verdict = "STATIC_LIKE_READY"
    else:
        verdict = "REJECTED_NOT_STATIC"

    result = {
        "schema": "na70-session-report-v1",
        "status": verdict,
        "session": str(session),
        "files": {
            "frames": str(frames), "frames_sha256": sha256(frames),
            "chunks": str(chunks), "chunks_sha256": sha256(chunks),
            "pcap": None if pcap is None else str(pcap),
            "pcap_sha256": None if pcap is None else sha256(pcap),
        },
        "time_level": timebase_result.get("time_level"),
        "device_time_present": device_time,
        "saturation_suspected": saturation,
        "static_baseline_usable": static_like and not invalid,
        "checks": {
            "timebase": timebase_result.get("status"),
            "ranges": ranges_result.get("status"),
            "gyro_unit": gyro_result.get("status"),
            "static": run_result.get("status"),
            "overlap": None if overlap_result is None else overlap_result.get("status"),
        },
        "summary": {
            "frames": run_result.get("sample_count"),
            "batches": (timebase_result.get("batches") or {}).get("unique_host_times"),
            "frames_per_batch_median": (timebase_result.get("batches") or {}).get("frames_per_batch_median"),
            "batch_interval_max_s": (timebase_result.get("batches") or {}).get("batch_interval_max_s"),
            "host_time_bound_s": (timebase_result.get("host_time_bound") or {}).get("conservative_s"),
            "host_time_bound_note": (timebase_result.get("host_time_bound") or {}).get("meaning"),
            "segments": run_result.get("segments"),
            "static_windows": [(window.get("window"), window.get("status"))
                               for window in run_result.get("windows", [])],
            "allan": run_result.get("allan"),
            "range_events": ranges_result.get("event_count"),
            "gyro_unit": {"status": gyro_result.get("status"),
                          "scale": gyro_result.get("scale_deg_per_recorded"),
                          "cosine": gyro_result.get("cosine"),
                          "reason": gyro_result.get("reason")},
            "overlap": overlap_result,
        },
        "verdicts": {
            "may_be_used_as_static_baseline": static_like and not invalid,
            "may_be_used_for_dynamic_calibration": False if not device_time else "UNVERIFIED_DEVICE_TIME",
            "reason": ("无设备采样时间；主机批量到达限制对齐精度"
                       if not device_time else "有设备时间字段，但语义未验证"),
        },
        "limits": ["本报告只汇总诊断，不代替原始字节审计、标定或同步验证",
                   "静态窗口通过不等于物理静止；量程未知时无饱和只是无证据",
                   "主机接收时间不能升级为设备采样时间"],
    }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--skip-overlap", action="store_true")
    args = parser.parse_args(argv)
    if (args.output / "report.json").exists():
        print(json.dumps({"status": "REFUSED", "error": "report.json already exists; keep history"},
                         ensure_ascii=False))
        return 2
    try:
        result = report(args.session, config_path=args.config, skip_overlap=args.skip_overlap)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = {"schema": "na70-session-report-v1", "status": "INVALID_DATA", "error": str(exc)}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    lines = ["# NA70 会话诊断报告", "",
             f"- 结论：**{result['status']}**",
             f"- 时间等级：`{result.get('time_level')}`，设备时间字段：{result.get('device_time_present')}",
             f"- 饱和可疑：{result.get('saturation_suspected')}，"
             f"静置基线可用：{result.get('static_baseline_usable')}"]
    summary = result.get("summary") or {}
    if summary.get("frames") is not None:
        lines += ["", f"- 帧数 {summary['frames']}，批次 {summary.get('batches')}，"
                      f"每批中位 {summary.get('frames_per_batch_median')}，"
                      f"批次最大间隔 {summary.get('batch_interval_max_s')} s",
                  f"- 静置窗口：{summary.get('static_windows')}",
                  f"- 饱和事件：{summary.get('range_events')}",
                  f"- 陀螺单位一致性：{(summary.get('gyro_unit') or {}).get('status')}"]
    overlap = summary.get("overlap") or {}
    if overlap.get("status") == "observed":
        lines += ["", "## 主机端点重叠",
                  f"- PCAP {overlap['lidar_packets']} 包 / IMU {overlap['imu_chunks']} 块，"
                  f"端点重叠 {overlap['host_overlap_s']:.6f} s",
                  f"- 最大雷达包间隔 {overlap['max_lidar_capture_gap_s']:.6f} s，"
                  f"最大 IMU 块间隔 {overlap['max_imu_chunk_gap_s']:.6f} s",
                  "- 主机端点重叠不证明设备采样同步或全程连续有效"]
    reasons = result.get("verdicts") or {}
    lines += ["", "## 用途判定",
              f"- 静态基线：{result.get('static_baseline_usable')}",
              f"- 动态标定：{reasons.get('may_be_used_for_dynamic_calibration')}；"
              f"原因：{reasons.get('reason')}"]
    lines += ["", "本报告只汇总诊断；主机时间不是设备时间，窗口通过不等于物理静止。"]
    (args.output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if result["status"] in ("STATIC_LIKE_READY", "REJECTED_NOT_STATIC") else 2


if __name__ == "__main__":
    raise SystemExit(main())

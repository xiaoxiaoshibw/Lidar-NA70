"""Associate NA70 packets with IMU host-arrival batches; never infer sample times.

Only backend.decoder reads PCAP. Source order and 1-based JSONL line ranges
are retained, including multiple IMU frames sharing one host receive time.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter
import csv
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "packages/na70_core", ROOT / "web"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from backend.decoder import DECODER_VERSION, scan_pcap  # noqa: E402
from na70_core.capture.session import sha256_file  # noqa: E402
from na70_core.capture.yesense import tid_follows  # noqa: E402

DEFAULT_CONFIG = Path(__file__).with_name("na70_qt_capture.yaml")


def load_batches(path):
    batches = []
    previous = None
    count = 0
    with Path(path).open(encoding="utf-8") as source:
        for line_no, line in enumerate(source, 1):
            row = json.loads(line)
            timestamp = row["t_unix"]
            if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
                    or not math.isfinite(timestamp)):
                raise ValueError(f"IMU 第 {line_no} 行主机时间无效")
            segment, tid = row["segment"], row["tid"]
            if (type(segment) is not int or segment < 0 or type(tid) is not int
                    or not 0 <= tid <= 65535):
                raise ValueError(f"IMU 第 {line_no} 行 segment/tid 无效")
            if previous is not None:
                if timestamp < previous["t_unix"]:
                    raise ValueError(f"IMU 第 {line_no} 行主机时间倒退，拒绝自动配对")
                if timestamp == previous["t_unix"] and segment != previous["segment"]:
                    raise ValueError("不同 IMU segment 共用主机时间，关系不明确")
            follows = (previous is None or
                       (segment == previous["segment"] and tid_follows(previous["tid"], tid)))
            if not batches or timestamp != batches[-1]["host_unix_s"]:
                batches.append({"batch_index": len(batches), "host_unix_s": timestamp,
                                "segment": segment, "line_first": line_no, "line_last": line_no,
                                "tid_first": tid, "tid_last": tid, "frames": 0,
                                "sequence_ok": True, "follows_previous": follows})
            elif not follows:
                batches[-1]["sequence_ok"] = False
            batch = batches[-1]
            batch.update(line_last=line_no, tid_last=tid, frames=batch["frames"] + 1)
            previous = row
            count += 1
    if not batches:
        raise ValueError("IMU 没有可关联的帧")
    return batches, count


def bracket(timestamp, batches, times, max_gap_s):
    right = bisect_left(times, timestamp)
    if right < len(times) and times[right] == timestamp:
        left = right
    else:
        left = right - 1
    before = batches[left] if left >= 0 else None
    after = batches[right] if right < len(batches) else None
    if before is None or after is None:
        status = "outside_imu_range"
    elif before["segment"] != after["segment"]:
        status = "segment_break"
    elif (not before["sequence_ok"] or not after["sequence_ok"]
          or (left != right and not after["follows_previous"])):
        status = "sequence_break"
    elif after["host_unix_s"] - before["host_unix_s"] > max_gap_s:
        status = "host_gap"
    else:
        status = "host_bracketed"
    return before, after, status


def associate(session, *, output=None, max_gap_s=None, progress=lambda _: None):
    """Write a separate, exclusive derived directory; never alter raw manifests."""
    if max_gap_s is None:
        max_gap_s = json.loads(DEFAULT_CONFIG.read_text("utf-8"))["max_host_batch_gap_s"]
    if isinstance(max_gap_s, bool) or not math.isfinite(max_gap_s) or max_gap_s <= 0:
        raise ValueError("主机批次间隔门限必须为有限正数")
    session = Path(session).resolve()
    pcap = session / "raw/lidar.pcap"
    if not pcap.is_file():
        pcap = session / "lidar.pcap"
    imu = session / "imu_frames.jsonl"
    # Derived files live outside the original session inventory.
    output = Path(output) if output else session.parent / "associations" / (
        session.name + "-" + time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1000000:06d}")
    output = output.resolve()
    if output == session or session in output.parents:
        raise ValueError("关联输出须放在原始会话目录以外，保持原始清单不变")
    output.mkdir(parents=True, exist_ok=False)
    progress(f"关联结果目录：{output}")
    report = {"schema": "na70-host-association-v1", "status": "failed",
              "session": str(session), "output": str(output),
              "time_basis": "HOST_RECEIVE_ONLY", "max_host_batch_gap_s": max_gap_s,
              "claims": {"time_sync_validated": False, "extrinsics_validated": False,
                         "sampling_error_bound_s": None, "R_IL": None, "t_IL_m": None},
              "limits": ["主机到达时间仅用于粗关联，不是传感器采样时间。",
                         "同批 IMU 帧共享时间，保留整批行号，不插值或假造逐帧时间。",
                         "间隔门限用于发现主机数据空洞，不是同步误差上界。",
                         "外参与设备时钟关系待独立标定；端点重叠不保证连续有效。"],
              "decoder_version": DECODER_VERSION}
    try:
        progress("正在读取 IMU 批次与雷达包时间…")
        batches, imu_count = load_batches(imu)
        scan = scan_pcap(pcap)
        times = [b["host_unix_s"] for b in batches]
        first = scan.frames[0]["packet_capture_times"][0]
        last = scan.frames[-1]["packet_capture_times"][-1]
        previous = None
        counts = Counter()
        with (output / "imu_batches.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(batches[0]))
            writer.writeheader()
            writer.writerows(batches)
        fields = ["packet_index", "frame_index", "packet_in_frame", "payload_offset",
                  "host_unix_s", "previous_batch", "next_batch", "previous_line_first",
                  "previous_line_last", "next_line_first", "next_line_last",
                  "since_previous_s", "until_next_s", "status"]
        progress("正在生成逐包关联表…")
        with (output / "packet_imu.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for frame in scan.frames:
                for index, timestamp in enumerate(frame["packet_capture_times"]):
                    if (not math.isfinite(timestamp) or
                            (previous is not None and timestamp < previous)):
                        raise ValueError("雷达主机时间无效或倒退，拒绝自动配对")
                    previous = timestamp
                    before, after, status = bracket(timestamp, batches, times, max_gap_s)
                    row = {"packet_index": frame["packet_start_index"] + index,
                           "frame_index": frame["index"], "packet_in_frame": index,
                           "payload_offset": frame["offsets"][index],
                           "host_unix_s": f"{timestamp:.9f}", "status": status}
                    for prefix, batch in (("previous", before), ("next", after)):
                        if batch is not None:
                            row[prefix + "_batch"] = batch["batch_index"]
                            row[prefix + "_line_first"] = batch["line_first"]
                            row[prefix + "_line_last"] = batch["line_last"]
                    if before is not None:
                        row["since_previous_s"] = f"{timestamp - before['host_unix_s']:.9f}"
                    if after is not None:
                        row["until_next_s"] = f"{after['host_unix_s'] - timestamp:.9f}"
                    writer.writerow(row)
                    counts[status] += 1
        progress("正在校验来源文件并保存报告…")
        status = "NO_USABLE_BRACKETS"
        if counts["host_bracketed"]:
            status = ("HOST_ASSOCIATED" if counts["host_bracketed"] == scan.valid_packets
                      and scan.invalid_packets == 0 else "PARTIAL_HOST_ASSOCIATION")
        report.update(status=status,
                      lidar_packets=scan.valid_packets, lidar_frames=len(scan.frames),
                      invalid_packets=scan.invalid_packets, ignored_packets=scan.ignored_packets,
                      decoder_warnings=scan.warnings, imu_frames=imu_count, imu_batches=len(batches),
                      packet_status_counts=dict(counts),
                      host_overlap_s=max(0.0, min(last, times[-1]) - max(first, times[0])),
                      sources={str(p): {"bytes": p.stat().st_size, "sha256": sha256_file(p)}
                               for p in (pcap, imu)},
                      generator_sha256=sha256_file(Path(__file__)),
                      files={p.name: sha256_file(p) for p in output.glob("*.csv")})
        quality_path = session / "quality.json"
        if quality_path.is_file():
            quality = json.loads(quality_path.read_text("utf-8"))
            report["capture_status"] = quality.get("status")
            report["capture_failed_checks"] = [c for c in quality.get("checks", []) if not c.get("ok")]
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        (output / "association.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-gap-s", type=float)
    args = parser.parse_args()
    result = associate(args.session, output=args.output, max_gap_s=args.max_gap_s)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "HOST_ASSOCIATED" else 3


if __name__ == "__main__":
    raise SystemExit(main())

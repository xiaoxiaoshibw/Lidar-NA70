"""只读检查已结束的 NA70 采集会话；不修改原始文件，不判定标定或同步。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def audit(root):
    root = Path(root).resolve()
    errors = []
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        quality = json.loads((root / "quality.json").read_text(encoding="utf-8"))
        if manifest.get("status") != quality.get("status"):
            errors.append("manifest 和 quality 状态不一致")
        entries = manifest["files"]
        if not isinstance(entries, list) or not entries:
            raise ValueError("manifest 文件列表为空或无效")
        paths = set()
        for item in entries:
            name = item["path"]
            path = (root / name).resolve()
            if root not in path.parents or name in paths:
                errors.append(f"非法或重复路径: {name}")
                continue
            paths.add(name)
            if not path.is_file():
                errors.append(f"缺失文件: {name}")
            elif path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
                errors.append(f"大小或哈希不符: {name}")
        required = {"quality.json", "events.jsonl", "operator.json", "raw/imu.bin",
                    "raw/imu_chunks.jsonl", "imu_frames.jsonl", "imu_host_receive.csv", "raw/lidar.pcap"}
        for name in sorted(required - paths):
            errors.append(f"清单缺少必需文件: {name}")
        actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
        for name in sorted(actual - paths - {"manifest.json"}):
            errors.append(f"未列入清单: {name}")
        raw = root / "raw/imu.bin"
        chunks = root / "raw/imu_chunks.jsonl"
        offset = count = 0
        previous_chunk_time = None
        max_chunk_gap_s = 0.0
        if raw.is_file() and chunks.is_file():
            with chunks.open(encoding="utf-8") as f:
                for line in f:
                    chunk = json.loads(line)
                    if chunk["offset"] != offset or not isinstance(chunk["bytes"], int) or chunk["bytes"] <= 0:
                        raise ValueError(f"IMU 块 {count} 偏移或长度无效")
                    timestamp = chunk["t_mono"]
                    if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or (previous_chunk_time is not None and timestamp < previous_chunk_time):
                        raise ValueError(f"IMU 块 {count} 单调时间无效或倒退")
                    if previous_chunk_time is not None:
                        max_chunk_gap_s = max(max_chunk_gap_s, timestamp - previous_chunk_time)
                    previous_chunk_time = timestamp
                    offset += chunk["bytes"]
                    count += 1
            if offset != raw.stat().st_size:
                errors.append(f"IMU 块覆盖 {offset} 字节，与原始文件大小不符")
        frame_count = csv_count = None
        wraps = sequence_breaks = 0
        if (root / "imu_frames.jsonl").is_file():
            with (root / "imu_frames.jsonl").open(encoding="utf-8") as f:
                frame_count = 0
                previous = None
                for line in f:
                    frame = json.loads(line)
                    timestamp, tid, segment = frame["t_mono"], frame["tid"], frame["segment"]
                    if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or not isinstance(tid, int) or not 0 <= tid <= 65535 or not isinstance(segment, int) or segment < 0:
                        raise ValueError(f"IMU 帧 {frame_count} 字段无效")
                    if previous:
                        if timestamp < previous[0] or segment < previous[2]:
                            raise ValueError(f"IMU 帧 {frame_count} 时间或段号倒退")
                        if segment == previous[2]:
                            if previous[1] == 60000 and tid == 1:
                                wraps += 1
                            elif tid != ((previous[1] + 1) & 65535):
                                sequence_breaks += 1
                    previous = timestamp, tid, segment
                    frame_count += 1
                if sequence_breaks:
                    errors.append(f"IMU 帧序号异常 {sequence_breaks} 次")
        if (root / "imu_host_receive.csv").is_file():
            with (root / "imu_host_receive.csv").open(encoding="utf-8", newline="") as f:
                rows = csv.reader(f)
                header = next(rows)
                if "device_time_s" in header or "host_monotonic_s" not in header:
                    errors.append("IMU CSV 时间字段异常")
                csv_count = sum(1 for _ in rows)
        reported = (quality.get("imu") or {}).get("frames")
        if frame_count is not None and (frame_count != csv_count or frame_count != reported):
            errors.append(f"IMU 帧数不一致: JSONL={frame_count}, CSV={csv_count}, quality={reported}")
        return {"schema": "na70-session-audit-v1", "session_id": manifest.get("session_id"),
                "status": "failed" if errors else "ok", "capture_status": quality.get("status"),
                "errors": errors, "imu_chunks": count,
                "imu_bytes_covered": offset, "imu_frames": frame_count,
                "max_imu_chunk_receive_gap_s": round(max_chunk_gap_s, 6) if count > 1 else None,
                "observed_60000_to_1": wraps, "sequence_breaks": sequence_breaks,
                "limits": ["status 仅表示文件审计结果，不代表采集质量通过", "不验证硬件同步或标定", "接收块间隔不是传感器采样间隔", "未解码 PCAP，也未验证实际有效时间重叠"]}
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        return {"schema": "na70-session-audit-v1", "status": "failed", "errors": errors + [str(exc)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    args = parser.parse_args()
    result = audit(args.session)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

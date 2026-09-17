# -*- coding: utf-8 -*-
"""离线运行 10 帧滑动窗口叠加：PCAP → 逐帧解码 → Accumulator → JSONL 日志。

Phase 1 验收工具。产物写入 Plan/evidence/goal-demo/<run>/：
  accumulation.jsonl  逐帧统计（帧号、时间、窗口帧数、点数、耗时）
  summary.json        配置版本、耗时分布、内存采样

用法（在仓库根目录）：
  .\\web\\.venv\\Scripts\\python.exe tools\\run_goal_demo_offline.py ^
      --pcap radar_capture_data\\2\\9.14_caochangyou.pcap --run 20260917-phase1
"""
from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))

from backend import decoder  # noqa: E402
from backend.goal_detection.accumulation import Accumulator  # noqa: E402
from backend.goal_detection.config import configuration_version, load_configuration  # noqa: E402


def working_set_kb() -> int | None:
    """进程工作集（KB）。Windows 用 psapi，其他平台返回 None。"""
    try:
        import ctypes.wintypes as wt

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        handle = kernel32.GetCurrentProcess()
        get_info = (getattr(kernel32, "K32GetProcessMemoryInfo", None)
                    or ctypes.windll.psapi.GetProcessMemoryInfo)
        if get_info(handle, ctypes.byref(pmc), pmc.cb):
            return int(pmc.WorkingSetSize // 1024)
    except Exception:
        pass
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcap", required=True, action="append",
                        help="PCAP 路径，可重复")
    parser.add_argument("--run", default=time.strftime("%Y%m%d-%H%M%S"), help="运行目录名")
    parser.add_argument("--max-frames", type=int, default=0, help="0 = 全片")
    parser.add_argument("--window", type=int, default=0, help="覆盖 window_size，0 = 用默认")
    parser.add_argument("--voxel", type=float, default=0.0, help="覆盖 voxel_size，0 = 用默认")
    parser.add_argument("--range", default="", help="覆盖 range，格式 min,max")
    args = parser.parse_args()

    overrides = {}
    if args.window:
        overrides.setdefault("accumulation", {})["window_size"] = args.window
    if args.voxel:
        overrides.setdefault("accumulation", {})["voxel_size"] = args.voxel
    if args.range:
        lo, hi = (float(v) for v in args.range.split(","))
        overrides.setdefault("accumulation", {})["range"] = {"min": lo, "max": hi}
    cfg = load_configuration(overrides=overrides or None)
    acc_cfg = cfg["accumulation"]
    acc = Accumulator(acc_cfg)

    out_dir = ROOT / "Plan" / "evidence" / "goal-demo" / args.run
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "label": "REAL_PCAP_WINDOWS",
        "config_version": configuration_version(cfg),
        "config": cfg,
        "captures": [str(p) for p in args.pcap],
        "window_size": acc.window_size,
        "voxel_size": acc.voxel_size,
        "range": [acc.range_min, acc.range_max],
        "memory_samples_kb": [working_set_kb()],
    }

    timings, point_counts, frame_rows = [], [], []
    total_frames = 0
    started = time.perf_counter()

    for pcap_path in args.pcap:
        pcap = Path(pcap_path)
        scan = decoder.scan_pcap(pcap)
        frames = scan.frames if not args.max_frames else scan.frames[:args.max_frames]
        summary.setdefault("frames_per_capture", []).append(
            {"pcap": str(pcap), "frames": len(frames), "duration_s": round(float(scan.duration), 3)})
        source = pcap.open("rb")
        for frame in frames:
            t0 = time.perf_counter()
            points = decoder.decode_frame(pcap, frame, source=source)
            t1 = time.perf_counter()
            cloud_points = np.ascontiguousarray(points, dtype="<f4")
            stamp = decoder.public_frame(frame)
            from backend.goal_detection.buffer import StampedCloud
            cloud = StampedCloud(cloud_points, frame_index=stamp["index"],
                                 device_time=stamp.get("device_time"))
            stats = acc.push(cloud)
            t2 = time.perf_counter()
            total_frames += 1
            decode_ms = (t1 - t0) * 1000.0
            accumulate_ms = (t2 - t1) * 1000.0
            timings.append(decode_ms + accumulate_ms)
            point_counts.append(stats["accumulated_points"])
            frame_rows.append({
                "frame_index": stats["frame_index"],
                "device_time": stamp.get("device_time"),
                "accumulated_frames": stats["accumulated_frames"],
                "window_raw_points": stats["window_raw_points"],
                "accumulated_points": stats["accumulated_points"],
                "decode_ms": round(decode_ms, 2),
                "accumulate_ms": round(accumulate_ms, 2),
                "monotonic": stats["monotonic"],
            })
            if total_frames % 200 == 0:
                summary["memory_samples_kb"].append(working_set_kb())
                print(f"{pcap.name}: frame {stats['frame_index']} "
                      f"acc={stats['accumulated_frames']} pts={stats['accumulated_points']}")
        source.close()

    summary["total_frames"] = total_frames
    summary["wall_time_s"] = round(time.perf_counter() - started, 2)
    if timings:
        summary["frame_ms_p50"] = round(float(np.percentile(timings, 50)), 2)
        summary["frame_ms_p95"] = round(float(np.percentile(timings, 95)), 2)
        summary["frame_ms_max"] = round(float(np.max(timings)), 2)
        summary["accumulated_points_p50"] = int(np.percentile(point_counts, 50))
        summary["accumulated_points_max"] = int(np.max(point_counts))
    summary["memory_samples_kb"].append(working_set_kb())
    summary["non_monotonic_frames"] = int(sum(1 for r in frame_rows if not r["monotonic"]))

    with (out_dir / "accumulation.jsonl").open("w", encoding="utf-8") as handle:
        for row in frame_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({k: v for k, v in summary.items() if k != "config"},
                     ensure_ascii=False, indent=2))
    print(f"产物目录: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

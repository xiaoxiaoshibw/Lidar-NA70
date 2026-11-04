# -*- coding: utf-8 -*-
"""离线运行完整 goal demo 管线：PCAP → 叠加 → 检测 → 状态机跟踪 → JSONL 日志。

产物写入 Plan/evidence/goal-demo/<run>/：
  goal_run.jsonl  逐帧状态（capture 身份、叠加点数、候选、跟踪状态、耗时）
  summary.json    配置版本、输入/源码/环境哈希、锁定时间线、耗时与内存采样

行格式与 capture 归属契约见 docs/GOAL_DEBUG_CONTRACT.md（run row v1）。

用法（在仓库根目录）：
  .\\web\\.venv\\Scripts\\python.exe tools\\run_goal_demo_offline.py ^
      --pcap radar_capture_data\\2\\9.14_caochangyou.pcap --run 20260917-phase4 ^
      --capture-id c4cc19fb-3020-4ccc-8407-1521f71b7212
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))

from backend import decoder  # noqa: E402
from backend.goal_detection.config import configuration_version, load_configuration  # noqa: E402
from backend.goal_detection.pipeline import GoalDemoPipeline  # noqa: E402

# 参与算法结果的源码：哈希进 summary，使运行可绑定到确切代码版本。
SOURCE_FILES = ["tools/run_goal_demo_offline.py", "tools/score_goal_detection.py",
                "web/backend/goal_detection.yaml"]
SOURCE_GLOBS = ["web/backend/goal_detection/*.py"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_hashes() -> dict:
    files = [ROOT / rel for rel in SOURCE_FILES]
    for pattern in SOURCE_GLOBS:
        files.extend(sorted(ROOT.glob(pattern)))
    return {str(path.relative_to(ROOT)).replace("\\", "/"): sha256_file(path)
            for path in files if path.is_file()}


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
        get_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), wt.DWORD]
        if get_info(handle, ctypes.byref(pmc), pmc.cb):
            return int(pmc.WorkingSetSize // 1024)
    except Exception:
        pass
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcap", required=True, action="append",
                        help="PCAP 路径，可重复；每个 PCAP 使用独立的新 pipeline 实例")
    parser.add_argument("--capture-id", action="append", default=[],
                        help="与 --pcap 顺序对应的标注 capture_id；缺省为 pcap-<输入sha256前16位>")
    parser.add_argument("--run", default=time.strftime("%Y%m%d-%H%M%S"), help="运行目录名")
    parser.add_argument("--max-frames", type=int, default=0, help="0 = 全片")
    parser.add_argument("--window", type=int, default=0, help="覆盖 window_size，0 = 用默认")
    parser.add_argument("--voxel", type=float, default=0.0, help="覆盖 voxel_size，0 = 用默认")
    parser.add_argument("--range", default="", help="覆盖 range，格式 min,max")
    parser.add_argument("--strategy", default="", choices=["", "staged", "poles", "facade"],
                        help="覆盖 strategy.mode（默认取 YAML）")
    parser.add_argument("--no-single", action="store_true", help="跳过单帧检测对比")
    parser.add_argument("--no-roi", action="store_true", help="忽略 webui.demo_roi（全图运行）")
    parser.add_argument("--roi", default="",
                        help="覆盖球门搜索 ROI：xmin,xmax,ymin,ymax,zmin,zmax")
    parser.add_argument("--ground-reference", choices=("", "detection", "full_field"),
                        default="", help="地面拟合范围；缺省按配置，full_field 不受球门 ROI 限制")
    parser.add_argument("--confirmed-static-until", type=float, default=None,
                        help="现场确认的起始静止秒数（按设备时间），缺省不假定静止")
    parser.add_argument("--motion-status", choices=("unknown", "moving"), default="unknown",
                        help="非确认静止区间的雷达运动状态；缺省为 unknown")
    parser.add_argument("--skip-input-hash", action="store_true",
                        help="跳过 PCAP sha256（大文件快速试跑，证据运行勿用）")
    args = parser.parse_args()
    if args.confirmed_static_until is not None and args.confirmed_static_until < 0:
        parser.error("--confirmed-static-until 不能为负")
    if args.no_roi and args.roi:
        parser.error("--no-roi 与 --roi 不能同时使用")

    overrides: dict = {"accumulation": {}}
    if args.window:
        overrides["accumulation"]["window_size"] = args.window
    if args.voxel:
        overrides["accumulation"]["voxel_size"] = args.voxel
    if args.range:
        lo, hi = (float(v) for v in args.range.split(","))
        overrides["accumulation"]["range"] = {"min": lo, "max": hi}
    if args.strategy:
        overrides["strategy"] = {"mode": args.strategy}
    if args.ground_reference:
        overrides["goal_detection"] = {"z_leveling": {
            "reference_roi": args.ground_reference}}
    cfg = load_configuration(overrides=overrides if len(overrides) > 1
                             or overrides["accumulation"] else None)
    if cfg["webui"].get("demo_roi") and not args.no_roi:
        cfg["accumulation"]["roi"] = list(cfg["webui"]["demo_roi"])
    if args.roi:
        try:
            bounds = [float(value) for value in args.roi.split(",")]
        except ValueError:
            parser.error("--roi 必须包含 6 个数值")
        if len(bounds) != 6 or not np.isfinite(bounds).all() or any(
                bounds[axis * 2] >= bounds[axis * 2 + 1] for axis in range(3)):
            parser.error("--roi 必须满足三个轴的 min < max")
        cfg["accumulation"]["roi"] = bounds
    config_version = configuration_version(cfg)

    if len(args.capture_id) > len(args.pcap):
        raise SystemExit("--capture-id 数量不能超过 --pcap 数量")

    out_dir = ROOT / "Plan" / "evidence" / "goal-demo" / args.run
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "label": "REAL_PCAP_WINDOWS",
        "schema_version": "goal-run-v1.1",
        "command": [sys.executable, *sys.argv],
        "motion_status_outside_confirmed_static": args.motion_status,
        "config_version": config_version,
        "config": cfg,
        "env": {"python": sys.version.split()[0], "platform": platform.platform()},
        "source_hashes": source_hashes(),
        "captures": [str(p) for p in args.pcap],
        "window_size": None,
        "voxel_size": None,
        "range": None,
        "strategy": cfg.get("strategy", {}).get("mode"),
        "memory_samples_kb": [working_set_kb()],
    }

    timings, state_counter, lock_events, rows = [], {}, [], []
    total_frames = 0
    started = time.perf_counter()

    for pcap_index, pcap_path in enumerate(args.pcap):
        pcap = Path(pcap_path)
        capture_sha = "" if args.skip_input_hash else sha256_file(pcap)
        capture_id = (args.capture_id[pcap_index] if pcap_index < len(args.capture_id)
                      else f"pcap-{capture_sha[:16]}" if capture_sha else f"pcap-{pcap.stem}")
        # 多 PCAP 隔离：每个输入文件使用全新的 accumulator/tracker。
        pipeline = GoalDemoPipeline(cfg)
        if summary["window_size"] is None:
            summary["window_size"] = pipeline.accumulator.window_size
            summary["voxel_size"] = pipeline.accumulator.voxel_size
            summary["range"] = [pipeline.accumulator.range_min, pipeline.accumulator.range_max]
        scan = decoder.scan_pcap(pcap)
        frames = scan.frames if not args.max_frames else scan.frames[:args.max_frames]
        summary.setdefault("frames_per_capture", []).append(
            {"pcap": str(pcap), "capture_id": capture_id, "sha256": capture_sha,
             "frames": len(frames), "duration_s": round(float(scan.duration), 3)})
        source = pcap.open("rb")
        for frame in frames:
            t0 = time.perf_counter()
            points = decoder.decode_frame(pcap, frame, source=source)
            meta = decoder.public_frame(frame)
            start_time = scan.frames[0].get('device_start_time')
            end_time = meta.get('device_time')
            motion = args.motion_status
            if args.confirmed_static_until is not None and start_time is not None and end_time is not None \
                    and start_time <= end_time <= start_time + args.confirmed_static_until:
                motion = 'confirmed_static'
            result = pipeline.process_frame(points, meta["index"], end_time, motion_status=motion)
            t1 = time.perf_counter()
            total_frames += 1
            elapsed_ms = (t1 - t0) * 1000.0
            timings.append(elapsed_ms)
            goal = result["goal"]
            # State and measurement are orthogonal.  REACQUIRE/LOST must remain
            # visible in evidence even though neither is a current detection.
            state = goal.get("state", "SEARCHING")
            state_counter[state] = state_counter.get(state, 0) + 1
            if state == "LOCKED" and goal.get("has_measurement"):
                lock_events.append({"frame_index": result["frame_index"], **{
                    k: goal[k] for k in ("id", "distance", "confidence", "center")}})
            motion_diagnostics = [candidate for candidate in
                                  result.get("accumulated_rejected_candidates", [])
                                  if candidate.get("reject_reason") ==
                                  "uncompensated_moving_window"]
            rows.append({
                "schema_version": "goal-run-v1.1",
                "capture_id": capture_id,
                "capture_sha256": capture_sha,
                "input_sha256": capture_sha,
                "config_version": config_version,
                "frame_index": result["frame_index"],
                "device_time": result["device_time"],
                "motion_status": motion,
                "accumulated_frames": result["accumulated_frames"],
                "accumulated_points": result["accumulated_points"],
                "elapsed_ms": round(elapsed_ms, 2),
                "strategy": result.get("strategy"),
                "focus_active": bool((result.get("focus") or {}).get("active")),
                "bev_regions": len(result.get("bev_regions") or []),
                "single_best": result["single_candidates"][0] if result["single_candidates"] else None,
                "accumulated_best": result["accumulated_candidates"][0] if result["accumulated_candidates"] else None,
                "selected_measurement": result.get("selected_measurement"),
                "selected_measurement_source": result.get("selected_measurement_source"),
                "motion_diagnostic_candidates": motion_diagnostics,
                "time_quality": result.get("time_quality", goal.get("time_quality")),
                "candidate_evidence": {
                    "single": result.get("single_candidates", []),
                    "accumulated": result.get("accumulated_candidates", []),
                    "selected": result.get("selected_measurement"),
                    "motion_diagnostic": motion_diagnostics,
                },
                "accumulated_cloud_id": result.get("accumulated_cloud_id"),
                "execution_trace": result.get("execution_trace"),
                "goal": {k: goal[k] for k in goal if k != "detected"},
            })
            if total_frames % 200 == 0:
                summary["memory_samples_kb"].append(working_set_kb())
                print(f"{pcap.name}: frame {result['frame_index']} state={state} "
                      f"conf={goal.get('confidence', '-')}")
        source.close()

    summary["total_frames"] = total_frames
    summary["wall_time_s"] = round(time.perf_counter() - started, 2)
    if timings:
        summary["frame_ms_p50"] = round(float(np.percentile(timings, 50)), 2)
        summary["frame_ms_p95"] = round(float(np.percentile(timings, 95)), 2)
        summary["frame_ms_max"] = round(float(np.max(timings)), 2)
    summary["state_counts"] = state_counter
    summary["lock_events"] = lock_events
    summary["memory_samples_kb"].append(working_set_kb())

    with (out_dir / "goal_run.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({k: v for k, v in summary.items() if k != "config"},
                     ensure_ascii=False, indent=2))
    print(f"产物目录: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

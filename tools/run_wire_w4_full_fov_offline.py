"""Offline W4: connected-component full-FOV wire proposals on a real PCAP.

Builds a PacketWindowRing over decoded packets, selects a causal window,
decodes into a RayBatch, then runs the wire pipeline with full-FOV
proposals (no manual ROI). Results remain UNKNOWN_LINE evidence only -
full-FOV never implies HV-line identity.

Usage:
  ./web/.venv/Scripts/python.exe tools/run_wire_w4_full_fov_offline.py \
      --pcap radar_capture_data/2/9.14_caochangzhong2.pcap \
      --end-frame 600 --duration 1.0 \
      --output Plan/evidence/hv-line/<run-id>
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT))

from backend import decoder, thin_wire  # noqa: E402
from na70_core import RayBatch  # noqa: E402
from na70_core.wire import (FullFovConfig, WindowInputs, load_wire_config,  # noqa: E402
                            run_detection_window)
from na70_core.windows import PacketWindowRing, RingLimits  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--pcap", required=True, type=Path)
    parser.add_argument("--end-frame", type=int, required=True)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--install-direction", type=int, default=1, choices=[1, 2])
    parser.add_argument("--wire-config", type=Path, default=None)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-components", type=int, default=16)
    args = parser.parse_args(argv)

    pcap: Path = args.pcap
    if not pcap.is_file():
        raise SystemExit(f"PCAP not found: {pcap}")
    output: Path = args.output
    output.mkdir(parents=True, exist_ok=False)

    capture_sha = sha256_file(pcap)
    scan = decoder.scan_pcap(pcap)
    if not 0 <= args.end_frame < len(scan.frames):
        raise SystemExit(f"end frame {args.end_frame} outside [0,{len(scan.frames)})")

    cfg_w1 = thin_wire.load_config()
    packet_timeline = thin_wire.packet_timeline(scan, cfg_w1)
    limits = RingLimits(max_packets_in_ring=max(2000, len(packet_timeline) + 64),
                        max_window_packets=max(5000, 100 * len(scan.frames)),
                        time_epsilon_s=cfg_w1["time_epsilon_s"],
                        max_packet_gap_s=cfg_w1["max_packet_gap_s"])
    ring = PacketWindowRing(limits)
    for packet in packet_timeline:
        ring.push(frame_index=packet.frame_index, local_index=packet.local_index,
                  device_time=packet.device_time, capture_time=packet.capture_time)
    duration = args.duration if args.duration and args.duration > 0 else None
    selected = ring.select(duration, end_frame_index=args.end_frame)

    grouped: dict[int, list] = {}
    for p in selected.packets:
        grouped.setdefault(p.frame_index, []).append(p)
    skipped = []
    buffers = {k: [] for k in ("codes", "points", "frame_indices", "packet_indices",
                               "point_indices", "device_times", "capture_times")}
    with pcap.open("rb") as source:
        for frame_index, parts in sorted(grouped.items()):
            frame = scan.frames[frame_index]
            if not frame["complete"]:
                skipped.append(frame_index); del grouped[frame_index]; continue
            pts = decoder.decode_frame(pcap, frame, args.install_direction, source=source)
            codes = decoder.decode_frame_codes(pcap, frame, source=source)
            ray_ids = np.concatenate(
                [np.arange(p.local_index * 200, (p.local_index + 1) * 200, dtype=np.int64)
                 for p in parts])
            buffers["codes"].append(codes[ray_ids])
            buffers["points"].append(pts[ray_ids])
            buffers["frame_indices"].append(np.full(ray_ids.size, frame_index, dtype=np.int64))
            buffers["point_indices"].append(ray_ids.astype(np.int64))
            buffers["packet_indices"].append(np.repeat(
                np.array([p.index for p in parts], dtype=np.int64), 200))
            buffers["device_times"].append(np.repeat(
                np.array([p.device_time if p.device_time is not None else np.nan for p in parts]),
                200))
            buffers["capture_times"].append(np.repeat(
                np.array([p.capture_time for p in parts], dtype=np.float64), 200))
    arrays = {k: np.concatenate(v) if v else np.empty((0, 4) if k in ("codes", "points") else (0,))
              for k, v in buffers.items()}
    batch = RayBatch.from_decoded(capture_sha256=capture_sha,
                                  coordinate_frame="lidar_link",
                                  install_direction=args.install_direction,
                                  codes=arrays["codes"], points=arrays["points"],
                                  frame_indices=arrays["frame_indices"],
                                  packet_indices=arrays["packet_indices"],
                                  point_indices=arrays["point_indices"],
                                  device_times=arrays["device_times"],
                                  capture_times=arrays["capture_times"])
    wire_cfg = load_wire_config(args.wire_config)
    config_sha = hashlib.sha256(json.dumps(wire_cfg.to_dict(), sort_keys=True).encode()).hexdigest()
    fov_cfg = FullFovConfig(max_components=args.max_components)
    inputs = WindowInputs(batch=batch, selected_window=selected,
                          source_window_id=f"pcap-{capture_sha[:8]}-f{args.end_frame}-d{duration}",
                          manual_roi_aabb_m=None, config=wire_cfg,
                          window_end_device_time_s=selected.end_device_time,
                          availability=("available"
                                        if selected.time_quality in ("ok", "partial_window")
                                        else "invalid_time"),
                          enable_full_fov=True, full_fov_config=fov_cfg)
    result = run_detection_window(inputs)

    manifest = {
        "schema_version": "na70-wire-w4-run-v1", "status": "COMPLETED",
        "pcap_path": str(pcap), "capture_sha256": capture_sha,
        "capture_frames": len(scan.frames), "install_direction": args.install_direction,
        "end_frame": args.end_frame, "requested_duration_s": duration,
        "window_packet_count": selected.packet_count,
        "window_quality": selected.time_quality,
        "window_partial": selected.partial_window,
        "config_sha256": config_sha,
        "full_fov_config": {"azimuth_slab_deg": fov_cfg.azimuth_slab_deg,
                            "elevation_slab_deg": fov_cfg.elevation_slab_deg,
                            "min_component_points": fov_cfg.min_component_points,
                            "min_horizontal_extent_m": fov_cfg.min_horizontal_extent_m,
                            "max_component_spatial_diameter_m": fov_cfg.max_component_spatial_diameter_m,
                            "min_component_span_along_dominant_axis_m": fov_cfg.min_component_span_along_dominant_axis_m,
                            "max_components": fov_cfg.max_components},
        "command": [sys.executable, *sys.argv],
        "environment": {"python": sys.version, "platform": platform.platform(),
                        "numpy": np.__version__},
        "incomplete_frames_skipped": skipped,
        "wire_return_status": "NOT_EVALUATED",
        "warnings": ["Full-FOV UNKNOWN_LINE evidence only; identity requires reviewed truth."],
        "source_hashes": {
            rel: sha256_file(ROOT / rel) for rel in (
                "packages/na70_core/na70_core/wire/pipeline.py",
                "packages/na70_core/na70_core/wire/proposals.py",
                "packages/na70_core/na70_core/wire/proposal_full_fov.py",
                "packages/na70_core/na70_core/wire/fitting.py",
                "packages/na70_core/na70_core/wire/validation.py",
                "packages/na70_core/na70_core/wire/constants.py",
                "packages/na70_core/na70_core/wire/config.py",
                "packages/na70_core/na70_core/wire_default.yaml",
                "packages/na70_core/na70_core/windows.py",
                "packages/na70_core/na70_core/observations.py",
                "web/backend/decoder.py", "web/backend/thin_wire.py",
                "tools/run_wire_w4_full_fov_offline.py",
            )},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), "utf-8")
    with (output / "stages.jsonl").open("w", encoding="utf-8") as fh:
        for row in result.stage_trace:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    (output / "candidate.json").write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
                                           "utf-8")
    lines = [
        "# W4 全场提案离线运行报告（NOT_EVALUATED）", "",
        f"PCAP:{pcap.name} end_frame={args.end_frame} duration={duration} s; window quality={selected.time_quality}（{selected.packet_count} packets）",
        f"config_sha256 {config_sha[:16]}… max_components={fov_cfg.max_components}",
        f"状态：{result.status}；首个阻断阶段：{result.first_blocking_stage or 'none'}；拒绝原因：{result.reject_reason or '-'}",
        "",
        "Full-FOV proposals are UNKNOWN_LINE evidence only; HV-line identity does not follow from a geometric line fit.",
        "",
        "## stage trace（顺序与 counts 详 stages.jsonl）",
        *[f"- {row['stage']}: {row['status']}" for row in result.stage_trace],
    ]
    if result.candidate_line:
        cand = result.candidate_line
        lines += ["", "## 最佳候选（仅一个 best span 汇报入口）",
                  f"- direction={cand['direction']}",
                  f"- point_on_line={cand['point_on_line']}",
                  f"- span_m={cand['span_m']}",
                  f"- nearest_distance_m={cand['nearest_distance_m']}",
                  f"- unique_packet_count={cand['unique_packet_count']}"]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "status": result.status,
                      "first_blocking_stage": result.first_blocking_stage,
                      "packets_in_window": selected.packet_count,
                      "config_sha256": config_sha}, ensure_ascii=False))


if __name__ == "__main__":
    main()

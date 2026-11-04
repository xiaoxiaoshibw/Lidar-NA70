"""Offline W3: run manual-ROI line extraction on a real PCAP.

Builds a PacketWindowRing over decoded packets, selects a causal window
ending at a chosen frame, decodes it into a RayBatch, then runs the
wire pipeline. Outputs the manifest + per-stage trace so the reviewer can
see the first blocking stage and gate-by-gate counts.

Evidence directory layout (no overwrite, fails if exists):
  <output>/manifest.json
  <output>/stages.jsonl
  <output>/window.json
  <output>/candidate.json (if status is MEASURED_CURRENT)
  <output>/REPORT.md

Usage:
  ./web/.venv/Scripts/python.exe tools/run_wire_w3_offline.py \
      --pcap radar_capture_data/1.pcap \
      --end-frame 100 --duration 1.0 \
      --roi 1.0,12.0,1.0,4.0,6.0,10.0 \
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
from na70_core.wire import (WindowInputs, load_wire_config,  # noqa: E402
                            run_detection_window)
from na70_core.windows import PacketWindowRing, RingLimits  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_roi(spec: str) -> tuple:
    parts = [float(v) for v in spec.split(",")]
    if len(parts) != 6:
        raise ValueError("--roi must have six comma-separated numbers")
    return tuple(parts)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--pcap", required=True, type=Path)
    parser.add_argument("--end-frame", type=int, required=True)
    parser.add_argument("--duration", type=float, default=None,
                        help="seconds; None or 0 -> single frame")
    parser.add_argument("--roi", type=parse_roi, required=True,
                        help="xmin,xmax,ymin,ymax,zmin,zmax metres (sensor frame)")
    parser.add_argument("--install-direction", type=int, default=1, choices=[1, 2])
    parser.add_argument("--config", type=Path, default=None,
                        help="wire config JSON; default na70_core/wire/wire_default.yaml")
    parser.add_argument("--output", required=True, type=Path,
                        help="evidence directory; must not exist")
    args = parser.parse_args(argv)

    pcap: Path = args.pcap
    if not pcap.is_file():
        raise SystemExit(f"PCAP not found: {pcap}")
    output: Path = args.output
    output.mkdir(parents=True, exist_ok=False)

    capture_sha = sha256_file(pcap)
    scan = decoder.scan_pcap(pcap)
    if args.end_frame < 0 or args.end_frame >= len(scan.frames):
        raise SystemExit(f"end frame {args.end_frame} outside [0,{len(scan.frames)})")

    # Build the offline timeline for the whole capture (spec inputs to
    # select_window's segment bookkeeping), then feed the ring with the same
    # data. We reuse the offline path's timeline for segment decisions; the
    # incremental ring mirrors that exactly via PacketIdentity pushes.
    cfg_w1 = thin_wire.load_config()
    packets_offline = thin_wire.packet_timeline(scan, cfg_w1)
    limits = RingLimits(max_packets_in_ring=max(2000, len(packets_offline) + 64),
                        max_window_packets=max(5000,
                                               sum(100 for _ in range(len(scan.frames)))),
                        time_epsilon_s=cfg_w1["time_epsilon_s"],
                        max_packet_gap_s=cfg_w1["max_packet_gap_s"])
    ring = PacketWindowRing(limits)
    for packet in packets_offline:
        ring.push(frame_index=packet.frame_index, local_index=packet.local_index,
                  device_time=packet.device_time, capture_time=packet.capture_time)

    duration = args.duration if args.duration and args.duration > 0 else None
    selected = ring.select(duration, end_frame_index=args.end_frame)

    # Decode the selected packets into a RayBatch, keeping global ray IDs.
    grouped: dict[int, list] = {}
    for p in selected.packets:
        grouped.setdefault(p.frame_index, []).append(p)
    buffers = {k: [] for k in ("codes", "points", "frame_indices", "packet_indices",
                               "point_indices", "device_times", "capture_times")}
    incomplete_frames_skipped: list[int] = []
    with pcap.open("rb") as source:
        for frame_index, parts in sorted(grouped.items()):
            frame = scan.frames[frame_index]
            if not frame["complete"]:
                # Incomplete bundles must not be decoded; a partial 100-ray
                # block misaligns (frame_index, packet_index, point_index)
                # downstream. Skipping keeps all kept packets' identity
                # intact and reports the gap in the manifest.
                incomplete_frames_skipped.append(frame_index)
                del grouped[frame_index]
                continue
            pts = decoder.decode_frame(pcap, frame,
                                       install_direction=args.install_direction,
                                       source=source)
            codes = decoder.decode_frame_codes(pcap, frame, source=source)
            ray_ids = np.concatenate(
                [np.arange(p.local_index * 200, (p.local_index + 1) * 200, dtype=np.int64)
                 for p in parts])
            buffers["codes"].append(codes[ray_ids])
            buffers["points"].append(pts[ray_ids])
            buffers["frame_indices"].append(np.full(ray_ids.size, frame_index, dtype=np.int64))
            # frame-local point identity = position in the 20000-ray frame
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

    batch = RayBatch.from_decoded(
        capture_sha256=capture_sha,
        coordinate_frame="lidar_link",
        install_direction=args.install_direction,
        codes=arrays["codes"], points=arrays["points"],
        frame_indices=arrays["frame_indices"],
        packet_indices=arrays["packet_indices"],
        point_indices=arrays["point_indices"],
        device_times=arrays["device_times"],
        capture_times=arrays["capture_times"])

    wire_cfg = load_wire_config(args.config)
    config_payload = wire_cfg.to_dict()
    config_sha = hashlib.sha256(json.dumps(config_payload, sort_keys=True).encode()).hexdigest()

    inputs = WindowInputs(batch=batch, selected_window=selected,
                          source_window_id=f"pcap-{capture_sha[:8]}-f{args.end_frame}-d{duration}",
                          manual_roi_aabb_m=args.roi,
                          config=wire_cfg,
                          window_end_device_time_s=selected.end_device_time,
                          availability=("available"
                                        if selected.time_quality in ("ok", "partial_window")
                                        else "invalid_time"))
    result = run_detection_window(inputs)

    window_record = selected.to_dict()
    source_hashes = {
        "web/backend/decoder.py": sha256_file(ROOT / "web/backend/decoder.py"),
        "packages/na70_core/na70_core/__init__.py": sha256_file(ROOT / "packages/na70_core/na70_core/__init__.py"),
        "packages/na70_core/na70_core/observations.py": sha256_file(ROOT / "packages/na70_core/na70_core/observations.py"),
        "packages/na70_core/na70_core/windows.py": sha256_file(ROOT / "packages/na70_core/na70_core/windows.py"),
        "packages/na70_core/na70_core/wire/__init__.py": sha256_file(ROOT / "packages/na70_core/na70_core/wire/__init__.py"),
        "packages/na70_core/na70_core/wire/pipeline.py": sha256_file(ROOT / "packages/na70_core/na70_core/wire/pipeline.py"),
        "packages/na70_core/na70_core/wire/fitting.py": sha256_file(ROOT / "packages/na70_core/na70_core/wire/fitting.py"),
        "packages/na70_core/na70_core/wire/proposals.py": sha256_file(ROOT / "packages/na70_core/na70_core/wire/proposals.py"),
        "packages/na70_core/na70_core/wire/validation.py": sha256_file(ROOT / "packages/na70_core/na70_core/wire/validation.py"),
        "packages/na70_core/na70_core/wire/config.py": sha256_file(ROOT / "packages/na70_core/na70_core/wire/config.py"),
        "packages/na70_core/na70_core/wire_default.yaml": sha256_file(ROOT / "packages/na70_core/na70_core/wire_default.yaml"),
        "tools/run_wire_w3_offline.py": sha256_file(ROOT / "tools/run_wire_w3_offline.py"),
    }
    manifest = {
        "schema_version": "na70-wire-w3-run-v1",
        "status": "COMPLETED",
        "pcap_path": str(pcap),
        "capture_sha256": capture_sha,
        "capture_frames": len(scan.frames),
        "install_direction": args.install_direction,
        "end_frame": args.end_frame,
        "requested_duration_s": duration,
        "manual_roi_m": list(args.roi),
        "config": config_payload,
        "config_sha256": config_sha,
        "command": [sys.executable, *sys.argv],
        "environment": {"python": sys.version, "platform": platform.platform(),
                        "numpy": np.__version__,
                        "pyransac3d": __import__("pyransac3d").__file__},
        "source_hashes": source_hashes,
        "wire_return_status": "NOT_EVALUATED",
        "incomplete_frames_skipped": incomplete_frames_skipped,
        "warnings": ["This run is W3 detection evidence only; real-world HV-line identity"
                     " and presence require independent reviewed truth."],
    }
    with (output / "manifest.json").open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    with (output / "window.json").open("w", encoding="utf-8") as fh:
        json.dump(window_record, fh, indent=2, ensure_ascii=False)
    with (output / "stages.jsonl").open("w", encoding="utf-8") as fh:
        for row in result.stage_trace:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (output / "candidate.json").open("w", encoding="utf-8") as fh:
        json.dump(result.to_dict(), fh, indent=2, ensure_ascii=False)

    first_block = result.first_blocking_stage or "none"
    lines = [
        "# W3 离线运行报告", "",
        f"PCAP：{pcap.name}（SHA-256 {capture_sha[:16]}…，{len(scan.frames)} 帧）",
        f"end_frame={args.end_frame}，请求时长={duration}s；实际 window={selected.packet_count} packets，quality={selected.time_quality}。",
        f"ROI={args.roi}（sensor frame，单位米）",
        f"状态：**{result.status}**（geometry={result.geometry_class}）",
        f"首个阻断阶段：{first_block}" + (f"，拒绝原因：{result.reject_reason}" if result.reject_reason else ""),
    ]
    if result.candidate_line:
        seg = result.candidate_line.get("segment_endpoints_sensor_m")
        lines += [
            "", "## 候选详",
            f"- direction={result.candidate_line['direction']}",
            f"- point_on_line={result.candidate_line['point_on_line']}",
            f"- segment_endpoints={seg}",
            f"- span_m={result.candidate_line['span_m']}",
            f"- coverage_fraction={result.candidate_line['coverage_fraction']}",
            f"- largest_internal_gap_m={result.candidate_line['largest_internal_gap_m']}",
            f"- nearest_distance_m={result.candidate_line['nearest_distance_m']}",
            f"- rms/p95={result.candidate_line['rms_orthogonal_m']}/{result.candidate_line['p95_orthogonal_m']}",
            f"- unique_packets={result.candidate_line['unique_packet_count']}, unique_frames={result.candidate_line['unique_frame_count']}",
        ]
    lines += [
        "", "## 阶段执行记录（顺序与 counts 见 stages.jsonl/candidate.json）",
        *[f"- {row['stage']}: {row['status']}" for row in result.stage_trace],
        "", "本次运行只反映算法在给定 ROI 与时长下的行为，"
            "**不能解释为已识别高压线**。真实身份仍需 independent reviewed truth。"
    ]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "status": result.status,
                      "first_blocking_stage": first_block,
                      "packets_in_window": selected.packet_count,
                      "config_sha256": config_sha}, ensure_ascii=False))


if __name__ == "__main__":
    main()

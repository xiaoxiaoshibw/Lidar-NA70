"""W1 raw-return evidence, independent of goal detection and tracking.

ROI/reference membership is a diagnostic label, never target attribution.
Packet timestamps are preserved; no interpolated per-point times are invented.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from copy import deepcopy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys

import numpy as np
from na70_core import direction_known_from_codes

from . import decoder

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "tools" / "thin_wire_config.yaml"
SCHEMA = "thin-wire-w1-v1"
_DEFAULTS = json.loads(DEFAULT_CONFIG.read_text("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), "utf-8")


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'}")
    return value


def _roi(value, name):
    try:
        array = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be six finite coordinates") from error
    if array.shape != (6,) or not np.isfinite(array).all() or np.any(array[::2] >= array[1::2]):
        raise ValueError(f"{name} must have finite min < max on each axis")
    return array


def validate_config(supplied: dict) -> dict:
    """Merge a partial W1 configuration with defaults without mutating either input."""
    defaults = _DEFAULTS
    if not isinstance(supplied, dict) or set(supplied) - set(defaults):
        raise ValueError("Unknown thin-wire configuration field")
    cfg = {**deepcopy(defaults), **deepcopy(supplied)}
    if not isinstance(cfg["acquisition"], dict) or set(cfg["acquisition"]) - set(defaults["acquisition"]):
        raise ValueError("Unknown acquisition field")
    cfg["acquisition"] = {**defaults["acquisition"], **cfg["acquisition"]}
    if cfg["schema_version"] != SCHEMA:
        raise ValueError("Unsupported thin-wire schema")
    for key in ("max_packet_gap_s", "time_epsilon_s"):
        _number(cfg[key], key, positive=True)
    if cfg["time_epsilon_s"] >= cfg["max_packet_gap_s"]:
        raise ValueError("time_epsilon_s must be below max_packet_gap_s")
    for key in ("max_window_points", "max_windows", "render_max_points", "render_dpi"):
        if type(cfg[key]) is not int or cfg[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    if type(cfg["include_single_frame"]) is not bool:
        raise ValueError("include_single_frame must be boolean")
    if type(cfg["install_direction"]) is not int or cfg["install_direction"] not in (1, 2):
        raise ValueError("install_direction must be 1 or 2")
    seconds = cfg["window_seconds"]
    if not isinstance(seconds, list):
        raise ValueError("window_seconds must be a list")
    for value in seconds:
        _number(value, "window_seconds", positive=True)
    if len(set(seconds)) != len(seconds) or (not seconds and not cfg["include_single_frame"]):
        raise ValueError("Window durations must be unique; at least one window is required")
    boxes = []
    if cfg["target_roi"] is not None:
        boxes.append(_roi(cfg["target_roi"], "target_roi"))
    if not isinstance(cfg["background_rois"], list):
        raise ValueError("background_rois must be a list")
    boxes.extend(_roi(box, "background_roi") for box in cfg["background_rois"])
    for i, first in enumerate(boxes):
        for second in boxes[i + 1:]:
            if np.all(np.maximum(first[::2], second[::2]) <= np.minimum(first[1::2], second[1::2])):
                raise ValueError("Target and background ROIs must not overlap or touch")
    reference = cfg["target_reference"]
    if reference is not None:
        keys = {"endpoints_sensor_m", "corridor_radius_m", "position_uncertainty_m", "source", "review_status"}
        if not isinstance(reference, dict) or set(reference) != keys:
            raise ValueError("target_reference requires endpoints, radius, uncertainty, source and review_status")
        endpoints = np.asarray(reference["endpoints_sensor_m"], dtype=float)
        if endpoints.shape != (2, 3) or not np.isfinite(endpoints).all() or np.array_equal(*endpoints):
            raise ValueError("Reference endpoints must be two distinct finite XYZ points")
        _number(reference["corridor_radius_m"], "corridor_radius_m", positive=True)
        _number(reference["position_uncertainty_m"], "position_uncertainty_m")
        if reference["corridor_radius_m"] < reference["position_uncertainty_m"]:
            raise ValueError("Reference corridor must include position uncertainty")
        if not isinstance(reference["source"], str) or not reference["source"].strip():
            raise ValueError("Reference source is required")
        if reference["review_status"] not in ("reviewed", "unreviewed"):
            raise ValueError("Invalid reference review_status")
        if cfg["target_roi"] is None or not in_roi(endpoints, cfg["target_roi"]).all():
            raise ValueError("Reference endpoints must lie inside target_roi")
    acquisition = cfg["acquisition"]
    if acquisition["data_label"] not in ("REAL_PCAP_DIAGNOSTIC", "SYNTHETIC"):
        raise ValueError("Invalid data_label")
    if acquisition["capture_role"] not in ("unknown", "target_present", "target_absent"):
        raise ValueError("Invalid capture_role")
    if acquisition["static_status"] not in ("unknown", "confirmed", "moving"):
        raise ValueError("Invalid static_status")
    if acquisition["static_status"] == "confirmed" and not acquisition["static_evidence"]:
        raise ValueError("Confirmed static acquisition requires evidence")
    for key in ("static_evidence", "wire_material", "wire_orientation", "sensor_settings",
                "background_pairing_evidence", "notes"):
        if acquisition[key] is not None and not isinstance(acquisition[key], str):
            raise ValueError(f"{key} must be text or null")
    for key in ("target_distance_m", "wire_diameter_mm"):
        if acquisition[key] is not None:
            _number(acquisition[key], key, positive=True)
    return cfg


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    return validate_config(json.loads(Path(path).read_text("utf-8")))


def in_roi(xyz: np.ndarray, roi) -> np.ndarray:
    box = np.asarray(roi, dtype=float)
    return np.all((xyz >= box[::2]) & (xyz <= box[1::2]), axis=1)


def reference_distances(xyz: np.ndarray, reference: dict) -> np.ndarray:
    start, end = np.asarray(reference["endpoints_sensor_m"], dtype=float)
    direction = end - start
    along = np.clip((xyz - start) @ direction / (direction @ direction), 0.0, 1.0)
    return np.linalg.norm(xyz - (start + along[:, None] * direction), axis=1)


@dataclass(frozen=True)
class Packet:
    index: int
    frame_index: int
    local_index: int
    device_time: float | None
    capture_time: float
    segment: int | None
    boundary_reason: str | None


def packet_timeline(scan, cfg: dict) -> list[Packet]:
    packets = []
    previous = None
    segment = -1
    for frame in scan.frames:
        for local, (device, captured) in enumerate(zip(frame["packet_device_times"], frame["packet_capture_times"])):
            if device is None or not np.isfinite(device):
                packets.append(Packet(len(packets), frame["index"], local, None, captured, None, "invalid_device_time"))
                previous = None
                continue
            reason = None
            if previous is None:
                reason = "capture_start" if not packets else "after_invalid_device_time"
            elif device < previous:
                reason = "device_backwards"
            elif device - previous > cfg["max_packet_gap_s"]:
                reason = "device_gap"
            if reason:
                segment += 1
            packets.append(Packet(len(packets), frame["index"], local, device, captured, segment, reason))
            previous = device
    return packets


def select_window(packets: list[Packet], end_frame: int, duration: float | None, cfg: dict):
    end_packets = [packet for packet in packets if packet.frame_index == end_frame]
    if not end_packets:
        raise ValueError(f"End frame {end_frame} does not exist")
    end = end_packets[-1]
    left = None
    partial = False
    if duration is None:
        selected = end_packets
        quality = "ok"
        if any(packet.device_time is None for packet in selected):
            quality = "invalid_device_time"
        elif len({packet.segment for packet in selected}) != 1:
            quality = "mixed_time_segments"
    elif end.device_time is None:
        selected, quality, partial = [], "invalid_end_time", True
    else:
        left = end.device_time - duration
        segment = [packet for packet in packets[:end.index + 1] if packet.segment == end.segment]
        selected = [packet for packet in segment if packet.device_time > left]
        partial = segment[0].device_time > left + cfg["time_epsilon_s"]
        quality = "partial_window" if partial else "ok"
    count = len(selected) * 200
    if count > cfg["max_window_points"]:
        raise ValueError(f"Window needs {count} rays, above max_window_points; no truncation performed")
    valid_times = [packet.device_time for packet in selected if packet.device_time is not None]
    span = valid_times[-1] - valid_times[0] if valid_times and quality not in ("invalid_device_time", "mixed_time_segments") else None
    segment_start = next((p for p in packets if p.segment == end.segment), None) if end.segment is not None else None
    metadata = {
        "end_frame": end_frame, "requested_duration_s": duration,
        "window_kind": "single_frame" if duration is None else "device_time",
        "requested_start_time_exclusive": left, "end_device_time": end.device_time,
        "actual_start_device_time": valid_times[0] if valid_times else None,
        "actual_end_device_time": valid_times[-1] if valid_times else None,
        "actual_span_s": span, "time_quality": quality, "partial_window": partial,
        "time_resolution": "packet_device_time_repeated_per_ray",
        "packet_count": len(selected), "frame_count": len({p.frame_index for p in selected}),
        "first_frame": selected[0].frame_index if selected else None,
        "first_packet_index": selected[0].index if selected else None,
        "last_packet_index": selected[-1].index if selected else None,
        "segment_start_reason": segment_start.boundary_reason if segment_start else None,
        "missing_device_packets": None,
        "equal_adjacent_device_times": sum(a.device_time is not None and a.device_time == b.device_time
                                           for a, b in zip(selected, selected[1:])),
    }
    return selected, metadata


def decode_window(path: Path, scan, packets: list[Packet], cfg: dict) -> dict[str, np.ndarray]:
    buffers = {key: [] for key in ("points", "codes", "frame_indices", "point_indices",
                                  "packet_indices", "device_times", "capture_times")}
    grouped = {}
    for packet in packets:
        grouped.setdefault(packet.frame_index, []).append(packet)
    with path.open("rb") as source:
        for frame_index, selected in grouped.items():
            frame = scan.frames[frame_index]
            points = decoder.decode_frame(path, frame, install_direction=cfg["install_direction"], source=source)
            codes = decoder.decode_frame_codes(path, frame, source=source)
            indices = np.concatenate([np.arange(p.local_index * 200, (p.local_index + 1) * 200, dtype=np.int32) for p in selected])
            buffers["points"].append(points[indices])
            buffers["codes"].append(codes[indices])
            buffers["frame_indices"].append(np.full(len(indices), frame_index, dtype=np.int32))
            buffers["point_indices"].append(indices)
            buffers["packet_indices"].append(np.repeat(np.array([p.index for p in selected], dtype=np.int64), 200))
            buffers["device_times"].append(np.repeat(np.array([p.device_time if p.device_time is not None else np.nan for p in selected]), 200))
            buffers["capture_times"].append(np.repeat(np.array([p.capture_time for p in selected]), 200))
    dtypes = {"points": np.float32, "codes": np.uint16, "frame_indices": np.int32,
              "point_indices": np.int32, "packet_indices": np.int64,
              "device_times": np.float64, "capture_times": np.float64}
    arrays = {key: np.concatenate(chunks) if chunks else np.empty((0, 4) if key in ("points", "codes") else (0,), dtype=dtypes[key])
              for key, chunks in buffers.items()}
    valid = (arrays["codes"][:, 0] != 0) & np.isfinite(arrays["points"]).all(axis=1)
    arrays["valid_mask"] = valid
    arrays["direction_known_mask"] = direction_known_from_codes(arrays["codes"])
    xyz = arrays["points"][:, :3]
    target = valid & in_roi(xyz, cfg["target_roi"]) if cfg["target_roi"] is not None else np.zeros(len(valid), bool)
    background = np.zeros(len(valid), bool)
    for box in cfg["background_rois"]:
        background |= valid & in_roi(xyz, box)
    corridor = np.zeros(len(valid), bool)
    if cfg["target_reference"] is not None:
        corridor[target] = reference_distances(xyz[target], cfg["target_reference"]) <= cfg["target_reference"]["corridor_radius_m"]
    arrays.update(target_roi_mask=target, background_roi_mask=background, reference_corridor_mask=corridor)
    return arrays


def distribution(values: np.ndarray):
    values = values[np.isfinite(values)]
    if not len(values):
        return None
    return dict(zip(("min", "p05", "p50", "p95", "max"), np.quantile(values, [0, .05, .5, .95, 1]).tolist()))


def point_stats(arrays, mask, end_time=None):
    times = arrays["device_times"][mask]
    first_time = float(times[0]) if len(times) and np.isfinite(times[0]) else None
    last_time = float(times[-1]) if len(times) and np.isfinite(times[-1]) else None
    return {
        "point_count": int(mask.sum()),
        "source_frame_count": int(len(np.unique(arrays["frame_indices"][mask]))),
        "source_packet_count": int(len(np.unique(arrays["packet_indices"][mask]))),
        "first_packet_device_time": first_time,
        "last_packet_device_time": last_time,
        "latest_source_age_s": end_time - last_time if end_time is not None and last_time is not None else None,
        "raw_intensity": distribution(arrays["points"][mask, 3]),
        "sensor_distance_m": distribution(np.linalg.norm(arrays["points"][mask, :3], axis=1)),
    }


def window_metrics(arrays, cfg, end_time=None):
    valid, target = arrays["valid_mask"], arrays["target_roi_mask"]
    reference = cfg["target_reference"]
    return {
        "ray_count": len(valid), "valid_return_count": int(valid.sum()),
        "direction_known_count": int(arrays["direction_known_mask"].sum()),
        "direction_unknown_count": int((~arrays["direction_known_mask"]).sum()),
        "zero_distance_count": int((arrays["codes"][:, 0] == 0).sum()),
        "nonfinite_count": int((~np.isfinite(arrays["points"]).all(axis=1)).sum()),
        "valid_outside_rois_count": int((valid & ~target & ~arrays["background_roi_mask"]).sum()),
        "all_valid": point_stats(arrays, valid, end_time),
        "target_roi": point_stats(arrays, target, end_time) if cfg["target_roi"] is not None else None,
        "background_rois": [point_stats(arrays, valid & in_roi(arrays["points"][:, :3], box), end_time) for box in cfg["background_rois"]],
        "reference_corridor": point_stats(arrays, arrays["reference_corridor_mask"], end_time) if reference else None,
        "target_roi_reference_distance_m": distribution(reference_distances(arrays["points"][target, :3], reference)) if reference else None,
        "confirmed_target_return_count": None,
        "wire_return_status": "NOT_EVALUATED",
    }


def packet_metrics(arrays, packets, window_id):
    for i, packet in enumerate(packets):
        part = slice(i * 200, (i + 1) * 200)
        yield {"window_id": window_id, **asdict(packet), "ray_count": 200,
               **{key: int(arrays[key][part].sum()) for key in
                  ("valid_mask", "direction_known_mask", "target_roi_mask", "background_roi_mask", "reference_corridor_mask")}}


def evidence_gaps(cfg, has_background):
    acquisition = cfg["acquisition"]
    gaps = []
    if cfg["target_roi"] is None:
        gaps.append("target_roi_missing")
    if cfg["target_reference"] is None or cfg["target_reference"]["review_status"] != "reviewed":
        gaps.append("independent_target_position_not_reviewed")
    if acquisition["static_status"] != "confirmed":
        gaps.append("fixed_sensor_and_target_not_confirmed")
    if acquisition["capture_role"] != "target_present":
        gaps.append("target_presence_not_confirmed")
    if acquisition["wire_diameter_mm"] is None:
        gaps.append("wire_diameter_missing")
    if not has_background or not acquisition["background_pairing_evidence"]:
        gaps.append("same_site_background_pair_not_verified")
    gaps.extend(["independent_repeat_recordings_require_review", "target_attribution_requires_review"])
    return gaps


def write_report(output: Path, manifest: dict, rows: list[dict]) -> None:
    lines = ["# W1 原始回波诊断", "", f"数据标签：{manifest['config']['acquisition']['data_label']}。",
             "", "**工具已输出原始数据；细线回波归属 NOT_EVALUATED。ROI/参考走廊内点不自动认定为目标回波。**",
             "", "原始 NPZ 未经过距离/高度/强度过滤或体素降采样；零距离射线保留，须结合 valid_mask 使用。",
             "", "| 输入/窗口 | 请求秒数 | 实际跨度/s | 时间质量 | 有效点 | 目标ROI点 | 参考走廊点 |",
             "|---|---:|---:|---|---:|---:|---:|"]
    for row in rows:
        def count(key):
            return row[key]["point_count"] if row[key] is not None else "未配置"
        span = row["actual_span_s"]
        span_label = f"{span:.6f}" if span is not None else "未知"
        lines.append(f"| {row['window_id']} | {row['requested_duration_s'] if row['requested_duration_s'] is not None else '单帧'} | "
                     f"{span_label} | {row['time_quality']} | {row['valid_return_count']} | {count('target_roi')} | {count('reference_corridor')} |")
    lines += ["", "## 证据缺口", ""] + [f"- {gap}" for gap in manifest["evidence_gaps"]]
    lines += ["", "## 来源与查看", "", "- manifest.json：输入/源码/config 哈希、完整命令、环境、扫描质量和全部产物路径。",
              "- window_metrics.jsonl / packet_metrics.jsonl：窗口及逐包原始统计；重叠窗不可作为独立试验累加。",
              "- raw_windows/：完整射线、原始码、包时间、原始帧/点索引与掩码；allow_pickle=False 读取。",
              "- figures/：全云/局部三视图与逐包计数；图形抽样不改变 NPZ 或统计。",
              "", "单个 100 包帧不等于完整扫描；同包所有射线共享包时间。时间跳变会分段，缺包数未知时保留 null。",
              "背景 PCAP 按自身设备时间独立选窗；配对条件由现场证据确认，不直接用计数差认定目标。",
              "", "下一步：核实目标位置、静止及背景配对，并在重复录制上复核回波归属。未完成前保持 W1 未验收。", ""]
    (output / "REPORT.md").write_text("\n".join(lines), "utf-8")


def run_audit(pcap: Path, output: Path, cfg: dict, end_frames=None, *, background_pcap=None,
              background_end_frames=None, command=None, render=True):
    pcap, output = Path(pcap).resolve(), Path(output).resolve()
    inputs = [(pcap, "primary", end_frames)]
    if background_pcap is not None:
        background = Path(background_pcap).resolve()
        if background == pcap:
            raise ValueError("Background PCAP must be an independent recording")
        inputs.append((background, "background", background_end_frames))
    elif background_end_frames:
        raise ValueError("background_end_frames requires background_pcap")
    for path, _, _ in inputs:
        if not path.is_file():
            raise ValueError(f"PCAP not found: {path}")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": SCHEMA, "status": "RUNNING", "wire_return_status": "NOT_EVALUATED",
        "config": cfg, "config_sha256": hashlib.sha256(json.dumps(cfg, sort_keys=True, allow_nan=False).encode()).hexdigest(),
        "command": command or [sys.executable, *sys.argv],
        "environment": {"python": sys.version, "platform": platform.platform(), "numpy": np.__version__,
                        "matplotlib": importlib.metadata.version("matplotlib") if render else None},
        "coordinate_frame": "lidar_link", "decoder_version": decoder.DECODER_VERSION,
        "source_hashes": {}, "inputs": [], "windows": [],
        "evidence_gaps": evidence_gaps(cfg, background_pcap is not None),
    }
    for source in (Path(__file__), Path(decoder.__file__),
                   ROOT / "packages/na70_core/na70_core/geometry.py",
                   ROOT / "packages/na70_core/na70_core/observations.py",
                   ROOT / "packages/na70_core/na70_core/__init__.py",
                   ROOT / "packages/na70_core/pyproject.toml",
                   ROOT / "web/backend/thin_wire_plot.py",
                   ROOT / "tools/audit_thin_wire.py", DEFAULT_CONFIG,
                   ROOT / "ROS2/src/double_wedge_driver/src/inno_device_lidar_node.cpp",
                   ROOT / "ROS2/src/double_wedge_driver/src/test_lidar_driver.cpp"):
        if source.exists():
            manifest["source_hashes"][source.relative_to(ROOT).as_posix()] = sha256_file(source)
    rows = []
    write_json(output / "manifest.json", manifest)
    try:
        (output / "raw_windows").mkdir()
        (output / "figures").mkdir()
        with (output / "window_metrics.jsonl").open("w", encoding="utf-8") as row_file, (output / "packet_metrics.jsonl").open("w", encoding="utf-8") as packet_file:
            for path, role, ends in inputs:
                capture_hash = sha256_file(path)
                if any(entry["sha256"] == capture_hash for entry in manifest["inputs"]):
                    raise ValueError("Background content duplicates primary PCAP")
                scan = decoder.scan_pcap(path)
                packets = packet_timeline(scan, cfg)
                ends = [scan.frames[-1]["index"]] if ends is None else list(ends)
                if not ends or any(type(frame) is not int or frame < 0 for frame in ends) or len(set(ends)) != len(ends):
                    raise ValueError("End frames must be unique nonnegative integers")
                durations = ([None] if cfg["include_single_frame"] else []) + cfg["window_seconds"]
                if len(rows) + len(ends) * len(durations) > cfg["max_windows"]:
                    raise ValueError("Requested windows exceed max_windows")
                entry = {"role": role, "path": str(path), "sha256": capture_hash,
                         "capture_id": "pcap-" + capture_hash[:16], "frame_count": len(scan.frames),
                         "valid_packets": scan.valid_packets, "invalid_records": scan.invalid_packets,
                         "ignored_records": scan.ignored_packets, "warnings": scan.warnings,
                         "incomplete_frames": sum(not f["complete"] for f in scan.frames),
                         "missing_device_packets": None,
                         "time_boundaries": [asdict(p) for p in packets if p.boundary_reason]}
                manifest["inputs"].append(entry)
                write_json(output / "manifest.json", manifest)
                for end in ends:
                    for duration in durations:
                        selected, metadata = select_window(packets, end, duration, cfg)
                        window_id = f"{role}-f{end:06d}-" + ("single" if duration is None else f"{duration:g}s")
                        arrays = decode_window(path, scan, selected, cfg)
                        raw_path = output / "raw_windows" / f"{window_id}.npz"
                        np.savez_compressed(raw_path, **arrays)
                        reliable_end_time = metadata["end_device_time"] if metadata["time_quality"] in ("ok", "partial_window") else None
                        row = {"schema_version": SCHEMA, "window_id": window_id, "input_role": role,
                               "capture_id": entry["capture_id"], "input_sha256": capture_hash,
                               "raw_file": raw_path.relative_to(output).as_posix(),
                               "raw_sha256": sha256_file(raw_path), **metadata, **window_metrics(arrays, cfg, reliable_end_time)}
                        packet_rows = list(packet_metrics(arrays, selected, window_id))
                        if render:
                            from .thin_wire_plot import render_window
                            row["figures"] = render_window(arrays, cfg, row, packet_rows, output)
                        else:
                            row["figures"] = []
                        row_file.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                        row_file.flush()
                        for packet_row in packet_rows:
                            packet_file.write(json.dumps(packet_row, allow_nan=False) + "\n")
                        rows.append(row)
                        manifest["windows"].append({"window_id": window_id, "raw_file": row["raw_file"], "figures": row["figures"]})
                        print(f"{window_id}: {row['valid_return_count']}/{row['ray_count']} valid, {row['time_quality']}", flush=True)
        manifest["status"] = "COMPLETED"
        manifest["window_count"] = len(rows)
        write_report(output, manifest, rows)
        manifest["artifacts"] = {file.relative_to(output).as_posix(): sha256_file(file)
                                 for file in sorted(output.rglob("*")) if file.is_file() and file.name != "manifest.json"}
        write_json(output / "manifest.json", manifest)
        return manifest
    except Exception as error:
        manifest["status"] = "FAILED"
        manifest["error"] = f"{type(error).__name__}: {error}"
        write_json(output / "manifest.json", manifest)
        raise

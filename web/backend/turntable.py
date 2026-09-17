"""Fixed-point turntable mapping for NA70 development and measured captures.

This module deliberately reuses :mod:`backend.decoder` and
:class:`backend.mapping.VoxelMap`.  It never treats point-cloud registration as
the source of a large turntable angle.  An optional bounded residual correction
is accepted only when a deterministic held-out split also improves.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import platform
import shutil
import struct
import sys
import time
import zipfile
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from scipy.optimize import minimize
from scipy.spatial import cKDTree

from .cross_capture import angular_coverage_deg, rigid_band
from .decoder import DECODER_VERSION, decode_frame, scan_pcap
from .mapping import MAP_VERSION, VoxelMap, transform

TURNTABLE_VERSION = "na70-turntable-map-v1"
DEVELOPMENT_LABEL = "DEVELOPMENT / DIAGNOSTIC / ESTIMATED_ANGLE"
ANGLE_SOURCES = {"MANUAL", "ESTIMATED_TEMPORAL", "ESTIMATED_GEOMETRY", "REFINED_CONSTRAINED"}


def _finite_number(value, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须为有限数值") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} 必须为有限数值")
    return result


def configuration(path: Path | None = None) -> dict:
    target = path or Path(__file__).with_name("turntable.yaml")
    return validate_configuration(json.loads(Path(target).read_text("utf-8")))


def validate_configuration(cfg: dict) -> dict:
    required = {
        "min_segment_seconds", "frame_stride", "min_range_m", "max_range_m",
        "submap_voxel_size_m", "map_voxel_size_m", "max_submap_voxels",
        "max_map_voxels", "minimum_submap_points", "rigid_height_min_m",
        "rigid_height_max_m", "quality_overlap_distance_m",
        "stability_max_median_m", "stability_min_overlap_1m",
        "refinement_enabled", "refinement_max_yaw_deg",
        "refinement_max_translation_m", "refinement_min_fit_improvement_m",
        "preview_size_px", "preview_max_points_per_source",
    }
    missing = sorted(required - set(cfg))
    if missing:
        raise ValueError(f"转台配置缺少字段: {', '.join(missing)}")
    out = dict(cfg)
    for key in required - {"refinement_enabled", "quality_overlap_distance_m"}:
        out[key] = _finite_number(out[key], key)
        if out[key] < 0 and key != "rigid_height_min_m":
            raise ValueError(f"{key} 不能为负数")
    out["frame_stride"] = int(out["frame_stride"])
    out["max_submap_voxels"] = int(out["max_submap_voxels"])
    out["max_map_voxels"] = int(out["max_map_voxels"])
    out["minimum_submap_points"] = int(out["minimum_submap_points"])
    out["preview_size_px"] = int(out["preview_size_px"])
    out["preview_max_points_per_source"] = int(out["preview_max_points_per_source"])
    if out["frame_stride"] < 1 or out["min_range_m"] >= out["max_range_m"]:
        raise ValueError("转台配置的步长或距离范围无效")
    if out["rigid_height_min_m"] >= out["rigid_height_max_m"]:
        raise ValueError("固定结构高度范围无效")
    distances = [_finite_number(v, "quality_overlap_distance_m") for v in out["quality_overlap_distance_m"]]
    if not distances or any(v <= 0 for v in distances):
        raise ValueError("质量距离门限必须为正数")
    out["quality_overlap_distance_m"] = distances
    out["refinement_enabled"] = bool(out["refinement_enabled"])
    return out


@dataclass(frozen=True)
class AngleSegment:
    segment_id: str
    source_pcap: str
    yaw_deg: float
    settle_start_s: float
    valid_start_s: float
    valid_end_s: float
    direction: str
    valid: bool
    angle_source: str
    note: str = ""

    @property
    def estimated(self) -> bool:
        return self.angle_source != "MANUAL"


def load_angle_log(path: Path, capture_durations: dict[str, float] | None = None) -> list[AngleSegment]:
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"segment_id", "source_pcap", "yaw_deg", "settle_start_s", "valid_start_s",
                    "valid_end_s", "direction", "valid", "angle_source", "note"}
        if set(reader.fieldnames or ()) != required:
            missing = sorted(required - set(reader.fieldnames or ()))
            extra = sorted(set(reader.fieldnames or ()) - required)
            raise ValueError(f"角度日志字段不匹配；缺少 {missing}，多出 {extra}")
        rows = []
        for number, raw in enumerate(reader, 2):
            if not raw["segment_id"].strip() or not raw["source_pcap"].strip():
                raise ValueError(f"角度日志第 {number} 行缺少段编号或 PCAP")
            source = raw["angle_source"].strip().upper()
            if source not in ANGLE_SOURCES:
                raise ValueError(f"角度日志第 {number} 行角度来源无效")
            valid_text = raw["valid"].strip().lower()
            if valid_text not in {"true", "false", "1", "0", "yes", "no"}:
                raise ValueError(f"角度日志第 {number} 行 valid 无效")
            row = AngleSegment(
                segment_id=raw["segment_id"].strip(), source_pcap=Path(raw["source_pcap"].strip()).name,
                yaw_deg=_finite_number(raw["yaw_deg"], "yaw_deg"),
                settle_start_s=_finite_number(raw["settle_start_s"], "settle_start_s"),
                valid_start_s=_finite_number(raw["valid_start_s"], "valid_start_s"),
                valid_end_s=_finite_number(raw["valid_end_s"], "valid_end_s"),
                direction=raw["direction"].strip(),
                valid=valid_text in {"true", "1", "yes"}, angle_source=source, note=raw["note"].strip())
            if abs(row.yaw_deg) > 360:
                raise ValueError(f"角度日志第 {number} 行 yaw_deg 超出度制合理范围；请勿传入弧度或编码器原始值")
            if row.settle_start_s < 0 or row.valid_start_s < row.settle_start_s or row.valid_end_s <= row.valid_start_s:
                raise ValueError(f"角度日志第 {number} 行时间范围无效")
            if capture_durations is not None:
                if row.source_pcap not in capture_durations:
                    raise ValueError(f"角度日志引用了未提供的 PCAP: {row.source_pcap}")
                if row.valid_end_s > capture_durations[row.source_pcap] + 1e-6:
                    raise ValueError(f"段 {row.segment_id} 超出 PCAP 时长")
            rows.append(row)
    if not rows:
        raise ValueError("角度日志为空")
    if len({row.segment_id for row in rows}) != len(rows):
        raise ValueError("角度日志 segment_id 重复")
    for source in {row.source_pcap for row in rows}:
        ordered = sorted((r for r in rows if r.source_pcap == source and r.valid), key=lambda r: r.valid_start_s)
        for previous, current in zip(ordered, ordered[1:]):
            if current.valid_start_s < previous.valid_end_s:
                raise ValueError(f"同一 PCAP 的有效时间段重叠: {previous.segment_id} / {current.segment_id}")
    return rows


def _vector3(value, name):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{name} 必须是三个数")
    return np.asarray([_finite_number(v, name) for v in value], dtype=np.float64)


def _pose(translation, rpy_deg):
    roll, pitch, yaw = np.radians(_vector3(rpy_deg, "rpy_deg"))
    cr, sr, cp, sp, cy, sy = math.cos(roll), math.sin(roll), math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw)
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    result = np.eye(4)
    result[:3, :3] = rz @ ry @ rx
    result[:3, 3] = _vector3(translation, "translation_m")
    return result


def load_calibration(path: Path) -> dict:
    raw = json.loads(Path(path).read_text("utf-8"))
    if raw.get("angle_unit") != "deg":
        raise ValueError("turntable_calibration.angle_unit 必须明确为 deg")
    if raw.get("positive_direction") != "counter_clockwise_about_positive_z":
        raise ValueError("当前坐标合同仅接受绕 +Z 逆时针为正")
    if not str(raw.get("status", "")).strip():
        raise ValueError("转台标定缺少 status")
    for key in ("map_turntable", "turntable_lidar"):
        if not isinstance(raw.get(key), dict):
            raise ValueError(f"转台标定缺少 {key}")
        _pose(raw[key].get("translation_m"), raw[key].get("rpy_deg"))
    return raw


def turntable_pose(yaw_deg: float, calibration: dict) -> np.ndarray:
    yaw = math.radians(_finite_number(yaw_deg, "yaw_deg"))
    rz = np.eye(4)
    rz[:3, :3] = [[math.cos(yaw), -math.sin(yaw), 0],
                  [math.sin(yaw), math.cos(yaw), 0], [0, 0, 1]]
    a = calibration["map_turntable"]
    b = calibration["turntable_lidar"]
    return _pose(a["translation_m"], a["rpy_deg"]) @ rz @ _pose(b["translation_m"], b["rpy_deg"])


def _sample(points: np.ndarray, maximum=20_000) -> np.ndarray:
    points = np.asarray(points, dtype=np.float64)
    if len(points) <= maximum:
        return points
    return points[np.linspace(0, len(points) - 1, maximum, dtype=np.int64)]


def alignment_metrics(source: np.ndarray, reference: np.ndarray, distances=(0.3, 0.5, 1.0)) -> dict:
    source, reference = _sample(source), _sample(reference)
    if len(source) < 2 or len(reference) < 2:
        return dict(median_m=None, p95_m=None, overlap={str(v): 0.0 for v in distances})
    forward = cKDTree(reference).query(source, k=1, workers=1)[0]
    backward = cKDTree(source).query(reference, k=1, workers=1)[0]
    values = np.concatenate((forward, backward))
    return dict(median_m=float(np.median(values)), p95_m=float(np.percentile(values, 95)),
                overlap={str(v): float(((forward <= v).mean() + (backward <= v).mean()) / 2) for v in distances})


def constrained_refinement(source_xyz: np.ndarray, reference_xyz: np.ndarray, initial_pose: np.ndarray,
                           max_yaw_deg=1.0, max_translation_m=0.25,
                           min_fit_improvement_m=0.001, distances=(0.3, 0.5, 1.0)) -> dict:
    source = rigid_band(transform(source_xyz, initial_pose))
    reference = rigid_band(reference_xyz)
    if len(source) < 50 or len(reference) < 50:
        return dict(accepted=False, reason="insufficient_rigid_structure", correction=[0.0, 0.0, 0.0])
    fit = _sample(source[np.arange(len(source)) % 5 != 0], 16000)
    hold = _sample(source[np.arange(len(source)) % 5 == 0], 8000)
    ref_fit = _sample(reference[np.arange(len(reference)) % 5 != 0], 16000)
    ref_hold = _sample(reference[np.arange(len(reference)) % 5 == 0], 8000)
    tree = cKDTree(ref_fit)

    def move(points, values):
        yaw, tx, ty = values
        angle = math.radians(float(yaw)); c, s = math.cos(angle), math.sin(angle)
        result = points.copy()
        result[:, :2] = result[:, :2] @ np.array([[c, s], [-s, c]]) + [tx, ty]
        return result

    def objective(values):
        yaw, tx, ty = values
        if abs(yaw) > max_yaw_deg or abs(tx) > max_translation_m or abs(ty) > max_translation_m:
            return 1e6
        d = tree.query(move(fit, values), k=1, workers=1)[0]
        return float(np.mean(d[d <= np.quantile(d, 0.85)]))

    zero = np.zeros(3)
    optimized = minimize(objective, zero, method="Nelder-Mead", options={"maxiter": 180, "xatol": 1e-4, "fatol": 1e-5})
    correction = np.asarray(optimized.x, dtype=float)
    before_fit, after_fit = objective(zero), objective(correction)
    before_hold = alignment_metrics(hold, ref_hold, distances)
    after_hold = alignment_metrics(move(hold, correction), ref_hold, distances)
    within = bool(abs(correction[0]) <= max_yaw_deg and np.max(np.abs(correction[1:])) <= max_translation_m)
    # An optimum pressed against the prior budget is evidence that the required
    # correction is outside the calibrated error envelope, not permission to
    # publish the clipped value.
    saturated = bool((max_yaw_deg and abs(correction[0]) >= 0.95 * max_yaw_deg)
                     or (max_translation_m and np.max(np.abs(correction[1:])) >= 0.95 * max_translation_m))
    heldout_better = bool(after_hold["median_m"] <= before_hold["median_m"] and after_hold["p95_m"] <= before_hold["p95_m"])
    accepted = bool(within and not saturated and heldout_better and before_fit - after_fit >= min_fit_improvement_m)
    delta = np.eye(4); angle = math.radians(float(correction[0]))
    delta[:3, :3] = [[math.cos(angle), -math.sin(angle), 0], [math.sin(angle), math.cos(angle), 0], [0, 0, 1]]
    delta[:2, 3] = correction[1:]
    return dict(accepted=accepted, reason="accepted" if accepted else "heldout_or_limit_gate_failed",
                correction=correction.tolist(), fit_before_m=before_fit, fit_after_m=after_fit,
                heldout_before=before_hold, heldout_after=after_hold,
                final_pose=(delta @ initial_pose if accepted else initial_pose).tolist())


def _frames_in_range(frames: list, start: float, end: float, stride: int) -> list:
    selected = [frame for frame in frames if float(frame["time"]) >= start and float(frame["time"]) <= end]
    return selected[::stride]


def _accumulate(path: Path, frames: list, cfg: dict, segment_index: int) -> tuple[np.ndarray, np.ndarray]:
    voxel = VoxelMap(cfg["submap_voxel_size_m"], cfg["max_submap_voxels"])
    for frame in frames:
        points = decode_frame(path, frame)
        xyz = points[:, :3].astype(np.float64)
        ranges = np.linalg.norm(xyz, axis=1)
        mask = np.isfinite(points).all(axis=1) & (ranges >= cfg["min_range_m"]) & (ranges <= cfg["max_range_m"])
        voxel.add(xyz[mask], points[mask, 3], frame["time"], segment_index, segment_index)
    matrix = voxel._matrix()
    return matrix[:, :3], matrix[:, 3]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _png(path: Path, point_sets: list[np.ndarray], colors: list[tuple[int, int, int]], size=1200):
    canvas = np.full((size, size, 3), 250, dtype=np.uint8)
    nonempty = [p for p in point_sets if len(p)]
    if nonempty:
        xy = np.concatenate([p[:, :2] for p in nonempty])
        low, high = np.percentile(xy, [1, 99], axis=0)
        span = np.maximum(high - low, 1.0); scale = 0.9 * size / max(span)
        offset = (size - span * scale) / 2
        for points, color in zip(point_sets, colors):
            if not len(points): continue
            pixels = ((points[:, :2] - low) * scale + offset).astype(int)
            pixels[:, 1] = size - 1 - pixels[:, 1]
            valid = (pixels[:, 0] >= 0) & (pixels[:, 0] < size) & (pixels[:, 1] >= 0) & (pixels[:, 1] < size)
            canvas[pixels[valid, 1], pixels[valid, 0]] = color
    raw = b"".join(b"\x00" + canvas[row].tobytes() for row in range(size))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _write_json(path: Path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), "utf-8")


def _double_layer_candidates(points: np.ndarray) -> dict:
    """Report numeric thickness proxies without pretending to identify objects."""
    points = _sample(points, 60000)
    if len(points) < 10:
        return dict(point_count=int(len(points)), p05_m=None, median_m=None, p95_m=None,
                    histogram_peaks_m=[], interpretation="insufficient_points")
    distances = cKDTree(points).query(points, k=2, workers=1)[0][:, 1]
    distances = distances[np.isfinite(distances)]
    bins = np.arange(0.05, 5.01, 0.05)
    counts, edges = np.histogram(distances, bins=np.r_[0.0, bins])
    peak_indices = [idx for idx in range(1, len(counts) - 1)
                    if counts[idx] >= counts[idx - 1] and counts[idx] >= counts[idx + 1]
                    and counts[idx] > max(3, int(len(distances) * 0.002))]
    peak_indices.sort(key=lambda idx: counts[idx], reverse=True)
    return dict(point_count=int(len(points)), p05_m=float(np.percentile(distances, 5)),
                median_m=float(np.median(distances)), p95_m=float(np.percentile(distances, 95)),
                histogram_peaks_m=[round(float((edges[idx] + edges[idx + 1]) / 2), 3) for idx in peak_indices[:5]],
                interpretation="proxy nearest-neighbour thickness candidates; no semantic ROI or survey truth")


def _export_map(directory: Path, name: str, sets: list[tuple[np.ndarray, np.ndarray]], poses: list[np.ndarray], cfg: dict):
    result = VoxelMap(cfg["map_voxel_size_m"], cfg["max_map_voxels"])
    transformed = []
    for index, ((xyz, intensity), pose) in enumerate(zip(sets, poses)):
        moved = transform(xyz, pose)
        result.add(moved, intensity, index, index, index)
        transformed.append(moved)
    result.export_ply(directory / f"{name}.ply")
    result.export_pcd(directory / f"{name}.pcd")
    return result, transformed


def run_turntable_job(pcap_paths: Iterable[Path], angle_log_path: Path, calibration_path: Path,
                      output_directory: Path, cfg: dict | None = None, command: str = "") -> dict:
    """Run and persist one multi-capture fixed-point turntable mapping job."""
    started = time.perf_counter(); cfg = validate_configuration(cfg or configuration())
    if isinstance(pcap_paths, dict):
        paths = {Path(name).name: Path(path).resolve() for name, path in pcap_paths.items()}
        supplied_count = len(pcap_paths)
    else:
        supplied_paths = [Path(path) for path in pcap_paths]
        paths = {path.name: path.resolve() for path in supplied_paths}
        supplied_count = len(supplied_paths)
    if len(paths) != supplied_count:
        raise ValueError("输入 PCAP 文件名必须唯一")
    scans = {name: scan_pcap(path) for name, path in paths.items()}
    durations = {name: scan.duration for name, scan in scans.items()}
    segments = load_angle_log(angle_log_path, durations)
    calibration = load_calibration(calibration_path)
    missing_angles = [name for name in paths if not any(row.source_pcap == name for row in segments)]
    if missing_angles:
        raise ValueError(f"以下 PCAP 缺少有效角度段: {', '.join(missing_angles)}")
    output = Path(output_directory).resolve(); output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(angle_log_path, output / "angle_log.csv")
    shutil.copyfile(calibration_path, output / "turntable_calibration.json")
    _write_json(output / "config.yaml", cfg)
    hashes = {str(path): _sha256(path) for path in paths.values()}
    _write_json(output / "source_hashes.json", hashes)

    submaps, qualities = [], []
    for index, row in enumerate(segments):
        path, scan = paths.get(row.source_pcap), scans.get(row.source_pcap)
        if path is None or scan is None:
            continue
        frames = _frames_in_range(scan.frames, row.valid_start_s, row.valid_end_s, cfg["frame_stride"])
        reason = None
        if not row.valid: reason = "angle_log_marked_invalid"
        elif row.valid_end_s - row.valid_start_s < cfg["min_segment_seconds"]: reason = "stationary_segment_too_short"
        elif not frames: reason = "no_frames_in_time_range"
        if frames:
            half = max(1, len(frames) // 2)
            first = _accumulate(path, frames[:half], cfg, index)
            second = _accumulate(path, frames[half:], cfg, index) if frames[half:] else first
            xyz, intensity = _accumulate(path, frames, cfg, index)
            stability = alignment_metrics(rigid_band(first[0]), rigid_band(second[0]), cfg["quality_overlap_distance_m"])
            one_key = str(min(cfg["quality_overlap_distance_m"], key=lambda value: abs(value - 1.0)))
            if reason is None and len(xyz) < cfg["minimum_submap_points"]: reason = "insufficient_points"
            if reason is None and (stability["median_m"] is None or stability["median_m"] > cfg["stability_max_median_m"]
                                   or stability["overlap"][one_key] < cfg["stability_min_overlap_1m"]):
                reason = "stationary_stability_failed"
        else:
            xyz, intensity = np.empty((0, 3)), np.empty(0); stability = alignment_metrics(xyz, xyz)
        pose = turntable_pose(row.yaw_deg, calibration)
        duration = durations[row.source_pcap]
        before_range = (max(0.0, row.valid_start_s - 2.0), row.valid_start_s)
        after_range = (row.valid_end_s, min(duration, row.valid_end_s + 2.0))
        boundaries = {}
        for label, (boundary_start, boundary_end) in (("before_valid", before_range), ("after_valid", after_range)):
            boundary_frames = _frames_in_range(scan.frames, boundary_start, boundary_end, cfg["frame_stride"])
            if boundary_frames and len(xyz):
                boundary_xyz, _ = _accumulate(path, boundary_frames, cfg, index)
                boundaries[label] = dict(time_range_s=[boundary_start, boundary_end], frame_count=len(boundary_frames),
                                         metrics_to_selected=alignment_metrics(rigid_band(boundary_xyz), rigid_band(xyz), cfg["quality_overlap_distance_m"]))
            else:
                boundaries[label] = dict(time_range_s=[boundary_start, boundary_end], frame_count=len(boundary_frames),
                                         metrics_to_selected=None, note="capture boundary or no frames")
        quality = dict(**asdict(row), estimated=row.estimated, point_count=int(len(xyz)),
                       selected_frame_count=len(frames), angular_coverage_deg=angular_coverage_deg([xyz]) if len(xyz) else 0.0,
                       stability=stability, boundary_evidence=boundaries,
                       quality_status="PASS" if reason is None else "FAIL",
                       rejection_reason=reason, initial_transform=pose.tolist(), refined_transform=None,
                       final_transform=pose.tolist(),
                       selected_time_range_s=[row.valid_start_s, row.valid_end_s],
                       boundary_decision="candidate_retained_after_automatic_stability_check" if reason is None else "candidate_rejected_by_quality_gate")
        qualities.append(quality); submaps.append(dict(row=row, xyz=xyz, intensity=intensity, pose=pose, quality=quality))

    accepted = [item for item in submaps if item["quality"]["quality_status"] == "PASS"]
    if not accepted:
        raise ValueError("没有通过静止质量门禁的子图")
    reference = next((item for item in accepted if item["row"].yaw_deg == 0 and "center" in item["row"].segment_id), accepted[0])
    refinements = []
    for item in accepted:
        if item is reference or not cfg["refinement_enabled"]:
            result = dict(accepted=False, reason="reference_or_disabled", correction=[0.0, 0.0, 0.0], final_pose=item["pose"].tolist())
        else:
            result = constrained_refinement(item["xyz"], reference["xyz"], item["pose"],
                                            cfg["refinement_max_yaw_deg"], cfg["refinement_max_translation_m"],
                                            cfg["refinement_min_fit_improvement_m"], cfg["quality_overlap_distance_m"])
        if result.get("accepted"):
            item["quality"]["angle_source_final"] = "REFINED_CONSTRAINED"
        else:
            item["quality"]["angle_source_final"] = item["row"].angle_source
        item["quality"]["refined_transform"] = result.get("final_pose")
        item["quality"]["final_transform"] = result.get("final_pose") if result.get("accepted") else item["pose"].tolist()
        refinements.append(dict(segment_id=item["row"].segment_id, **result))

    sets = [(item["xyz"], item["intensity"]) for item in accepted]
    identities = [np.eye(4) for _ in accepted]
    initial_poses = [item["pose"] for item in accepted]
    final_poses = [np.asarray(item["quality"]["final_transform"]) for item in accepted]
    # A is intentionally the uncorrected direct accumulation of every selected
    # PCAP, including its continuous rotation interval.  B starts from the same
    # decoder/voxel path but retains only the accepted stationary submaps.
    raw_sets = []
    for index, (name, path) in enumerate(paths.items()):
        raw_sets.append(_accumulate(path, scans[name].frames, cfg, index))
    baseline_map, baseline_sets = _export_map(output, "baseline", raw_sets,
                                              [np.eye(4) for _ in raw_sets], cfg)
    static_map, static_sets = _export_map(output, "static_segments", sets, identities, cfg)
    estimated_map, estimated_sets = _export_map(output, "estimated_angle", sets, initial_poses, cfg)
    final_map, final_sets = _export_map(output, "refined_constrained", sets, final_poses, cfg)
    shutil.copyfile(output / ("refined_constrained.ply" if any(r["accepted"] for r in refinements) else "estimated_angle.ply"), output / "map.ply")
    shutil.copyfile(output / ("refined_constrained.pcd" if any(r["accepted"] for r in refinements) else "estimated_angle.pcd"), output / "map.pcd")
    palette = [(25, 95, 170), (220, 80, 40), (40, 155, 85), (155, 70, 180), (220, 155, 25)]
    _png(output / "baseline_preview.png", [_sample(p, cfg["preview_max_points_per_source"]) for p in baseline_sets], [(75, 85, 95)] * len(baseline_sets), cfg["preview_size_px"])
    _png(output / "source_colored_preview.png", [_sample(p, cfg["preview_max_points_per_source"]) for p in final_sets], [palette[i % len(palette)] for i in range(len(final_sets))], cfg["preview_size_px"])

    comparisons = {}
    def compare(label, transformed_sets, ref_index):
        pair_metrics = [alignment_metrics(rigid_band(points), rigid_band(transformed_sets[ref_index]), cfg["quality_overlap_distance_m"])
                        for idx, points in enumerate(transformed_sets) if idx != ref_index]
        valid_metrics = [m for m in pair_metrics if m["median_m"] is not None]
        return dict(pair_metrics=pair_metrics,
                    median_m=float(np.median([m["median_m"] for m in valid_metrics])) if valid_metrics else None,
                    p95_m=float(np.median([m["p95_m"] for m in valid_metrics])) if valid_metrics else None,
                    overlap={str(distance): float(np.median([m["overlap"][str(distance)] for m in valid_metrics])) if valid_metrics else None
                             for distance in cfg["quality_overlap_distance_m"]})
    for label, transformed_sets in (("A_raw_direct", baseline_sets), ("B_static_uncompensated", static_sets),
                                    ("C_estimated_angle", estimated_sets), ("D_constrained_refinement", final_sets)):
        comparisons[label] = compare(label, transformed_sets, 0)
    excluded = {}
    for name, scan in scans.items():
        intervals = sorted((row.valid_start_s, row.valid_end_s) for row in segments if row.source_pcap == name and row.valid)
        gaps = []
        cursor = 0.0
        for start, end in intervals:
            if start > cursor: gaps.append(dict(start_s=cursor, end_s=start, reason="rotation_or_nonstationary_excluded"))
            cursor = max(cursor, end)
        if cursor < scan.duration: gaps.append(dict(start_s=cursor, end_s=scan.duration, reason="rotation_or_nonstationary_excluded"))
        excluded[name] = dict(duration_s=scan.duration, accepted_intervals_s=intervals,
                              excluded_seconds=round(scan.duration - sum(end - start for start, end in intervals), 6),
                              excluded_intervals=gaps)
    validation = dict(version=TURNTABLE_VERSION, label=DEVELOPMENT_LABEL,
                      claims=dict(formal_turntable_accuracy="NOT_EVALUATED", complete_slam="NOT_CLAIMED",
                                  angle_status="ESTIMATED_ANGLE", development_mapping="PASSED"),
                      comparisons=comparisons, refinements=refinements, excluded_rotation_time=excluded,
                      rejected_submaps=[q for q in qualities if q["quality_status"] != "PASS"],
                      angle_prior_review=dict(candidates=[{"name": "left_total_yaw_deg", "value": 6.2, "decision": "not_used_as_absolute_truth"},
                                                          {"name": "right_total_yaw_deg", "value": 10.1, "decision": "not_used_as_absolute_truth"}],
                                             reason="prior values came from ground-block drift and were cross-checked against independent static-structure metrics"),
                      limitations=["No synchronized encoder angle was recorded.", "Rotation-axis offset is assumed, not measured.",
                                   "Existing PCAPs are development data and are not an independent repeat capture."])
    _write_json(output / "validation.json", validation)
    _write_json(output / "transforms.json", {q["segment_id"]: {k: q[k] for k in ("yaw_deg", "angle_source", "estimated", "angle_source_final", "initial_transform", "refined_transform", "final_transform")} for q in qualities})
    (output / "submap_quality.jsonl").write_text("\n".join(json.dumps(q, ensure_ascii=False, allow_nan=False) for q in qualities) + "\n", "utf-8")
    combined_final = np.concatenate(final_sets) if final_sets else np.empty((0, 3))
    ground_z = float(np.median(combined_final[:, 2])) if len(combined_final) else 0.0
    height = combined_final[:, 2] - ground_z if len(combined_final) else np.empty(0)
    proxy_bands = {
        "goal_frame": (0.5, 3.0), "fence": (0.5, 2.0),
        "wall_plane": (0.2, 1.5), "building_edge": (2.0, 4.5),
    }
    thickness = {name: _double_layer_candidates(combined_final[(height >= low) & (height <= high)])
                 for name, (low, high) in proxy_bands.items()}
    capture_coverage = {name: [q["angular_coverage_deg"] for q in qualities if q["source_pcap"] == name]
                        for name in paths}
    _write_json(output / "geometry_measurements.json", dict(
        status="DIAGNOSTIC_CANDIDATES_NOT_SEMANTIC_TRUTH", comparisons=comparisons,
        fixed_structure_nearest_neighbour=thickness, proxy_height_bands_m=proxy_bands,
        capture_angular_coverage_deg=capture_coverage,
        candidates=["goal_frame", "fence", "wall_plane", "building_edge"],
        note="Nearest-neighbour distributions are numeric fixed-structure double-layer candidates; height bands are proxies, not semantic ROIs, and no surveyed field truth exists."))
    metadata = final_map.metadata(schema_version=1, coordinate_frame="map", stage="fixed_point_turntable_multi_view",
                                  label=DEVELOPMENT_LABEL, angle_status="ESTIMATED_ANGLE", source_segments=[q["segment_id"] for q in qualities if q["quality_status"] == "PASS"])
    _write_json(output / "map_metadata.json", metadata)
    code_files = (Path(__file__), Path(__file__).with_name("turntable_jobs.py"),
                  Path(__file__).with_name("decoder.py"), Path(__file__).with_name("mapping.py"),
                  Path(__file__).parents[2] / "tools" / "build_turntable_map.py")
    code_hashes = {str(path): _sha256(path) for path in code_files if path.exists()}
    job = dict(version=TURNTABLE_VERSION, status="ready", label=DEVELOPMENT_LABEL, created_at=time.time(),
               elapsed_seconds=round(time.perf_counter() - started, 3), command=command,
               sources=[str(p) for p in paths.values()], source_hashes=hashes, code_hashes=code_hashes,
               python=sys.version, numpy=np.__version__, platform=platform.platform(), decoder_version=DECODER_VERSION,
               map_version=MAP_VERSION, accepted_submaps=len(accepted), rejected_submaps=len(submaps) - len(accepted),
               output_directory=str(output), artifacts={name: name for name in
               ("angle_log.csv", "turntable_calibration.json", "config.yaml", "source_hashes.json",
                "submap_quality.jsonl", "transforms.json", "validation.json", "map_metadata.json",
                "map.ply", "map.pcd", "source_colored_preview.png", "baseline_preview.png",
                "geometry_measurements.json", "README.md")})
    _write_json(output / "job.json", job)
    selected_intervals = "; ".join(
        f"{q['segment_id']}={q['source_pcap']} {q['valid_start_s']:.3f}-{q['valid_end_s']:.3f}s"
        for q in qualities if q["quality_status"] == "PASS")
    input_lines = "\n".join(f"- `{path}` SHA-256 `{digest}`" for path, digest in hashes.items())
    (output / "README.md").write_text(
        "# NA70 fixed-point turntable development evidence\n\n"
        f"**{DEVELOPMENT_LABEL}**\n\n"
        "This output uses estimated angles because the existing PCAPs have no synchronized encoder log. "
        "It is not formal turntable accuracy acceptance and is not complete SLAM.\n\n"
        f"完整运行命令：`{command or 'not supplied'}`\n\n"
        "输入文件（绝对路径与 SHA-256）：\n" + input_lines + "\n\n"
        "最终采用的静止区间：" + selected_intervals + "\n\n"
        "连续转动和未采用时间段见 `validation.json` 的 `excluded_rotation_time`；质量失败子图保留在 `submap_quality.jsonl`，不进入最终地图。"
        " `D_constrained_refinement` 若未通过留出结构门禁则拒绝微调，最终使用外部角度变换。\n\n"
        "Future formal capture must provide measured yaw per stationary segment, encoder resolution/repeatability, zero reference, "
        "positive direction, measured lidar-to-axis translation and mounting RPY, settle/valid timestamps, and an independent repeat capture.\n",
        "utf-8")
    # The offline entry point also publishes a self-contained evidence bundle;
    # the API export endpoint can regenerate it after a service-run job.
    bundle = output / "turntable-development-evidence.zip"
    temporary = output / "turntable-development-evidence.zip.tmp"
    members = [path for path in sorted(output.iterdir()) if path.is_file() and path not in {bundle, temporary}]
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in members:
            archive.write(path, path.name)
    temporary.replace(bundle)
    job["artifacts"]["evidence_zip"] = bundle.name
    _write_json(output / "job.json", job)
    return dict(job=job, validation=validation, qualities=qualities, map_metadata=metadata)

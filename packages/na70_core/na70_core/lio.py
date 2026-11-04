"""ROS-independent LIO input checks; no odometry, clock fitting or fake point times."""
from __future__ import annotations

from dataclasses import dataclass
import csv
from pathlib import Path

import numpy as np

from .observations import RayBatch


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'}")
    return float(value)


@dataclass(frozen=True)
class ClockModel:
    source_origin_s: float
    reference_origin_s: float
    rate: float
    uncertainty_s: float | None = None
    evidence: str | None = None

    def __post_init__(self):
        for name in ("source_origin_s", "reference_origin_s", "rate"):
            value = getattr(self, name)
            if isinstance(value, bool) or not np.isfinite(value):
                raise ValueError(f"clock {name} must be finite")
        if self.rate <= 0:
            raise ValueError("clock rate must be positive")
        if self.uncertainty_s is not None:
            _number(self.uncertainty_s, "clock uncertainty_s")
        if self.evidence is not None and (not isinstance(self.evidence, str) or not self.evidence.strip()):
            raise ValueError("clock evidence must be nonempty text or null")

    def map(self, device_times):
        times = np.asarray(device_times, dtype=np.float64)
        if not np.isfinite(times).all():
            raise ValueError("missing_or_invalid_device_time; host-time fallback is forbidden")
        mapped = (times - self.source_origin_s) * self.rate + self.reference_origin_s
        if not np.isfinite(mapped).all():
            raise ValueError("clock mapping overflow")
        return mapped

    @property
    def declared(self):
        return self.uncertainty_s is not None and self.evidence is not None


def extrinsics(T_imu_lidar):
    """Return T_IL and T_LI. T_AB maps coordinates from B into A."""
    matrix = np.array(T_imu_lidar, dtype=np.float64, copy=True)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError("T_imu_lidar must be a finite 4x4 matrix")
    rotation = matrix[:3, :3]
    if (not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-9, rtol=0)
            or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6, rtol=0)
            or not np.isclose(np.linalg.det(rotation), 1, atol=1e-6, rtol=0)):
        raise ValueError("T_imu_lidar must be a proper rigid transform")
    inverse = np.eye(4)
    inverse[:3, :3] = rotation.T
    inverse[:3, 3] = -rotation.T @ matrix[:3, 3]
    return matrix, inverse


@dataclass(frozen=True)
class ImuSamples:
    """One segment of raw gyro/specific-force samples with original CSV row IDs."""
    device_times: np.ndarray
    gyro_rad_s: np.ndarray
    accel_m_s2: np.ndarray
    source_rows: np.ndarray
    frame_id: str

    @classmethod
    def from_arrays(cls, times, gyro, accel, *, frame_id, gyro_unit="rad/s", accel_unit="m/s^2"):
        times = np.array(times, dtype=np.float64, copy=True)
        gyro = np.array(gyro, dtype=np.float64, copy=True)
        accel = np.array(accel, dtype=np.float64, copy=True)
        if (times.ndim != 1 or len(times) == 0 or gyro.shape != (len(times), 3)
                or accel.shape != (len(times), 3) or not isinstance(frame_id, str) or not frame_id.strip()):
            raise ValueError("invalid IMU shapes, empty samples or missing frame_id")
        if not all(np.isfinite(x).all() for x in (times, gyro, accel)):
            raise ValueError("nonfinite IMU samples")
        if np.any(np.diff(times) <= 0):
            raise ValueError("IMU time must strictly increase within one segment; do not sort or deduplicate")
        if gyro_unit not in ("rad/s", "deg/s") or accel_unit not in ("m/s^2", "g"):
            raise ValueError("unsupported IMU units")
        if gyro_unit == "deg/s":
            gyro *= np.pi / 180
        if accel_unit == "g":
            accel *= 9.80665
        if not np.isfinite(gyro).all() or not np.isfinite(accel).all():
            raise ValueError("IMU unit conversion overflow")
        rows = np.arange(len(times), dtype=np.int64)
        for array in (times, gyro, accel, rows):
            array.setflags(write=False)
        return cls(times, gyro, accel, rows, frame_id)

    @classmethod
    def from_csv(cls, path: Path, *, frame_id, gyro_unit, accel_unit, max_samples):
        if type(max_samples) is not int or max_samples <= 0:
            raise ValueError("max_samples must be a positive integer")
        columns = ("device_time_s", "gx", "gy", "gz", "ax", "ay", "az")
        rows = []
        with Path(path).open(newline="", encoding="utf-8-sig") as source:
            reader = csv.DictReader(source)
            if not reader.fieldnames or not set(columns).issubset(reader.fieldnames):
                raise ValueError(f"IMU CSV requires {','.join(columns)}")
            for row in reader:
                if len(rows) >= max_samples:
                    raise ValueError("IMU max_samples exceeded; no truncation performed")
                rows.append([float(row[key]) for key in columns])
        if not rows:
            raise ValueError("empty IMU CSV")
        data = np.asarray(rows)
        return cls.from_arrays(data[:, 0], data[:, 1:4], data[:, 4:7],
                               frame_id=frame_id, gyro_unit=gyro_unit, accel_unit=accel_unit)


def prepare_inputs(batch: RayBatch, imu: ImuSamples | None, cfg: dict):
    """Export a diagnostic packet-timed cloud and report declared input readiness.

    No fitting, deskewing or IMU interpolation happens here. Unknown calibration
    and timing bounds block readiness but leave the diagnostic cloud inspectable.
    """
    if cfg.get("schema_version") != "na70-lio-preflight-v1":
        raise ValueError("unsupported preflight schema")
    packet_gap = _number(cfg["max_packet_gap_s"], "max_packet_gap_s", positive=True)
    imu_gap = _number(cfg["max_imu_gap_s"], "max_imu_gap_s", positive=True)
    max_points = cfg["max_window_points"]
    if type(max_points) is not int or max_points <= 0:
        raise ValueError("max_window_points must be a positive integer")
    if not batch.ray_count or batch.ray_count > max_points:
        raise ValueError("empty or oversized ray batch; no truncation performed")
    if batch.coordinate_frame != cfg["lidar_frame"]:
        raise ValueError("LiDAR coordinate frame mismatch")
    lidar_clock = ClockModel(**cfg["lidar_clock"])
    imu_clock = ClockModel(**cfg["imu_clock"])
    times = lidar_clock.map(batch.device_times)
    if np.any(np.diff(times) < 0):
        raise ValueError("LiDAR time rewind; split capture into segments")
    if np.any(np.diff(times) > packet_gap):
        raise ValueError("LiDAR packet gap; split capture into segments")
    _, first, counts = np.unique(batch.packet_indices, return_index=True, return_counts=True)
    for start, count in zip(first, counts):
        if (np.any(batch.packet_indices[start:start + count] != batch.packet_indices[start])
                or np.any(times[start:start + count] != times[start])):
            raise ValueError("inconsistent packet identity or packet time")
    envelope = cfg["motion_envelope"]
    speed = _number(envelope["max_speed_m_s"], "max_speed_m_s")
    angular_speed = _number(envelope["max_angular_speed_rad_s"], "max_angular_speed_rad_s")
    max_range = _number(envelope["max_range_m"], "max_range_m", positive=True)
    budget = _number(envelope["time_error_budget_m"], "time_error_budget_m", positive=True)
    ranges = np.linalg.norm(batch.points[:, :3].astype(np.float64), axis=1)
    selected = batch.valid_return & (ranges <= max_range)
    refs = batch.source_refs(selected)
    if len(refs) != len(np.unique(refs, axis=0)):
        raise ValueError("duplicate raw source point references")
    arrays = dict(points=batch.points[selected].copy(),
                  relative_time_s=(times[selected] - times[0]), source_refs=refs)
    reasons = []
    if not selected.any():
        reasons.append("no_valid_points_in_range")
    if not lidar_clock.declared:
        reasons.append("lidar_clock_unverified")
    if not imu_clock.declared:
        reasons.append("imu_clock_unverified")
    packet_uncertainty = cfg["packet_time_uncertainty_s"]
    error_bound = None
    if packet_uncertainty is None:
        reasons.append("packet_time_uncertainty_unknown")
    else:
        _number(packet_uncertainty, "packet_time_uncertainty_s")
        if lidar_clock.declared and imu_clock.declared:
            uncertainty = lidar_clock.uncertainty_s + imu_clock.uncertainty_s + packet_uncertainty
            error_bound = (speed + max_range * angular_speed) * uncertainty
            if not np.isfinite(error_bound):
                raise ValueError("time error budget overflow")
            if error_bound > budget:
                reasons.append("time_error_budget_exceeded")
    transforms = None
    if cfg["T_imu_lidar"] is None:
        reasons.append("extrinsic_unknown")
    else:
        forward, inverse = extrinsics(cfg["T_imu_lidar"])
        transforms = {"fast_lio_T_imu_lidar": forward.tolist(),
                      "glim_T_lidar_imu": inverse.tolist()}
    if not isinstance(cfg["calibration_evidence"], str) or not cfg["calibration_evidence"].strip():
        reasons.append("calibration_unverified")
    if cfg.get("accel_includes_gravity") is not True:
        reasons.append("specific_force_not_confirmed")
    if imu is None:
        reasons.append("imu_missing")
    else:
        if imu.frame_id != cfg["imu_frame"]:
            raise ValueError("IMU coordinate frame mismatch")
        imu_times = imu_clock.map(imu.device_times)
        left = max(0, int(np.searchsorted(imu_times, times[0], side="right")) - 1)
        right = min(len(imu_times), int(np.searchsorted(imu_times, times[-1], side="left")) + 1)
        if imu_times[0] > times[0] or imu_times[-1] < times[-1]:
            reasons.append("imu_does_not_bracket_window")
        indices = slice(left, right)
        if np.any(np.diff(imu_times[indices]) > imu_gap):
            reasons.append("imu_gap")
        arrays.update(imu_time_s=imu_times[indices], gyro_rad_s=imu.gyro_rad_s[indices],
                      accel_m_s2=imu.accel_m_s2[indices], imu_source_rows=imu.source_rows[indices])
        for key, values in (("gyro_range_rad_s", imu.gyro_rad_s[indices]),
                            ("accel_range_m_s2", imu.accel_m_s2[indices])):
            limit = cfg[key]
            if limit is None:
                reasons.append(f"{key}_unknown")
            elif np.any(np.abs(values) >= _number(limit, key, positive=True)):
                reasons.append(f"{key}_saturated")
    report = dict(schema_version="na70-lio-input-report-v1",
                  status="BLOCKED" if reasons else "INPUT_CONTRACT_PASSED",
                  block_reasons=reasons, measurement_status="NOT_EVALUATED",
                  time_quality="PACKET_APPROXIMATION", deskew_applied=False,
                  capture_sha256=batch.capture_sha256, coordinate_frame=batch.coordinate_frame,
                  reference_time_s=float(times[0]), end_time_s=float(times[-1]),
                  input_rays=batch.ray_count, output_points=int(selected.sum()),
                  invalid_return_count=int((~batch.valid_return).sum()),
                  outside_range_count=int((batch.valid_return & ~selected).sum()),
                  packet_count=len(first), time_error_bound_m=error_bound,
                  transforms=transforms,
                  claims={"hardware_sync_validated": False, "calibration_validated": False,
                          "lio_validated": False, "slam_validated": False},
                  note="Readiness checks supplied declarations only; it is not hardware validation.")
    return report, arrays

"""Deterministic synthetic datasets for calibration tests and evidence runs.

Every generator returns the ground truth next to the measurements so a test can
assert recovery error instead of only asserting that a solver returned a value.
All outputs are labelled ``SYNTHETIC``: they exercise the math and the error
reporting, not the NA70 hardware.
"""
from __future__ import annotations

import math

import numpy as np


AXES = {
    "+x": (1.0, 0.0, 0.0),
    "-x": (-1.0, 0.0, 0.0),
    "+y": (0.0, 1.0, 0.0),
    "-y": (0.0, -1.0, 0.0),
    "+z": (0.0, 0.0, 1.0),
    "-z": (0.0, 0.0, -1.0),
}


def rotation_zyx_deg(yaw, pitch, roll):
    """Return a rotation matrix for intrinsic Z-Y-X angles in degrees."""
    yaw, pitch, roll = np.radians([yaw, pitch, roll])
    cz, sz = math.cos(yaw), math.sin(yaw)
    cy, sy = math.cos(pitch), math.sin(pitch)
    cx, sx = math.cos(roll), math.sin(roll)
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=np.float64)
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float64)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=np.float64)
    return rz @ ry @ rx


def six_face_dataset(*, matrix=None, bias=None, gravity=9.80665, noise_m_s2=0.01,
                     seed=7, faces=None):
    """Synthetic six-face means for ``a_meas = M (g d) + b``."""
    matrix = np.eye(3) if matrix is None else np.asarray(matrix, dtype=np.float64)
    bias = np.zeros(3) if bias is None else np.asarray(bias, dtype=np.float64)
    if matrix.shape != (3, 3) or bias.shape != (3,):
        raise ValueError("matrix must be 3x3 and bias length three")
    names = faces or list(AXES)
    directions = np.array([AXES[name] for name in names], dtype=np.float64)
    truth = gravity * (directions @ matrix.T) + bias
    generator = np.random.default_rng(seed)
    measured = truth + generator.normal(0.0, noise_m_s2, truth.shape)
    return {
        "kind": "SYNTHETIC",
        "faces": names,
        "measurements_m_s2": measured,
        "directions": directions,
        "truth": {"M": matrix, "bias_m_s2": bias, "gravity_m_s2": gravity},
    }


def rotation_dataset(*, rotation=None, time_offset_s=0.013, gyro_bias=None,
                     duration_s=20.0, imu_rate_hz=200.0, lidar_rate_hz=50.0,
                     noise_rad_s=0.01, seed=11):
    """Synthetic gyro series with a known ``R_IL`` and time offset.

    ``omega_imu(t) = R omega_lidar(t - tau) + bias + noise``.  The LiDAR series
    deliberately contains several frequencies on all three axes; a single-axis
    recording would leave the rotation about that axis unobservable.
    """
    rotation = rotation_zyx_deg(12.0, -7.0, 3.0) if rotation is None else np.asarray(rotation, dtype=np.float64)
    if rotation.shape != (3, 3):
        raise ValueError("rotation must be 3x3")
    if not math.isfinite(time_offset_s):
        raise ValueError("time_offset_s must be finite")
    gyro_bias = np.zeros(3) if gyro_bias is None else np.asarray(gyro_bias, dtype=np.float64)
    if gyro_bias.shape != (3,):
        raise ValueError("gyro_bias must have three entries")
    if duration_s <= 0 or imu_rate_hz <= 0 or lidar_rate_hz <= 0:
        raise ValueError("duration and rates must be positive")
    imu_count = int(round(duration_s * imu_rate_hz))
    lidar_count = int(round(duration_s * lidar_rate_hz))
    imu_times = np.arange(imu_count) / imu_rate_hz
    lidar_times = np.arange(lidar_count) / lidar_rate_hz

    def motion(times):
        t = np.asarray(times, dtype=np.float64)
        omega = np.empty((len(t), 3), dtype=np.float64)
        omega[:, 0] = 0.55 * np.sin(2 * math.pi * 0.37 * t + 0.3) + 0.18 * np.sin(2 * math.pi * 1.13 * t)
        omega[:, 1] = 0.42 * np.sin(2 * math.pi * 0.53 * t + 1.1) + 0.12 * np.cos(2 * math.pi * 0.91 * t)
        omega[:, 2] = 0.65 * np.sin(2 * math.pi * 0.29 * t + 2.0) + 0.22 * np.sin(2 * math.pi * 1.31 * t + 0.4)
        return omega

    omega_lidar = motion(lidar_times)
    omega_imu_clean = motion(imu_times - time_offset_s) @ rotation.T + gyro_bias
    generator = np.random.default_rng(seed)
    omega_imu = omega_imu_clean + generator.normal(0.0, noise_rad_s, omega_imu_clean.shape)
    return {
        "kind": "SYNTHETIC",
        "t_lidar": lidar_times,
        "omega_lidar": omega_lidar,
        "t_imu": imu_times,
        "omega_imu": omega_imu,
        "truth": {"R_imu_lidar": rotation, "time_offset_s": float(time_offset_s),
                  "gyro_bias": gyro_bias, "noise_rad_s": float(noise_rad_s)},
    }


def plane_dataset(*, distance_m=10.0, tilt_deg=2.0, span_m=4.0, count=4000,
                  noise_m=0.01, seed=3):
    """Synthetic wall whose *perpendicular* distance from the origin is exact.

    The plane normal is tilted about Z, so ``distance_m`` is the value the
    measurement is expected to recover (unlike a raw ``x = d`` plane, whose
    normal distance is ``d / sqrt(1 + tan(tilt)^2)``).
    """
    if distance_m <= 0 or span_m <= 0 or count < 10:
        raise ValueError("distance/span must be positive and count >= 10")
    generator = np.random.default_rng(seed)
    angle = math.radians(tilt_deg)
    normal = np.array([math.cos(angle), math.sin(angle), 0.0])
    horizontal = np.array([math.sin(angle), -math.cos(angle), 0.0])
    vertical = np.array([0.0, 0.0, 1.0])
    y = generator.uniform(-span_m / 2, span_m / 2, count)
    z = generator.uniform(-span_m / 2, span_m / 2, count)
    points = distance_m * normal + y[:, None] * horizontal + z[:, None] * vertical
    points += generator.normal(0.0, noise_m, points.shape)
    return {
        "kind": "SYNTHETIC",
        "points": points,
        "truth": {"distance_m": float(distance_m), "tilt_deg": float(tilt_deg),
                  "span_m": float(span_m), "noise_m": float(noise_m)},
    }
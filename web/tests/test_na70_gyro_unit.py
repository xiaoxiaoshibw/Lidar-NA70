"""Tests for the device-internal gyro unit check (SYNTHETIC quaternion streams)."""
import json
import math

import numpy as np

from na70_core.calibration import gyro_unit
from na70_core.calibration.frames import load_imu_frames


def quaternion_from_axis_angle(axis, angle):
    axis = np.asarray(axis, dtype=np.float64)
    norm = np.linalg.norm(axis)
    if norm == 0:
        return np.array([1.0, 0.0, 0.0, 0.0])
    axis = axis / norm
    half = angle / 2.0
    return np.concatenate([[math.cos(half)], axis * math.sin(half)])


def synthetic_frames(*, rad_s=False, moving=True, batches=200, per_batch=10, dt=0.05):
    times = []
    gyro = []
    quaternions = []
    q = np.array([1.0, 0.0, 0.0, 0.0])
    for index in range(batches):
        t = index * dt
        if moving:
            omega_deg = np.array([20.0 * math.sin(0.7 * t),
                                  -12.0 * math.cos(0.4 * t),
                                  6.0 + 3.0 * math.sin(0.2 * t)])
        else:
            omega_deg = np.zeros(3)
        delta = quaternion_from_axis_angle(omega_deg, math.radians(np.linalg.norm(omega_deg)) * dt)
        q = gyro_unit._multiply(q, delta)
        q = q / np.linalg.norm(q)
        recorded = np.radians(omega_deg) if rad_s else omega_deg
        for _ in range(per_batch):
            times.append(t)
            gyro.append(recorded)
            quaternions.append(q)
    return {"times": np.asarray(times), "gyro": np.asarray(gyro),
            "quaternions": np.asarray(quaternions)}


def test_quaternion_rate_matches_axis_angle():
    q0 = np.array([1.0, 0.0, 0.0, 0.0])
    q1 = quaternion_from_axis_angle([0.0, 0.0, 1.0], 0.1)
    omega = gyro_unit.quaternion_rate(q0, q1, 1.0)
    assert np.allclose(omega, [0.0, 0.0, 0.1], atol=1e-12)


def test_deg_s_is_confirmed():
    result = gyro_unit.verify(synthetic_frames())
    assert result["status"] == "DEG_S_CONFIRMED", result
    assert abs(result["scale_deg_per_recorded"] - 1.0) < 0.02
    assert result["chosen_convention"] == "body"
    assert result["cosine"] > 0.99


def test_rad_s_is_detected():
    result = gyro_unit.verify(synthetic_frames(rad_s=True))
    assert result["status"] == "RAD_S_LIKELY", result
    assert 55 < result["scale_deg_per_recorded"] < 60


def test_stride_compares_interval_mean():
    result = gyro_unit.verify(synthetic_frames(), stride=2, max_dt=0.5)
    assert result["status"] == "DEG_S_CONFIRMED", result
    assert result["stride"] == 2
    assert abs(result["scale_deg_per_recorded"] - 1.0) < 0.02


def test_zero_motion_is_insufficient():
    result = gyro_unit.verify(synthetic_frames(moving=False))
    assert result["status"] == "INSUFFICIENT_DATA"


def test_missing_quaternion_is_rejected():
    frames = synthetic_frames()
    frames["quaternions"] = None
    try:
        gyro_unit.verify(frames)
    except ValueError as exc:
        assert "quaternion" in str(exc)
    else:
        raise AssertionError("missing quaternion should raise")


def test_loader_reads_quaternions_and_reports_missing(tmp_path):
    rows = [{"t_mono": 1000.0 + index * 0.005, "t_unix": 1793000000.0 + index * 0.005,
             "segment": 0, "tid": index + 1, "accel": [0, 0, 9.8], "gyro": [0, 0, 0],
             "quaternion": [1.0, 0.0, 0.0, 0.0]} for index in range(3)]
    path = tmp_path / "frames.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    data = load_imu_frames(path)
    assert data["quaternions"].shape == (3, 4)
    rows[1].pop("quaternion")
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert load_imu_frames(path)["quaternions"] is None
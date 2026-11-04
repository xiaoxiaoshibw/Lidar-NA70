"""SYNTHETIC recovery tests for na70_core.calibration (no hardware claims)."""
import math

import numpy as np
import pytest

from na70_core.calibration import imu, planes, rotation, six_face
from na70_core.calibration import synthetic


def test_six_face_recovers_matrix_and_bias():
    matrix = np.array([[1.02, 0.008, -0.004],
                       [0.003, 0.985, 0.006],
                       [-0.002, 0.004, 1.011]])
    bias = np.array([0.12, -0.07, 0.05])
    data = synthetic.six_face_dataset(matrix=matrix, bias=bias, noise_m_s2=0.005, seed=5)
    result = six_face.solve(data["measurements_m_s2"], data["directions"])
    assert result["status"] == "SOLVED_CANDIDATE", result["reasons"]
    assert np.allclose(result["transform"]["M"], matrix, atol=5e-3)
    assert np.allclose(result["transform"]["bias_m_s2"], bias, atol=5e-3)
    corrected = six_face.correct_accel(data["measurements_m_s2"], matrix, bias)
    true = 9.80665 * data["directions"]
    assert np.allclose(corrected, true, atol=0.02)


def test_six_face_missing_face_is_reported():
    data = synthetic.six_face_dataset(faces=["+x", "-x", "+y", "-y", "+z"])
    result = six_face.solve(data["measurements_m_s2"], data["directions"])
    assert result["status"] in ("POOR_FIT", "INSUFFICIENT_DATA")
    assert "missing_faces:-z" in result["reasons"]


def test_wahba_recovers_exact_rotation():
    true = synthetic.rotation_zyx_deg(20, -10, 5)
    generator = np.random.default_rng(2)
    source = generator.normal(size=(20, 3))
    target = source @ true.T
    recovered = rotation.wahba_rotation(source, target)
    assert np.allclose(recovered, true, atol=1e-9)
    quaternion = rotation.quaternion_wxyz(recovered)
    assert math.isclose(float(np.linalg.norm(quaternion)), 1.0, abs_tol=1e-9)


def test_rotation_and_time_offset_are_recovered():
    data = synthetic.rotation_dataset(time_offset_s=0.013, noise_rad_s=0.01, seed=9)
    result = rotation.solve_lidar_imu_rotation(
        data["t_lidar"], data["omega_lidar"], data["t_imu"], data["omega_imu"])
    assert result["status"] == "SOLVED_CANDIDATE", result["reasons"]
    truth = data["truth"]["R_imu_lidar"]
    recovered = np.array(result["R_imu_lidar"])
    angle = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(truth.T @ recovered) - 1) / 2))))
    assert angle < 0.2
    assert abs(result["time_offset_s"] - data["truth"]["time_offset_s"]) <= 0.0015


def test_single_axis_motion_is_not_claimed_observable():
    generator = np.random.default_rng(4)
    times = np.arange(0, 10, 0.005)
    omega = np.zeros((len(times), 3))
    omega[:, 0] = 0.4 * np.sin(2 * math.pi * 0.5 * times)
    rotation_true = synthetic.rotation_zyx_deg(30, 0, 0)
    target = omega @ rotation_true.T
    result = rotation.solve_lidar_imu_rotation(times, omega, times, target,
                                               max_offset_s=0.05, coarse_step_s=0.01)
    assert result["status"] == "INSUFFICIENT_MOTION"
    assert "weak_rotation_observability" in result["reasons"]


def test_plane_fit_and_range_fit():
    data = synthetic.plane_dataset(distance_m=10.0, tilt_deg=2.0, noise_m=0.004, seed=8)
    fitted = planes.fit_plane(data["points"])
    assert fitted["status"] == "FIT"
    assert abs(fitted["normal_distance_m"] - 10.0) < 0.02
    assert fitted["residual_rms_m"] < 0.01
    measurement = planes.plane_measurement(data["points"], reference_distance_m=10.0)
    assert abs(measurement["reference"]["error_m"]) < 0.02

    reference = [5.0, 10.0, 20.0]
    measured = [5.0 * 1.01 + 0.02, 10.0 * 1.01 + 0.02, 20.0 * 1.01 + 0.02]
    fit = planes.multi_range_fit(reference, measured)
    assert fit["status"] == "FIT"
    assert math.isclose(fit["slope"], 1.01, rel_tol=1e-6)
    assert math.isclose(fit["intercept_m"], 0.02, abs_tol=1e-6)


def test_unwrap_microseconds_handles_wrap_and_rejects_reset():
    times, wraps = imu.unwrap_microseconds([0xFFFFFF00, 0xFFFFFFF0, 0x00000100])
    assert wraps == 1
    assert np.allclose(times, [0.0, 240e-6, 512e-6])
    with pytest.raises(ValueError):
        imu.unwrap_microseconds([1000, 900])


def test_host_batch_allan_is_labelled_and_uses_batch_means():
    generator = np.random.default_rng(12)
    batches = 1200
    times = np.repeat(np.arange(batches) * 0.05, 10)
    drift = np.arange(batches) * 0.0001
    values = np.repeat(drift, 10)[:, None] + generator.normal(0, 0.01, (batches * 10, 1))
    result = imu.host_batch_allan(times, values, axis_names=["gx"])
    assert result["time_basis"] == "HOST_BATCH_AVERAGED"
    assert result["batches"] == batches
    assert abs(result["mean_batch_size"] - 10.0) < 1e-9
    assert abs(result["batch_interval_median_s"] - 0.05) < 1e-9
    assert result["table"]
    assert "批次平均" in result["note"]


def test_host_batch_allan_splits_at_large_gap():
    generator = np.random.default_rng(13)
    batches = 1200
    times = np.repeat(np.arange(batches) * 0.05, 10)
    times[600 * 10:] += 0.45
    values = generator.normal(0.0, 0.01, (batches * 10, 1))
    result = imu.host_batch_allan(times, values, axis_names=["gx"])
    assert result["time_basis"] == "HOST_BATCH_AVERAGED"
    assert result["blocks"] == 2
    assert result["excluded_batches"] >= 1
    assert result["table"]


def test_allan_and_noise_summary_are_positive():
    generator = np.random.default_rng(3)
    times = np.arange(0, 100, 0.005)
    values = generator.normal(0.0, 0.02, (len(times), 3))
    result = imu.allan_deviation(times, values, axis_names=["x", "y", "z"])
    assert result["table"]
    assert result["table"][0]["adev"]["x"] > 0
    summary = imu.noise_summary(times, values, values)
    assert summary["sample_count"] == len(times)
    assert all(value > 0 for value in summary["gyro_rate_noise_density"])
    with pytest.raises(ValueError):
        imu.allan_deviation(times[:2], values[:2])
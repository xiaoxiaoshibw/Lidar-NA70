"""SYNTHETIC tests for the plane-based LiDAR-IMU rotation solver."""
import math

import numpy as np
import pytest

from na70_core.calibration import plane_extrinsic


def rotation_zyx_deg(yaw, pitch, roll):
    from scipy.spatial.transform import Rotation
    return Rotation.from_euler("zyx", [yaw, pitch, roll], degrees=True).as_matrix()


def test_ransac_recovers_dominant_plane():
    generator = np.random.default_rng(5)
    inliers = generator.uniform(-2, 2, (2000, 2))
    plane = np.column_stack([inliers, np.full(len(inliers), 2.0)])
    outliers = generator.uniform(-3, 3, (500, 3))
    points = np.vstack([plane, outliers])
    result = plane_extrinsic.ransac_plane(points, threshold_m=0.02, iterations=100)
    assert result["inlier_ratio"] > 0.75
    assert abs(abs(result["normal"][2]) - 1.0) < 1e-3
    assert result["residual_p95_m"] < 0.03


def test_solver_recovers_rotation_from_normals():
    true = rotation_zyx_deg(22.0, -7.0, 4.0)
    world_normal = np.array([0.0, 1.0, 0.0])
    from scipy.spatial.transform import Rotation
    rotations = Rotation.random(40, random_state=7).as_matrix()
    normals = np.einsum("nji,j->ni", rotations, world_normal) @ true
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    result = plane_extrinsic.solve_rotation(normals, rotations)
    assert result["status"] == "SOLVED_CANDIDATE", result["reasons"]
    recovered = np.asarray(result["R_imu_lidar"])
    angle = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(true.T @ recovered) - 1) / 2))))
    assert angle < 1e-4
    assert result["residual"]["rms_deg"] < 1e-3


def test_no_rotation_is_insufficient():
    rotations = np.tile(np.eye(3), (10, 1, 1))
    normals = np.tile([0.0, 1.0, 0.0], (10, 1))
    result = plane_extrinsic.solve_rotation(normals, rotations)
    assert result["status"] == "INSUFFICIENT_MOTION"


def test_quaternion_interpolation_reaches_midpoint():
    from scipy.spatial.transform import Rotation
    times = np.array([0.0, 1.0])
    q0 = [1.0, 0.0, 0.0, 0.0]
    q1 = Rotation.from_euler("z", 90, degrees=True).as_quat()  # xyzw
    quaternions = np.array([q0, [q1[3], q1[0], q1[1], q1[2]]])
    rotations = plane_extrinsic.interpolate_rotations(times, quaternions, [0.5])
    angle = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(rotations[0]) - 1) / 2))))
    assert abs(angle - 45.0) < 1e-6


def test_solver_rejects_nonfinite():
    normals = np.tile([0.0, 0.0, 1.0], (5, 1))
    normals[0, 0] = np.nan
    with pytest.raises(ValueError):
        plane_extrinsic.solve_rotation(normals, np.tile(np.eye(3), (5, 1, 1)))
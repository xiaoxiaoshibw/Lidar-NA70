"""SYNTHETIC tests for pose interpolation and scene assembly (no hardware)."""
import math

import numpy as np
import pytest

from na70_core import scene


def yaw_quaternion(angle_rad):
    return np.array([math.cos(angle_rad / 2), 0.0, 0.0, math.sin(angle_rad / 2)])


def analytic_pose(t):
    position = np.array([0.10 * t, 0.02 * t, 0.0])
    quaternion = yaw_quaternion(math.radians(2.0 * t))
    return position, quaternion


def plane_scene(count=1500, seed=4):
    generator = np.random.default_rng(seed)
    wall_x = np.column_stack([np.full(count, 5.0), generator.uniform(-4, 4, count),
                              generator.uniform(-2, 4, count)])
    wall_y = np.column_stack([generator.uniform(-2, 5, count), np.full(count, 3.0),
                              generator.uniform(-2, 4, count)])
    floor = np.column_stack([generator.uniform(-2, 5, count), generator.uniform(-4, 3, count),
                             np.full(count, -1.0)])
    return np.concatenate([wall_x, wall_y, floor], axis=0)


def scene_distance(points):
    return np.min(np.column_stack([np.abs(points[:, 0] - 5.0),
                                   np.abs(points[:, 1] - 3.0),
                                   np.abs(points[:, 2] + 1.0)]), axis=1)


def test_interpolation_is_exact_at_nodes_and_rejects_outside_and_gaps():
    trajectory = scene.Trajectory(
        times=np.array([0.0, 1.0, 2.0]),
        positions=np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0]], dtype=float),
        quaternions=np.array([[1, 0, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0]], dtype=float),
        frame="lidar")
    positions, rotations, valid, reasons = trajectory.interpolate([0.0, 0.5, 1.0, 2.0, 2.5], max_gap_s=1.5)
    assert valid.tolist() == [True, True, True, True, False]
    assert np.allclose(positions[1], [0.5, 0, 0])
    assert np.allclose(rotations[3], np.eye(3))
    assert reasons["outside"] == 1

    gapped = scene.Trajectory(
        times=np.array([0.0, 1.0, 10.0, 11.0]),
        positions=np.zeros((4, 3)),
        quaternions=np.tile([1.0, 0, 0, 0], (4, 1)),
        frame="lidar")
    _, _, valid, reasons = gapped.interpolate([5.0], max_gap_s=0.5)
    assert not valid[0] and reasons["gap"] == 1


def test_voxel_downsample_keeps_first_and_counts_all():
    points = np.array([[0.01, 0, 0], [0.02, 0, 0], [0.20, 0, 0]], dtype=float)
    indices, inverse, counts = scene.voxel_downsample(points, 0.1)
    assert indices.tolist() == [0, 2]
    assert counts.tolist() == [2.0, 1.0]
    assert inverse.tolist() == [0, 0, 1]


def test_imu_frame_requires_explicit_extrinsic():
    trajectory = scene.Trajectory(times=np.array([0.0, 1.0]), positions=np.zeros((2, 3)),
                                  quaternions=np.tile([1.0, 0, 0, 0], (2, 1)), frame="imu")
    with pytest.raises(ValueError):
        scene.transform_points(np.zeros((1, 3)), np.array([0.5]), trajectory)
    with pytest.raises(ValueError):
        scene.MapBuilder(trajectory=trajectory)


def test_scene_reconstruction_within_voxel_and_motion_error():
    truth = plane_scene()
    nodes = np.arange(0.0, 4.0001, 0.02)
    positions = np.array([analytic_pose(t)[0] for t in nodes])
    quaternions = np.array([analytic_pose(t)[1] for t in nodes])
    trajectory = scene.Trajectory(times=nodes, positions=positions,
                                  quaternions=quaternions, frame="lidar")
    builder = scene.MapBuilder(trajectory, voxel_size=0.03, max_gap_s=0.2)
    generator = np.random.default_rng(10)
    for frame_index, t0 in enumerate(np.arange(0.1, 3.9, 0.1)):
        point_times = t0 + generator.uniform(-0.04, 0.04, len(truth))
        angles = np.radians(2.0 * point_times)
        rotations = np.zeros((len(truth), 3, 3))
        rotations[:, 0, 0] = np.cos(angles)
        rotations[:, 0, 1] = -np.sin(angles)
        rotations[:, 1, 0] = np.sin(angles)
        rotations[:, 1, 1] = np.cos(angles)
        rotations[:, 2, 2] = 1.0
        positions = np.column_stack([0.10 * point_times, 0.02 * point_times, np.zeros(len(point_times))])
        points = np.einsum("nij,nj->ni", np.transpose(rotations, (0, 2, 1)), truth - positions)
        builder.add_frame(points, point_times, frame_index=frame_index)
    result = builder.finish()
    assert result.stats["points"] > 1000
    distance = scene_distance(result.points.astype(np.float64))
    assert np.percentile(distance, 95) < 0.03
    assert result.stats["dropped"]["outside"] == 0


def test_builder_drops_points_outside_trajectory():
    trajectory = scene.Trajectory(times=np.array([0.0, 1.0]), positions=np.zeros((2, 3)),
                                  quaternions=np.tile([1.0, 0, 0, 0], (2, 1)), frame="lidar")
    builder = scene.MapBuilder(trajectory, voxel_size=0.05, max_gap_s=1.5)
    points = np.zeros((4, 3))
    builder.add_frame(points, np.array([-1.0, 0.5, 1.0, 2.0]), frame_index=0)
    result = builder.finish()
    assert result.stats["dropped"]["outside"] == 2
    assert result.stats["valid_points"] == 2
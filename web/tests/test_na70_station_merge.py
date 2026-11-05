"""Tests for deterministic multi-station registration (SYNTHETIC clouds)."""
import numpy as np
import pytest

from na70_core.registration import (LIMITATIONS, StationMerge, VoxelCloud,
                                    icp_multistart, icp_point_to_point,
                                    kabsch, merge_stations,
                                    merge_voxel_clouds, rotation_angle_deg,
                                    transform_cloud, voxel_average)


def room_scene(rng, count=24000):
    """Three orthogonal wall grids 7.5 m out on +X/+Y/+Z, no noise."""
    specs = [(0, (-14.0, 14.0), (-10.0, 10.0)),
             (1, (-12.0, 12.0), (-10.0, 10.0)),
             (2, (-12.0, 12.0), (-14.0, 14.0))]
    walls = []
    for fixed, urange, vrange in specs:
        n = count // 3
        free = [axis for axis in range(3) if axis != fixed]
        points = np.empty((n, 3))
        points[:, fixed] = 7.5
        points[:, free[0]] = rng.uniform(*urange, n)
        points[:, free[1]] = rng.uniform(*vrange, n)
        walls.append(points)
    return np.concatenate(walls, axis=0)


def rotation_from_axis_angle(axis, angle_deg):
    axis = np.asarray(axis, dtype=np.float64)
    axis = axis / np.linalg.norm(axis)
    angle = np.radians(angle_deg)
    k = np.array([[0, -axis[2], axis[1]],
                  [axis[2], 0, -axis[0]],
                  [-axis[1], axis[0], 0]])
    return np.eye(3) + np.sin(angle) * k + (1 - np.cos(angle)) * (k @ k)


def test_voxel_average_is_mean_preserving_and_counts():
    points = np.array([[0.01, 0.01, 0.01], [0.04, 0.04, 0.04],
                       [0.11, 0.11, 0.11]])
    cloud = voxel_average(points, 0.1)
    assert cloud.size == 2
    assert cloud.weights.tolist() == [2.0, 1.0]
    np.testing.assert_allclose(cloud.means[0], [0.025, 0.025, 0.025])
    np.testing.assert_allclose(cloud.means[1], [0.11, 0.11, 0.11])
    # voxel centre is key*size + size/2, never the point mean
    np.testing.assert_allclose(cloud.centers[0], [0.05, 0.05, 0.05])


def test_voxel_average_rejects_bad_input():
    with pytest.raises(ValueError, match="finite"):
        voxel_average(np.array([[np.nan, 0, 0]]), 0.1)
    with pytest.raises(ValueError, match="positive"):
        voxel_average(np.zeros((4, 3)), 0.0)
    with pytest.raises(ValueError, match="one weight per point"):
        voxel_average(np.zeros((4, 3)), 0.1, weights=np.ones(3))


def test_voxel_merge_is_weighted_and_regrouping_stable():
    rng = np.random.default_rng(7)
    points = rng.uniform(-5, 5, (3000, 3))
    whole = voxel_average(points, 0.5)
    parts = voxel_average(points[:1000], 0.5)
    parts = merge_voxel_clouds(parts, voxel_average(points[1000:], 0.5), 0.5)
    assert parts.size == whole.size
    np.testing.assert_allclose(parts.means, whole.means, atol=1e-12)
    np.testing.assert_allclose(parts.weights, whole.weights, atol=1e-9)


def test_kabsch_recovers_exact_transform():
    rng = np.random.default_rng(3)
    src = rng.uniform(-8, 8, (500, 3))
    rotation = rotation_from_axis_angle([0.3, -1.0, 0.6], 37.0)
    translation = np.array([1.2, -3.4, 0.7])
    dst = src @ rotation.T + translation
    fit_rotation, fit_translation = kabsch(src, dst)
    np.testing.assert_allclose(fit_rotation, rotation, atol=1e-12)
    np.testing.assert_allclose(fit_translation, translation, atol=1e-12)


def test_kabsch_keeps_proper_rotation_on_mirrored_data():
    rng = np.random.default_rng(11)
    src = rng.uniform(-1, 1, (200, 3))
    mirror = src * np.array([-1.0, 1.0, 1.0])
    rotation, _ = kabsch(src, mirror)
    assert np.linalg.det(rotation) == pytest.approx(1.0, abs=1e-9)


def test_kabsch_requires_enough_nonempty_pairs():
    with pytest.raises(ValueError, match="at least 3"):
        kabsch(np.zeros((2, 3)), np.zeros((2, 3)))
    with pytest.raises(ValueError, match="same shape"):
        kabsch(np.zeros((5, 3)), np.zeros((4, 3)))


def test_icp_recovers_partial_overlap_transform():
    rng = np.random.default_rng(2026)
    scene = room_scene(rng)
    scene = scene + rng.normal(0, 0.003, scene.shape)
    rotation = rotation_from_axis_angle([0, 0, 1], 28.0)
    translation = np.array([0.9, -0.6, 0.2])
    moved = scene @ rotation.T + translation
    # station B sees a limited horizontal FOV sector and range window
    radius = np.linalg.norm(moved[:, :2], axis=1)
    azimuth = np.degrees(np.arctan2(moved[:, 1], moved[:, 0]))
    keep = (azimuth > 20) & (azimuth < 110) & (radius > 4) & (radius < 13)
    station_b = moved[keep]

    result = icp_point_to_point(station_b, scene,
                                voxel_schedule=(1.0, 0.35, 0.12))
    # ICP maps station B back onto the scene: the inverse of the world->B
    # transform used to synthesise the station.
    expected_rotation = rotation.T
    expected_translation = -rotation.T @ translation
    angle_error = rotation_angle_deg(result.rotation @ expected_rotation.T)
    translation_error = np.linalg.norm(result.translation - expected_translation)
    assert result.converged, result.describe()
    assert angle_error < 0.5, angle_error
    assert translation_error < 0.05, translation_error
    assert result.fitness > 0.8, result.fitness
    assert result.rmse < 0.05, result.rmse


def test_multistart_escapes_wrong_wall_lock():
    """A near-symmetric rectangular room can trap single-start ICP on a wrong
    wall (observed on the real 20261105 classroom static cloud with
    fitness 0.998 at a ~60 degree error); the deterministic yaw grid must
    recover the true alignment (SYNTHETIC)."""
    rng = np.random.default_rng(77)
    specs = [(0, 7.5, (-12.0, 12.0), (-9.0, 3.0)),
             (0, -7.5, (-12.0, 12.0), (-9.0, 3.0)),
             (1, 7.5, (-12.0, 12.0), (-9.0, 3.0)),
             (1, -7.5, (-12.0, 12.0), (-9.0, 3.0))]
    walls = []
    for fixed, value, urange, vrange in specs:
        n = 4000
        free = [axis for axis in range(3) if axis != fixed]
        points = np.empty((n, 3))
        points[:, fixed] = value
        points[:, free[0]] = rng.uniform(*urange, n)
        points[:, free[1]] = rng.uniform(*vrange, n)
        walls.append(points)
    room = np.concatenate(walls, axis=0)
    room = room + rng.normal(0, 0.004, room.shape)
    rotation = rotation_from_axis_angle([0, 0, 1], 31.0)
    translation = np.array([1.1, -0.7, 0.25])
    moved = room @ rotation.T + translation
    radius = np.linalg.norm(moved[:, :2], axis=1)
    azimuth = np.degrees(np.arctan2(moved[:, 1], moved[:, 0]))
    station = moved[(azimuth > 5) & (azimuth < 130) & (radius > 5)]

    result = icp_multistart(station, room, yaw_starts=8,
                            voxel_schedule=(1.0, 0.35, 0.12))
    angle_error = rotation_angle_deg(result.rotation @ rotation)
    translation_error = np.linalg.norm(result.translation + rotation.T @ translation)
    assert result.fitness > 0.8, result.describe()
    assert angle_error < 0.5, angle_error
    assert translation_error < 0.05, translation_error


def test_multistart_validates_inputs():
    rng = np.random.default_rng(1)
    cloud = rng.uniform(-2, 2, (2000, 3))
    with pytest.raises(ValueError, match="yaw_starts"):
        icp_multistart(cloud, cloud, yaw_starts=0)
    with pytest.raises(ValueError, match="proper rotation"):
        icp_multistart(cloud, cloud, initial=(np.zeros((3, 3)), np.zeros(3)))
    with pytest.raises(ValueError, match="min_fitness"):
        icp_multistart(cloud, cloud, min_fitness=1.5)


def test_multistart_records_seed_table():
    rng = np.random.default_rng(3)
    cloud = rng.uniform(-2, 2, (3000, 3))
    result = icp_multistart(cloud, cloud, yaw_starts=4)
    assert len(result.seeds) == 4
    assert all(len(row) == 4 for row in result.seeds)
    assert result.seeds[0][1] == pytest.approx(result.fitness)


def test_icp_flags_low_overlap_without_mutation():
    rng = np.random.default_rng(5)
    a = np.column_stack([rng.uniform(-5, 5, 4000), rng.uniform(-5, 5, 4000),
                         np.zeros(4000)])
    far = a + np.array([60.0, 0.0, 0.0])
    result = icp_point_to_point(far, a)
    # may still fit the two planes, but fitness against the full target is low
    assert result.fitness < 0.3 or not result.converged


def test_merge_stations_refuses_disjoint_station():
    rng = np.random.default_rng(9)
    base = np.column_stack([rng.uniform(-5, 5, 6000), rng.uniform(-5, 5, 6000),
                            rng.uniform(-5, 5, 6000)])
    apart = base + np.array([80.0, 0.0, 0.0])
    merge = merge_stations([base, apart], labels=["a", "apart"],
                           fitness_gate=0.3)
    assert isinstance(merge, StationMerge)
    assert merge.accepted == ["a"]
    assert [r["label"] for r in merge.refusals] == ["apart"]
    assert merge.merged.size == voxel_average(base, 0.05).size


def test_merge_stations_three_stations_chained_transforms():
    rng = np.random.default_rng(4242)
    scene = room_scene(rng)
    scene = scene + rng.normal(0, 0.003, scene.shape)
    second_rotation = rotation_from_axis_angle([0, 0, 1], 25.0)
    second_translation = np.array([0.8, -0.5, 0.15])
    third_rotation = rotation_from_axis_angle([0, 0, 1], -18.0)
    third_translation = np.array([-1.1, 0.9, -0.2])

    def crop(points, lo, hi):
        radius = np.linalg.norm(points[:, :2], axis=1)
        azimuth = np.degrees(np.arctan2(points[:, 1], points[:, 0]))
        unwrap = (azimuth - (lo + hi) / 2 + 180) % 360 - 180
        return points[(np.abs(unwrap) < (hi - lo) / 2) & (radius > 4) & (radius < 13)]

    stations = [
        crop(scene, -70, 70),
        crop(scene @ second_rotation.T + second_translation, 20, 160),
        crop(scene @ third_rotation.T + third_translation, -160, -20),
    ]
    merge = merge_stations(stations, labels=["s0", "s1", "s2"],
                           voxel=0.05, fitness_gate=0.5,
                           voxel_schedule=(1.0, 0.35, 0.12))
    assert merge.accepted == ["s0", "s1", "s2"]
    assert not merge.refusals
    for label, rotation, translation in (
            ("s1", second_rotation, second_translation),
            ("s2", third_rotation, third_translation)):
        reg = merge.registrations[label]
        expected_rotation = rotation.T
        expected_translation = -rotation.T @ translation
        angle_error = rotation_angle_deg(reg.rotation @ expected_rotation.T)
        translation_error = np.linalg.norm(reg.translation - expected_translation)
        assert angle_error < 0.5, (label, angle_error)
        assert translation_error < 0.05, (label, translation_error)
    base_voxels = voxel_average(scene, 0.05).size
    assert 0.5 * base_voxels < merge.merged.size < 2.0 * base_voxels


def test_transform_cloud_moves_means_keeps_weights():
    cloud = voxel_average(np.array([[1.0, 0.0, 0.0]]), 0.1)
    rotation = rotation_from_axis_angle([0, 1, 0], 90.0)
    moved = transform_cloud(cloud, rotation, [0, 2, 0])
    np.testing.assert_allclose(moved.means[0], [0.0, 2.0, -1.0], atol=1e-12)
    np.testing.assert_allclose(moved.weights, cloud.weights)


def test_limitations_are_exposed():
    assert any("自洽性" in line for line in LIMITATIONS)

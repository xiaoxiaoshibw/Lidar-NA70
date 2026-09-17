"""SYNTHETIC geometry regressions; never real-data acceptance evidence."""
import numpy as np
import pytest
from backend import mapping as m
from test_mapping import synthetic_config, room, pose_from, drive, load


@pytest.mark.parametrize('rotation', [0.0, 1.0])
def test_moving_sensor_preserves_world_surfaces(rotation):
    rng = np.random.default_rng(3)
    cfg = synthetic_config()
    world = room(rng)
    poses = [pose_from([i * .12, 0, 0], i * rotation) for i in range(4)]
    report = m.run_odometry(load(drive(poses, world, cfg, rng)), list(range(4)), cfg)
    assert all(f['pose'] is not None for f in report['frames'])
    bounds = report['map'].bounds()
    # Noiseless fixture: 2 cm is already much larger than the ICP residual.
    np.testing.assert_allclose(bounds['minimum'], [0, 0, 0], atol=.02)
    np.testing.assert_allclose(bounds['maximum'], [9, 7, 3], atol=.02)


def test_constant_velocity_initial_is_one_step(monkeypatch):
    rng = np.random.default_rng(3)
    cfg = synthetic_config()
    poses = [pose_from([i * .12, 0, 0]) for i in range(4)]
    initial = []
    register = m.register
    def spy(source, target, guess, *args, **kwargs):
        initial.append(guess.copy())
        return register(source, target, guess, *args, **kwargs)
    monkeypatch.setattr(m, 'register', spy)
    m.run_odometry(load(drive(poses, room(rng), cfg, rng)), list(range(4)), cfg)
    assert initial[-1][0, 3] == pytest.approx(.36, abs=.005)


def test_twist_prediction_scales_rotation_and_translation():
    cfg = synthetic_config()
    increment = pose_from([.1, .05, 0], 3)
    predicted, reason = m.predict_pose(np.eye(4), increment, .1, .2, cfg)
    np.testing.assert_allclose(predicted, increment @ increment, atol=1e-10)
    assert reason == 'constant_body_twist'
    predicted, reason = m.predict_pose(increment, increment, .1, 10, cfg)
    np.testing.assert_allclose(predicted, increment)
    assert reason == 'prediction_horizon_exceeded'


def test_stationary_window_requires_declaration():
    cfg = synthetic_config(initialization_mode='stationary_window', stationary_end_frame=2)
    with pytest.raises(ValueError, match='声明'):
        m.validate_configuration(cfg)


def test_stationary_initialization_and_registration_are_counted_separately():
    rng = np.random.default_rng(3)
    cfg = synthetic_config(initialization_mode='stationary_window', stationary_end_frame=2,
                           stationary_declaration='SYNTHETIC stationary frames 0-2')
    m.validate_configuration(cfg)
    clouds = drive([np.eye(4)] * 4, room(rng), cfg, rng)
    report = m.run_odometry(load(clouds), [0., .1, .2, .3], cfg)
    assert [r['state'] for r in report['frames']] == ['reference'] * 3 + ['accepted']
    assert report['metrics']['initialization_frames'] == 3
    assert report['metrics']['registered_frames'] == 1


def test_submap_accepts_non_keyframes_and_duplicate_time_is_rejected():
    rng = np.random.default_rng(3)
    cfg = synthetic_config(keyframe_time=0, keyframe_translation=10, keyframe_rotation=90)
    report = m.run_odometry(load(drive([np.eye(4)] * 4, room(rng), cfg, rng)), [0., .1, .2, .2], cfg)
    assert report['metrics']['keyframe_count'] == 1
    assert report['frames'][2]['submap_frame_count'] == 2
    assert report['frames'][3]['state'] == 'timestamp_invalid'


def test_voxel_budget_bounds_exclude_dropped_points():
    voxel = m.VoxelMap(1, max_voxels=1)
    voxel.add(np.array([[.1, .1, .1], [50., 0, 0]]), np.ones(2), 0, 0, 0)
    assert voxel.bounds()['maximum'] == [.1, .1, .1]


def test_independent_surface_sampling_has_bounded_map_error():
    rng = np.random.default_rng(31)
    cfg = synthetic_config()
    clouds = [drive([pose_from([i*.08, 0, 0], i*.3)], room(rng), cfg, rng)[0] for i in range(5)]
    report = m.run_odometry(load(clouds), np.arange(5)*.1, cfg)
    assert report['metrics']['registered_frames'] >= 3
    xyz = report['map']._matrix()[:, :3]
    distance_to_wall = np.min(np.column_stack((np.abs(xyz), np.abs(xyz - [9, 7, 3]))), axis=1)
    assert np.quantile(distance_to_wall, .95) < .1


def test_failed_initialization_does_not_publish_map():
    cfg = synthetic_config(initialization_mode='stationary_window', stationary_end_frame=2,
                           stationary_declaration='SYNTHETIC stationary degenerate input')
    cloud = np.column_stack((np.linspace(1, 5, 100), np.zeros((100, 3))))
    report = m.run_odometry(load([cloud] * 4), [0., .1, .2, .3], cfg)
    assert report['metrics']['initialization_failed']
    assert report['map'].voxel_count() == 0
    assert all(r['pose'] is None for r in report['frames'])


def test_recovery_keeps_common_map_and_excludes_failed_frame():
    rng = np.random.default_rng(6)
    cfg = synthetic_config(lost_after_failures=1)
    good = drive([np.eye(4)], room(rng), cfg, rng)[0]
    report = m.run_odometry(load([good, np.zeros((2, 4)), good]), [0., .1, .2], cfg)
    assert report['frames'][2]['state'] == 'reinitialized'
    assert [s['map_id'] for s in report['trajectory']['segments']] == [0, 0]
    assert report['frames'][1]['pose'] is None
    np.testing.assert_allclose(report['frames'][2]['pose'], np.eye(4), atol=.002)

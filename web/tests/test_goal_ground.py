"""SYNTHETIC regression tests for ground-relative coordinates."""
import numpy as np
import pytest

from backend.goal_detection.config import load_configuration
from backend.goal_detection.pipeline import _estimate_ground_level, _apply_ground_level


def plane(a=0.0, b=0.0, c=-1.3):
    x, y = np.meshgrid(np.linspace(85, 103, 40), np.linspace(-35, -18, 40))
    return np.column_stack((x.ravel(), y.ravel(), (a*x+b*y+c).ravel(),
                            np.arange(x.size)))


@pytest.mark.parametrize('a,b,c', [(0, 0, -1.3), (.001, -.002, -1.3),
                                     (.02, -.03, -3)])
def test_ground_removes_mount_offset_even_when_horizontal(a, b, c):
    cfg = load_configuration()
    points = plane(a, b, c)
    original = points.copy()
    level = _estimate_ground_level(points, points, cfg, cfg['goal_detection']['z_leveling'])
    assert level['enabled'], level
    output = _apply_ground_level(points, level)
    assert np.quantile(np.abs(output[:, 2]), .95) < .00001
    assert np.array_equal(output[:, [0, 1, 3]], points.astype(np.float32)[:, [0, 1, 3]])
    assert np.array_equal(original, points)


@pytest.mark.parametrize('kind', ['line', 'wall', 'sparse', 'empty', 'nan'])
def test_ground_rejects_unobservable_surfaces(kind):
    points = plane()
    if kind == 'line':
        points[:, 1] = points[:, 0] * .1
    elif kind == 'wall':
        points[:, 0] = 94
        points[:, 2] = np.linspace(-1.3, 3, len(points))
    elif kind == 'sparse':
        points = points[:12]
    elif kind == 'empty':
        points = points[:0]
    else:
        points[:, 2] = np.nan
    cfg = load_configuration()
    level = _estimate_ground_level(points, points[:0], cfg, cfg['goal_detection']['z_leveling'])
    assert not level['enabled'], level
    assert level['reason']


@pytest.mark.parametrize('context,reason', [
    ({'motion_status': 'moving', 'source_age_seconds': 1}, 'moving_ground_window'),
    ({'motion_status': 'unknown', 'source_age_seconds': 1}, 'unknown_ground_motion'),
    ({'motion_status': 'confirmed_static', 'source_age_seconds': 40}, 'stale_ground_window'),
    ({'motion_status': 'confirmed_static', 'source_age_seconds': -1}, 'invalid_ground_time'),
])
def test_accumulated_ground_requires_valid_time_and_static_context(context, reason):
    points = plane(.01, 0, -2)
    cfg = load_configuration()
    level = _estimate_ground_level(points[:10], points, cfg,
        cfg['goal_detection']['z_leveling'], source_context=context)
    assert not level['enabled']
    assert level['reason'] == reason


def test_static_accumulated_ground_has_source_metadata():
    points = plane()
    cfg = load_configuration()
    level = _estimate_ground_level(points[:10], points, cfg,
        cfg['goal_detection']['z_leveling'], source_context={
            'motion_status': 'confirmed_static', 'source_age_seconds': 1.9,
            'source_frame_range': [0, 19]})
    assert level['enabled'], level
    assert level['source_frame_range'] == [0, 19]
    assert level['source_age_seconds'] == 1.9
    assert level['rank'] == 3


def test_ground_reference_can_be_independent_of_manual_goal_roi():
    points = plane()
    cfg = load_configuration()
    cfg['accumulation']['roi'] = [94.0, 96.0, -24.0, -22.0, -2.0, 5.0]
    restricted = _estimate_ground_level(points, points[:0], cfg,
                                        cfg['goal_detection']['z_leveling'])
    assert not restricted['enabled']
    assert restricted['source_attempts'][0]['reason'] == 'insufficient_ground_points'

    cfg['goal_detection']['z_leveling']['reference_roi'] = 'full_field'
    full = _estimate_ground_level(points, points[:0], cfg,
                                  cfg['goal_detection']['z_leveling'])
    assert full['enabled'], full
    assert full['source'] == 'single'
    assert full['reference_roi'] == 'full_field'
    assert full['input_points'] == len(points)


def test_candidate_corners_are_restored_at_each_xy_not_center_only():
    from backend.goal_detection.detector import GoalCandidate
    from backend.goal_detection.geometry import oriented_box_corners
    from backend.goal_detection.pipeline import _restore_candidate_levels
    candidate = GoalCandidate([94, -26, 1], [.6, 5, 2], 1.2, .9, 1, 2)
    original = np.asarray(oriented_box_corners(candidate.center, candidate.size, candidate.yaw))
    model = {'enabled': True, 'slope_x': .02, 'slope_y': -.01, 'intercept': -3}
    _restore_candidate_levels([candidate], model)
    corners = np.asarray(candidate.as_dict()['corners'])
    expected = original.copy()
    expected[:, 2] += .02*expected[:, 0]-.01*expected[:, 1]-3
    assert np.allclose(corners, expected, atol=.00051)
    assert np.allclose(candidate.as_dict()['bbox_min'], expected.min(axis=0), atol=.00051)


@pytest.mark.parametrize('key,value', [('min_xy_span', 0), ('min_xy_minor_std', -1),
    ('max_source_age_seconds', float('nan')), ('max_below_plane_fraction', 1)])
def test_ground_configuration_rejects_invalid_quality_limits(key, value):
    with pytest.raises(ValueError):
        load_configuration(overrides={'goal_detection': {'z_leveling': {key: value}}})


def test_ground_configuration_rejects_unknown_reference_roi():
    with pytest.raises(ValueError, match='reference_roi'):
        load_configuration(overrides={'goal_detection': {'z_leveling': {
            'reference_roi': 'unverified_pose'}}})


def test_restoration_does_not_double_shift_aliases_or_shared_poles():
    from dataclasses import replace
    from backend.goal_detection.detector import GoalCandidate
    from backend.goal_detection.pipeline import _restore_candidate_levels
    pole = {'center': [94, -26], 'base_z': 0., 'top_z': 2.}
    a = GoalCandidate([94,-26,1], [.6,5,2], 1.2, .9, 1, 2, left_pole=pole)
    b = replace(a, center=[94,-26,1.1])
    _restore_candidate_levels([a,a,b], {'enabled':True, 'slope_x':0., 'slope_y':0., 'intercept':-1.3})
    assert a.center[2] == pytest.approx(-.3)
    assert a.left_pole['base_z'] == pytest.approx(-1.3)
    assert b.left_pole['base_z'] == pytest.approx(-1.3)
    assert pole['base_z'] == 0

"""SYNTHETIC window identity and immutable raw-reference checks."""
from copy import deepcopy
import pytest
from backend.goal_detection.window_annotation import validate_window_label, COMPONENTS


def window_label():
    return {'capture_id': 'cap', 'capture_sha256': 'a'*64,
            'observation_unit': 'accumulated_window', 'type': 'goal',
            'frame_range': [0, 1], 'cloud_id': 'raw:one', 'coordinate_frame': 'lidar_link',
            'device_time_range': [0., .19], 'duration_seconds': .19,
            'motion_status': 'confirmed_static', 'review_status': 'reviewed',
            'visibility': 'visible', 'estimated_error_m': .1,
            'box': {'center': [94., -26., 0.], 'size': [.6, 5., 2.], 'yaw': 1.57},
            'components': {k: {'visibility': 'visible', 'raw_refs': [[0, 1], [1, 2]]} for k in COMPONENTS}}


FRAMES = [{'device_start_time': 0., 'device_time': .09, 'point_count': 20},
          {'device_start_time': .1, 'device_time': .19, 'point_count': 20}]


def test_window_contract_and_out_of_window_sources():
    label = window_label()
    validate_window_label(label, 'a'*64, FRAMES)
    for key, value in [('capture_sha256', 'b'*64), ('device_time_range', [0, .2]),
                       ('duration_seconds', .2), ('observation_unit', 'single_frame')]:
        with pytest.raises(ValueError):
            validate_window_label({**label, key:value}, 'a'*64, FRAMES)
    for ref in [[2, 0], [0, 20], [True, 0]]:
        bad = deepcopy(label)
        bad['components']['left_edge']['raw_refs'] = [ref]
        with pytest.raises(ValueError):
            validate_window_label(bad, 'a'*64, FRAMES)


def test_window_score_is_one_observation_and_never_frame_truth():
    from test_score_goal_detection import score_run, row
    from score_goal_detection import score_windows
    label = window_label()
    rows = [row(i, (94, -26)) for i in range(2)]
    result = score_run(rows, [label])
    assert result['measurement']['frames'] == 0
    assert result['window_evaluation']['windows_evaluated'] == 0
    record = {k: label[k] for k in ('capture_id','capture_sha256','cloud_id','coordinate_frame',
                                  'frame_range','device_time_range','observation_unit')}
    record['selected_measurement'] = {'center': [94,-26,0], 'size': [.6,5,2], 'confidence': .9,
                                      'evidence_role': 'MEASURED_CURRENT'}
    score = score_windows([record], [label])
    assert score['windows_evaluated'] == 1
    assert score['entries'][0]['center_hit']
    assert score_windows([record, record], [label])['windows_evaluated'] == 0
    assert score_windows([{**record, 'capture_sha256': 'wrong'}], [label])['windows_evaluated'] == 0
    assert score_windows([record], [{**label, 'review_status': 'PENDING_REVIEW'}])['windows_evaluated'] == 0

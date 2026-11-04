"""Window truth contract, independent of detector output and tracking."""
import math


COMPONENTS = ('left_edge', 'right_edge', 'upper_edge', 'local_ground', 'net_or_brace')


def validate_window_label(label, capture_sha256, frames):
    """Validate a v2 label against immutable capture metadata before saving."""
    if label.get('observation_unit') not in ('single_frame', 'accumulated_window'):
        raise ValueError('observation_unit 非法')
    lo, hi = label['frame_range']
    if label['observation_unit'] == 'single_frame' and lo != hi:
        raise ValueError('single_frame 只能绑定一帧')
    if label.get('capture_sha256') != capture_sha256:
        raise ValueError('capture_sha256 不匹配')
    if label.get('coordinate_frame') != 'lidar_link':
        raise ValueError('coordinate_frame 必须为 lidar_link')
    if not isinstance(label.get('cloud_id'), str) or not label['cloud_id']:
        raise ValueError('cloud_id 缺失')
    times = label.get('device_time_range')
    actual = [frames[lo].get('device_start_time'), frames[hi].get('device_time')]
    if not isinstance(times, list) or len(times) != 2 or any(
            not _finite(t) or a is None or not math.isclose(t, a, abs_tol=1e-6, rel_tol=0)
            for t, a in zip(times, actual)):
        raise ValueError('device_time_range 与原始窗口不符')
    duration = label.get('duration_seconds')
    if times[1] < times[0] or not _finite(duration) or not math.isclose(
            duration, times[1]-times[0], abs_tol=1e-6, rel_tol=0):
        raise ValueError('duration_seconds 非法')
    if label.get('motion_status') not in ('confirmed_static', 'moving', 'unknown'):
        raise ValueError('motion_status 非法')
    if label.get('review_status') not in ('reviewed', 'PENDING_REVIEW'):
        raise ValueError('review_status 非法')
    if label.get('visibility') not in ('visible', 'partially_occluded', 'invisible', 'uncertain'):
        raise ValueError('visibility 非法')
    error = label.get('estimated_error_m')
    if error is not None and (not _finite(error) or error < 0):
        raise ValueError('estimated_error_m 非法')
    parts = label.get('components')
    if not isinstance(parts, dict) or set(parts) != set(COMPONENTS):
        raise ValueError('components 不完整')
    for name, part in parts.items():
        if not isinstance(part, dict) or part.get('visibility') not in ('visible', 'invisible', 'uncertain'):
            raise ValueError(f'{name}.visibility 非法')
        refs = part.get('raw_refs')
        if not isinstance(refs, list) or len(refs) > 200000:
            raise ValueError(f'{name}.raw_refs 非法')
        for ref in refs:
            if not isinstance(ref, list) or len(ref) != 2 or any(type(v) is not int for v in ref):
                raise ValueError('raw_ref 必须为两个整数')
            f, p = ref
            if not lo <= f <= hi or not 0 <= p < frames[f]['point_count']:
                raise ValueError('raw_ref 超出窗口或原始帧')
        if part['visibility'] == 'visible' and not refs:
            raise ValueError('可见部件必须绑定原始点')


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

"""Static consistency checks, not a complete LiDAR/IMU calibration."""
import math
import statistics

THRESHOLDS = {'min_samples': 1000, 'min_host_span_s': 9.0,
              'max_accel_axis_std_m_s2': 0.10, 'max_accel_norm_std_m_s2': 0.08,
              'max_gyro_axis_std_deg_s': 0.20, 'max_gyro_mean_norm_deg_s': 0.80,
              'accel_mean_norm_range_m_s2': [9.3, 10.3], 'max_host_gap_s': 0.20}


def evaluate_static(rows, stream_errors=0):
    valid = [r for r in rows if len(r['fields'].get('accel', [])) == 3
             and len(r['fields'].get('gyro', [])) == 3]
    result = {'schema': 'yesense.static-check.v1', 'scope': 'SINGLE_POSE_STATIC_CONSISTENCY',
              'calibration_accepted': False, 'thresholds': THRESHOLDS,
              'sample_count': len(valid), 'stream_error_events': stream_errors,
              'clock': 'HOST_RECEIVE_TIME', 'device_timestamp_present': bool(valid) and all('sample_us' in r['fields'] for r in valid),
              'unobserved': ['LiDAR_to_IMU_rotation', 'LiDAR_to_IMU_translation',
                             'LiDAR_IMU_time_offset', 'absolute_yaw',
                             'full_accelerometer_bias_and_scale', 'Allan_noise_parameters'],
              'assumptions': ['Rigid assembly is physically stationary; IMU alone cannot prove this.',
                              'Units follow YESENSE reference parser; exact hardware model not confirmed.',
                              'Short-term apparent gyro bias only; temperature stability not measured.']}
    if len(valid) < 2:
        return dict(result, status='INSUFFICIENT_DATA', failed_checks=['sample_count'])
    accel = list(zip(*(r['fields']['accel'] for r in valid)))
    gyro = list(zip(*(r['fields']['gyro'] for r in valid)))
    am = [statistics.mean(v) for v in accel]
    gm = [statistics.mean(v) for v in gyro]
    ast = [statistics.stdev(v) for v in accel]
    gst = [statistics.stdev(v) for v in gyro]
    norms = [math.sqrt(sum(v*v for v in r['fields']['accel'])) for r in valid]
    span = valid[-1]['t'] - valid[0]['t']
    gaps = [b['t'] - a['t'] for a, b in zip(valid, valid[1:])]
    sequence_breaks = sum(b['tid'] not in (((a['tid'] + 1) & 65535), 1 if a['tid'] == 60000 else -1)
                          for a, b in zip(valid, valid[1:]))
    norm = math.sqrt(sum(v*v for v in am))
    gyro_norm = math.sqrt(sum(v*v for v in gm))
    checks = {'sample_count': len(valid) >= THRESHOLDS['min_samples'],
              'host_span': span >= THRESHOLDS['min_host_span_s'],
              'accel_axis_std': max(ast) <= THRESHOLDS['max_accel_axis_std_m_s2'],
              'accel_norm_std': statistics.stdev(norms) <= THRESHOLDS['max_accel_norm_std_m_s2'],
              'gyro_axis_std': max(gst) <= THRESHOLDS['max_gyro_axis_std_deg_s'],
              'gyro_mean': gyro_norm <= THRESHOLDS['max_gyro_mean_norm_deg_s'],
              'accel_norm': 9.3 <= norm <= 10.3,
              'host_continuity': max(gaps) <= THRESHOLDS['max_host_gap_s'],
              'sequence_continuity': sequence_breaks == 0,
              'stream_integrity': stream_errors == 0,
              'complete_fields': len(valid) == len(rows)}
    return dict(result, status='STATIC_LIKE_WINDOW' if all(checks.values()) else 'REJECTED',
                checks=checks, failed_checks=[k for k, v in checks.items() if not v],
                host_span_s=span, host_received_hz=(len(valid)-1)/span if span > 0 else None,
                max_host_gap_s=max(gaps), sequence_breaks=sequence_breaks,
                accel_mean_m_s2=am, accel_std_m_s2=ast, accel_mean_norm_m_s2=norm,
                accel_norm_std_m_s2=statistics.stdev(norms),
                gyro_mean_deg_s=gm, gyro_std_deg_s=gst,
                gyro_bias_candidate_rad_s=[math.radians(v) for v in gm] if all(checks.values()) else None,
                specific_force_unit_imu=[v/norm for v in am] if norm else None,
                apparent_gravity_direction_imu=[-v/norm for v in am] if norm else None,
                gravity_tilt_deg={'roll': math.degrees(math.atan2(am[1], am[2])),
                                  'pitch': math.degrees(math.atan2(-am[0], math.hypot(am[1], am[2])))},
                tilt_note='Assumes accelerometer +Z up with gravity specific force; not LiDAR/IMU extrinsic rotation.')

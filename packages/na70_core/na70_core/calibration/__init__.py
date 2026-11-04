"""Calibration math for the NA70 LiDAR + YESENSE IMU rig.

Pure NumPy helpers, no ROS and no file I/O beyond the documented JSONL/CSV
readers.  Every routine reports observability and refuses to invent a result
when the data cannot constrain the parameter; see ``LIMITATIONS`` in each
module.  A recovered number is a *candidate* until an independent hold-out run
passes the gates in ``Plan/NA70_YESENSE_标定AI执行计划.md``.
"""
from .imu import (
    allan_deviation,
    noise_summary,
    overall_status,
    range_summary,
    static_windows,
)
from .six_face import correct_accel, solve as solve_six_face
from .extrinsic import compose_candidate
from .planes import fit_plane, multi_range_fit, plane_measurement
from .rotation import (
    estimate_time_offset,
    quaternion_wxyz,
    rotation_matrix_to_euler_zyx_deg,
    solve_lidar_imu_rotation,
    wahba_rotation,
)

__all__ = [
    "allan_deviation",
    "compose_candidate",
    "correct_accel",
    "estimate_time_offset",
    "fit_plane",
    "multi_range_fit",
    "noise_summary",
    "overall_status",
    "plane_measurement",
    "quaternion_wxyz",
    "range_summary",
    "rotation_matrix_to_euler_zyx_deg",
    "solve_lidar_imu_rotation",
    "solve_six_face",
    "static_windows",
    "wahba_rotation",
]
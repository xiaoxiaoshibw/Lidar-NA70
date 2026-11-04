"""Compose the LiDAR-IMU extrinsic candidate from rotation plus a measured lever arm.

Rotation and time offset are observed from motion; the translation of the LiDAR
optical origin in the IMU frame is not observable from rotation and must come
from a physical measurement or a separately validated estimator.  This module
only validates, inverts and documents the inputs.  It never fills a missing
translation with zero.
"""
from __future__ import annotations

import math

import numpy as np

from ..lio import extrinsics


LIMITATIONS = (
    "t_IL 必须来自光学原点测量或独立验证的估计，不能用零或外壳中心代替",
    "旋转/时间候选来自角速度对齐，仍需留出动作验证",
    "候选参数在 A8 通过前不得写成 calibration_frozen.yaml",
)


def compose_candidate(rotation_report, translation_m, *, translation_uncertainty_m=None,
                      source_note=None, rotation_source=None):
    rotation = np.asarray(rotation_report.get("R_imu_lidar"), dtype=np.float64)
    translation = np.asarray(translation_m, dtype=np.float64).reshape(3)
    if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
        raise ValueError("rotation report needs a finite 3x3 R_imu_lidar")
    if not np.isfinite(translation).all():
        raise ValueError("translation must be three finite values")
    matrix = np.eye(4)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = translation
    forward, inverse = extrinsics(matrix)
    solved = rotation_report.get("status") == "SOLVED_CANDIDATE"
    uncertainty = None
    if translation_uncertainty_m is not None:
        uncertainty = float(translation_uncertainty_m)
        if not math.isfinite(uncertainty) or uncertainty < 0:
            raise ValueError("translation uncertainty must be finite and non-negative")
    result = {
        "schema": "na70-calibration-candidate-v1",
        "status": "CANDIDATE" if solved else "INCOMPLETE",
        "reason": None if solved else "rotation solver did not return SOLVED_CANDIDATE",
        "R_imu_lidar": forward[:3, :3].tolist() if solved else None,
        "t_imu_lidar_m": forward[:3, 3].tolist() if solved else None,
        "T_imu_lidar": forward.tolist() if solved else None,
        "T_lidar_imu": inverse.tolist() if solved else None,
        "time_offset_s": rotation_report.get("time_offset_s") if solved else None,
        "time_offset_resolution_s": rotation_report.get("time_offset_resolution_s") if solved else None,
        "rotation_provenance": {
            "source": rotation_source,
            "status": rotation_report.get("status"),
            "angle_deg": rotation_report.get("euler_zyx_deg"),
            "relative_residual": (rotation_report.get("residual") or {}).get("relative"),
            "observability": (rotation_report.get("observability") or {}).get("status"),
        },
        "translation_provenance": {
            "method": "mechanical_measurement",
            "uncertainty_m": uncertainty,
            "note": source_note,
        },
        "gate": {
            "rotation_candidate": solved,
            "translation_measured": True,
            "holdout_validated": False,
            "requires": "A8 独立留出动作与墙面/立柱对照；未通过前不得写 calibration_frozen.yaml",
        },
        "claims": {"calibration_validated": False, "time_sync_validated": False},
        "limitations": list(LIMITATIONS),
    }
    return result
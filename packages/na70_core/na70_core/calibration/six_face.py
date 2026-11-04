"""Six-face accelerometer check: bias, scale and cross-axis terms.

Model (per static face): ``a_meas = M @ (g * d) + b`` where ``d`` is the unit
body-frame direction that points *up* and ``g`` is local gravity.  Six faces
give the classic ``(a+ + a-) / 2 = b`` and ``(a+ - a-) / (2g) = M e_i``
estimates; the least-squares form used here also accepts repeated faces or
extra known orientations.

This recovers accelerometer *candidate* parameters only.  It cannot separate a
sensor bias from a mounting tilt that was wrong in the recorded ``d``, and it
does not observe gyroscope scale.  Applying the correction to LIO input is a
separate, reviewed step.
"""
from __future__ import annotations

import math

import numpy as np


LIMITATIONS = (
    "六面结果依赖记录时声明的朝向；摆放/工装误差会混入 bias 与 M",
    "只覆盖加速度计，陀螺尺度与轴间误差需要独立角速度基准",
    "同一姿态不能同时解出偏置、尺度和绝对航向",
    "候选参数须经留出静置与动态数据验证后才能应用到 LIO 输入",
)


def _directions(values):
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 3 or len(array) == 0:
        raise ValueError("directions must be a non-empty Nx3 array")
    if not np.isfinite(array).all():
        raise ValueError("directions contain non-finite values")
    norms = np.linalg.norm(array, axis=1)
    if np.any(norms <= 0):
        raise ValueError("directions must be non-zero")
    if np.any(np.abs(norms - 1.0) > 1e-3):
        raise ValueError("directions must be unit vectors")
    return array / norms[:, None]


def _measurements(values):
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 3 or len(array) == 0:
        raise ValueError("measurements must be a non-empty Nx3 array")
    if not np.isfinite(array).all():
        raise ValueError("measurements contain non-finite values")
    return array


def solve(measurements, directions, *, gravity=9.80665, max_residual_m_s2=0.5):
    """Least-squares accelerometer ``M`` and ``b`` from known static faces.

    ``measurements`` are mean specific-force vectors in m/s^2, ``directions``
    the matching body-frame "up" unit vectors.  Returns a JSON-ready report
    with ``status`` and a ``transform`` block; ``M`` and ``b`` are ``None``
    when the design matrix is rank deficient.
    """
    accel = _measurements(measurements)
    up = _directions(directions)
    if len(accel) != len(up):
        raise ValueError("measurements and directions must have equal length")
    gravity = float(gravity)
    if not math.isfinite(gravity) or gravity <= 0:
        raise ValueError("gravity must be finite and positive")
    if not math.isfinite(max_residual_m_s2) or max_residual_m_s2 <= 0:
        raise ValueError("max_residual_m_s2 must be finite and positive")

    count = len(accel)
    design = np.zeros((3 * count, 12), dtype=np.float64)
    for index in range(count):
        design[3 * index:3 * index + 3, :9] = gravity * np.kron(np.eye(3), up[index][None, :])
        design[3 * index:3 * index + 3, 9:] = np.eye(3)
    target = accel.reshape(-1)
    solution, _, rank, singular = np.linalg.lstsq(design, target, rcond=None)
    matrix = solution[:9].reshape(3, 3)
    bias = solution[9:]
    predicted = gravity * (up @ matrix.T) + bias
    residual = accel - predicted
    residual_norm = np.linalg.norm(residual, axis=1)
    rms = float(np.sqrt(np.mean(residual_norm ** 2)))
    condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else float("inf")

    scale = np.diag(matrix)
    degenerate_scale = bool(np.any(np.abs(scale) < 1e-9))
    if degenerate_scale:
        off_diagonal = None
    else:
        cross_axis = matrix / scale[:, None]
        np.fill_diagonal(cross_axis, 0.0)
        off_diagonal = float(np.max(np.abs(cross_axis)))

    faces = []
    for direction in up:
        dominant = int(np.argmax(np.abs(direction)))
        axis = ("x", "y", "z")[dominant]
        sign = "+" if direction[dominant] > 0 else "-"
        faces.append(f"{sign}{axis}")

    reasons = []
    if rank < 12:
        reasons.append("design_rank_deficient")
    if not np.isfinite(condition) or condition > 1e6:
        reasons.append("design_ill_conditioned")
    if rms > max_residual_m_s2:
        reasons.append("residual_exceeds_limit")
    if len(set(faces)) < len(faces):
        reasons.append("duplicate_face_direction")
    if degenerate_scale:
        reasons.append("scale_near_zero")
    missing = [face for face in ("+x", "-x", "+y", "-y", "+z", "-z") if face not in faces]
    if missing:
        reasons.append("missing_faces:" + ",".join(missing))
    status = "SOLVED_CANDIDATE" if not reasons else ("INSUFFICIENT_DATA" if rank < 12 else "POOR_FIT")

    inverse = None
    if rank == 12:
        inverse = np.linalg.inv(matrix)
    return {
        "schema": "na70-imu-six-face-v1",
        "status": status,
        "reasons": reasons,
        "gravity_m_s2": gravity,
        "faces": faces,
        "transform": {
            "model": "a_meas = M @ (g * d) + b",
            "M": matrix.tolist(),
            "bias_m_s2": bias.tolist(),
            "scale": scale.tolist(),
            "cross_axis": off_diagonal,
            "inverse_M": inverse.tolist() if inverse is not None else None,
            "application": "a_true = inverse_M @ (a_meas - bias_m_s2)",
        },
        "residual": {
            "rms_m_s2": rms,
            "per_face_m_s2": residual_norm.tolist(),
            "predicted_m_s2": predicted.tolist(),
            "measured_norm_m_s2": np.linalg.norm(accel, axis=1).tolist(),
        },
        "design": {"rows": int(design.shape[0]), "columns": 12, "rank": int(rank),
                   "condition": condition},
        "observability": {"faces": sorted(set(faces)), "missing_faces": missing,
                          "distinct_directions": len(set(faces))},
        "limitations": list(LIMITATIONS),
    }


def correct_accel(accel, matrix, bias):
    """Apply ``a_true = M^-1 (a_meas - b)`` to an Nx3 array."""
    values = np.asarray(accel, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 3:
        raise ValueError("accel must be Nx3")
    if not np.isfinite(values).all():
        raise ValueError("accel contains non-finite values")
    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError("matrix must be a finite 3x3")
    if not np.isclose(np.linalg.det(matrix), 0.0):
        inverse = np.linalg.inv(matrix)
    else:
        raise ValueError("matrix is singular")
    bias = np.asarray(bias, dtype=np.float64).reshape(3)
    if not np.isfinite(bias).all():
        raise ValueError("bias must be three finite values")
    corrected = (values - bias) @ inverse.T
    return corrected
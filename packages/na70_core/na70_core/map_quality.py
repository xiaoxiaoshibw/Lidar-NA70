"""Map quality checks: map-to-map consistency, plane residuals, control points.

These checks need an independent reference.  A low map-to-map distance only
means two reconstructions agree; a low plane residual only means the surface is
consistent with the supplied plane.  Neither is absolute accuracy.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np


LIMITATIONS = (
    "地图间一致性不等于绝对准确度；两次重建可能共享同一系统误差",
    "平面残差依赖参考平面的来源与不确定度",
    "控制点距离只覆盖采样到的位置，不外推整图精度",
    "未做点云配准；比较前两侧必须已在同一坐标系",
)

_PLY_TYPES = {
    "float": "<f4", "float32": "<f4", "double": "<f8", "float64": "<f8",
    "uchar": "u1", "uint8": "u1", "char": "i1", "int8": "i1",
    "ushort": "<u2", "uint16": "<u2", "short": "<i2", "int16": "<i2",
    "uint": "<u4", "uint32": "<u4", "int": "<i4", "int32": "<i4",
}


def load_ply(path):
    """Read a binary little-endian PLY and return a structured array."""
    path = Path(path)
    with path.open("rb") as source:
        header = []
        while True:
            line = source.readline()
            if not line:
                raise ValueError("PLY header ended unexpectedly")
            text = line.decode("ascii", errors="replace").strip()
            header.append(text)
            if text == "end_header":
                break
        if not header or header[0] != "ply":
            raise ValueError("not a PLY file")
        if not any(line.startswith("format binary_little_endian") for line in header):
            raise ValueError("only binary little-endian PLY is supported")
        count = None
        properties = []
        for line in header:
            if line.startswith("element vertex "):
                count = int(line.split()[2])
            elif line.startswith("property ") and count is not None:
                parts = line.split()
                if len(parts) != 3 or parts[2] == "list":
                    raise ValueError(f"unsupported PLY property: {line}")
                properties.append((parts[2], parts[1]))
        if count is None or not properties:
            raise ValueError("PLY has no vertex element")
        missing = {"x", "y", "z"} - {name for name, _ in properties}
        if missing:
            raise ValueError(f"PLY missing {','.join(sorted(missing))}")
        dtype = np.dtype([(name, _PLY_TYPES[kind]) for name, kind in properties])
        data = np.fromfile(source, dtype=dtype, count=count)
        if len(data) != count:
            raise ValueError("PLY vertex data is truncated")
    return data


def _points(array, name):
    array = np.asarray(array, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] < 3 or len(array) == 0:
        raise ValueError(f"{name} must be a non-empty Nx3+ array")
    if not np.isfinite(array[:, :3]).all():
        raise ValueError(f"{name} contains non-finite points")
    return array[:, :3]


def nearest_distances(query, reference):
    """One-sided nearest-neighbour distances using a KD-tree (SciPy)."""
    query = _points(query, "query")
    reference = _points(reference, "reference")
    try:
        from scipy.spatial import cKDTree
    except ImportError as exc:  # pragma: no cover - venv always has scipy
        raise RuntimeError("map quality checks require scipy") from exc
    distances, _ = cKDTree(reference).query(query, k=1, workers=-1)
    return np.asarray(distances, dtype=np.float64)


def _summary(distances, threshold):
    distances = np.asarray(distances, dtype=np.float64)
    return {
        "points": int(len(distances)),
        "p50_m": float(np.percentile(distances, 50)),
        "p95_m": float(np.percentile(distances, 95)),
        "max_m": float(distances.max()),
        "within_threshold": int((distances <= threshold).sum()),
        "within_fraction": float((distances <= threshold).mean()),
        "threshold_m": float(threshold),
    }


def map_consistency(map_points, reference_points, *, threshold_m=0.1):
    """Symmetric map-to-map distances (both directions)."""
    if not isinstance(threshold_m, (int, float)) or not np.isfinite(threshold_m) or threshold_m <= 0:
        raise ValueError("threshold_m must be a finite positive number")
    forward = nearest_distances(map_points, reference_points)
    backward = nearest_distances(reference_points, map_points)
    return {
        "schema": "na70-map-consistency-v1",
        "status": "EVALUATED",
        "kind": "REFERENCE_CONSISTENCY",
        "forward": _summary(forward, threshold_m),
        "backward": _summary(backward, threshold_m),
        "limitations": list(LIMITATIONS),
        "claims": {"absolute_accuracy_validated": False},
    }


def plane_residuals(points, plane):
    """Signed/absolute distance to one plane ``{point, normal}``."""
    data = _points(points, "points")
    point = np.asarray(plane.get("point"), dtype=np.float64).reshape(-1)
    normal = np.asarray(plane.get("normal"), dtype=np.float64).reshape(-1)
    if point.shape != (3,) or normal.shape != (3,) or not (np.isfinite(point).all() and np.isfinite(normal).all()):
        raise ValueError("plane needs finite point and normal of length three")
    norm = np.linalg.norm(normal)
    if norm <= 0:
        raise ValueError("plane normal must be non-zero")
    normal = normal / norm
    signed = (data - point) @ normal
    return {"signed_m": signed, "absolute_m": np.abs(signed)}


def plane_evaluation(points, planes, *, threshold_m=0.1):
    if not isinstance(planes, list) or not planes:
        raise ValueError("planes must be a non-empty list")
    data = _points(points, "points")
    reports = []
    for index, plane in enumerate(planes):
        subset = data
        bounds = plane.get("bounds")
        selected = len(data)
        if bounds is not None:
            minimum = np.asarray(bounds.get("minimum"), dtype=np.float64).reshape(-1)
            maximum = np.asarray(bounds.get("maximum"), dtype=np.float64).reshape(-1)
            if (minimum.shape != (3,) or maximum.shape != (3,)
                    or not (np.isfinite(minimum).all() and np.isfinite(maximum).all())
                    or np.any(minimum >= maximum)):
                raise ValueError(f"plane {index}: bounds must be a finite min<max box")
            mask = np.all((data >= minimum) & (data <= maximum), axis=1)
            subset = data[mask]
            selected = int(mask.sum())
        if selected < 3:
            reports.append({"index": index, "name": plane.get("name"),
                            "status": "INSUFFICIENT_POINTS", "selected_points": selected})
            continue
        result = plane_residuals(subset, plane)
        reports.append({"index": index, "name": plane.get("name"),
                        "status": "EVALUATED",
                        "selected_points": selected,
                        **_summary(result["absolute_m"], threshold_m),
                        "mean_signed_m": float(result["signed_m"].mean())})
    return {
        "schema": "na70-map-planes-v1",
        "status": "EVALUATED" if any(report["status"] == "EVALUATED" for report in reports)
        else "INSUFFICIENT_POINTS",
        "kind": "PLANE_RESIDUALS",
        "planes": reports,
        "note": "无 bounds 时平面残差会包含场景中所有点；应给每个平面独立 ROI。",
        "limitations": list(LIMITATIONS),
        "claims": {"absolute_accuracy_validated": False},
    }


def control_point_evaluation(map_points, control_points, *, threshold_m=0.2):
    distances = nearest_distances(control_points, map_points)
    return {
        "schema": "na70-map-control-points-v1",
        "status": "EVALUATED",
        "kind": "CONTROL_POINT_DISTANCE",
        "summary": _summary(distances, threshold_m),
        "note": "距离方向是控制点→地图点；控制点数量有限时不代表整图精度",
        "limitations": list(LIMITATIONS),
        "claims": {"absolute_accuracy_validated": False},
    }
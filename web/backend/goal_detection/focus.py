"""FOV 聚焦与 z 轴分析（staged 策略第 2/3 级）。

第 2 级（聚焦）：一旦跟踪器持有中心，所有检测只在该中心周围的方形 FOV
内进行——同一时间最多存在一个球门假设，也把叠加拖影排除在视野之外。
第 3 级（z 分析）：在 FOV 内用 z 直方图众数估地面，结合立柱顶高度得到
球门高度并修正包围盒的 z 中心与高度。叠加拖影会同时抬高立柱 base/top
的散布，而地面是 FOV 内点数最多的 z 带，众数比立柱端点更稳。
"""
from __future__ import annotations

import numpy as np


def crop_xy(points: np.ndarray, center_xy, half_size: float) -> np.ndarray:
    """Keep points whose x, y lie within ``half_size`` of ``center_xy``.

    空输入、非有限中心或非法半宽都返回空数组，不抛异常（每帧都会调用）。
    """
    points = np.asarray(points)
    if points.ndim != 2 or points.shape[0] == 0 or points.shape[1] < 2:
        return points.reshape(0, points.shape[1] if points.ndim == 2 else 0)
    center = np.asarray(center_xy, dtype=np.float64)
    if center.shape != (2,) or not np.isfinite(center).all() \
            or not np.isfinite(half_size) or half_size <= 0:
        return points[:0]
    keep = (np.abs(points[:, 0] - center[0]) <= half_size) \
        & (np.abs(points[:, 1] - center[1]) <= half_size)
    return points[keep]


def estimate_ground_z(z_values, bin_size: float, min_points: int) -> float | None:
    """Ground level as the (smoothed) mode of the z histogram, None if unsure."""
    z = np.asarray(z_values, dtype=np.float64).reshape(-1)
    z = z[np.isfinite(z)]
    if len(z) < int(min_points):
        return None
    lo = float(z.min())
    span = float(z.max()) - lo
    if span < bin_size:
        return float(np.median(z))
    bins = max(2, int(np.ceil(span / bin_size)))
    hist, edges = np.histogram(z, bins=bins, range=(lo, lo + bins * bin_size))
    # 三 bin 平滑后再取众数，避免单 bin 噪声抢峰。
    smooth = hist.astype(np.float64).copy()
    smooth[:-1] += 0.5 * hist[1:]
    smooth[1:] += 0.5 * hist[:-1]
    return float(edges[int(np.argmax(smooth))] + 0.5 * bin_size)


def refine_box_z(fov_xyz, left_pole: dict | None, right_pole: dict | None,
                 zrefine_cfg: dict) -> dict | None:
    """Refine a goal box's z extent from the FOV cloud: ground mode + pole tops.

    地面估计与立柱底差距超过 ``max_base_shift`` 时视为地面不可信，退回立柱
    底（例如 FOV 内主要是球门上部点、地面采样不足的情形）。
    """
    if fov_xyz is None or len(fov_xyz) == 0 or not left_pole or not right_pole:
        return None
    ground = estimate_ground_z(np.asarray(fov_xyz)[:, 2],
                               float(zrefine_cfg["bin_size"]),
                               int(zrefine_cfg["min_points"]))
    if ground is None:
        return None
    base = min(float(left_pole["base_z"]), float(right_pole["base_z"]))
    if abs(ground - base) > float(zrefine_cfg["max_base_shift"]):
        ground = base
    top = max(float(left_pole["top_z"]), float(right_pole["top_z"]))
    height = max(top - ground, float(zrefine_cfg.get("min_height", 0.5)))
    return {"ground_z": round(ground, 3), "top_z": round(top, 3),
            "height": round(height, 3),
            "center_z": round(ground + height / 2.0, 3)}

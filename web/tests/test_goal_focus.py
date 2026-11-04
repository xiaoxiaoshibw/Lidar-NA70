"""FOV 聚焦与 z 分析合成测试（SYNTHETIC）：裁剪、地面众数、包围盒 z 修正。"""
import numpy as np
import pytest

from backend.goal_detection.config import load_configuration
from backend.goal_detection.focus import crop_xy, estimate_ground_z, refine_box_z

CFG = load_configuration()
ZR = CFG["zrefine"]


def fov_cloud(ground_n=300, goal_n=60, ground_z=0.0, seed=5):
    rng = np.random.default_rng(seed)
    ground = np.column_stack((rng.uniform(-6, 6, ground_n),
                              rng.uniform(-6, 6, ground_n),
                              rng.normal(ground_z, 0.05, ground_n)))
    goal = np.column_stack((rng.uniform(-2, 2, goal_n),
                            rng.uniform(-4, 4, goal_n),
                            rng.uniform(0.4, 2.4, goal_n)))
    return np.vstack((ground, goal))


def test_crop_xy_selects_box():
    points = np.array([[0.0, 0.0, 0.0], [5.0, 0.0, 1.0], [0.0, -7.0, 2.0]])
    kept = crop_xy(points, [0.0, 0.0], 6.0)
    assert len(kept) == 2
    assert sorted(kept[:, 0].tolist()) == [0.0, 5.0]


def test_crop_xy_degenerate_inputs():
    empty = np.empty((0, 4))
    assert len(crop_xy(empty, [0, 0], 5.0)) == 0
    points = np.arange(12, dtype=np.float64).reshape(3, 4)
    assert len(crop_xy(points, [100.0, 100.0], 5.0)) == 0
    assert len(crop_xy(points, None, 5.0)) == 0
    assert len(crop_xy(points, [0.0, 0.0], -1.0)) == 0
    assert len(crop_xy(points, [np.nan, 0.0], 5.0)) == 0


def test_estimate_ground_z_finds_densest_band():
    ground = estimate_ground_z(fov_cloud()[:, 2], ZR["bin_size"], ZR["min_points"])
    assert ground is not None
    assert abs(ground - 0.125) <= ZR["bin_size"]  # 直方图 bin 中心


def test_estimate_ground_z_insufficient_points():
    assert estimate_ground_z(np.full(5, 0.0), ZR["bin_size"], ZR["min_points"]) is None
    assert estimate_ground_z(np.array([]), ZR["bin_size"], ZR["min_points"]) is None


def test_refine_box_z_uses_ground_mode():
    poles = ({"base_z": 0.45, "top_z": 2.44}, {"base_z": 0.5, "top_z": 2.4})
    info = refine_box_z(fov_cloud(), poles[0], poles[1], ZR)
    assert info is not None
    assert abs(info["ground_z"]) <= ZR["bin_size"]
    assert abs(info["height"] - 2.44) < 0.1
    assert abs(info["center_z"] - (info["ground_z"] + info["height"] / 2)) < 1e-6


def test_refine_box_z_falls_back_when_ground_untrusted():
    """FOV 内地面缺失（地面估计远离立柱底）时退回立柱底，不产出荒谬高度。"""
    high = fov_cloud(ground_z=8.0)  # 「地面」带在 8m——不是真地面
    poles = ({"base_z": 0.3, "top_z": 2.44}, {"base_z": 0.35, "top_z": 2.4})
    info = refine_box_z(high, poles[0], poles[1], ZR)
    assert info is not None
    assert abs(info["ground_z"] - 0.3) < 1e-6
    assert abs(info["height"] - (2.44 - 0.3)) < 1e-6


def test_refine_box_z_degenerate_inputs():
    poles = ({"base_z": 0.0, "top_z": 2.44}, {"base_z": 0.0, "top_z": 2.44})
    assert refine_box_z(None, *poles, zrefine_cfg=ZR) is None
    assert refine_box_z(np.empty((0, 3)), *poles, zrefine_cfg=ZR) is None
    assert refine_box_z(fov_cloud(), None, poles[1], ZR) is None
    # 点太少估不出地面
    assert refine_box_z(fov_cloud(ground_n=10, goal_n=5), *poles, ZR) is None

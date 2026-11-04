"""Pole extraction and pair matching primitives (SYNTHETIC)."""
import numpy as np
import pytest

from backend.goal_detection.config import load_configuration
from backend.goal_detection.geometry import (VerticalPole, bbox_for_pair, crossbar_coverage,
                                             find_poles, find_poles_neighbor,
                                             oriented_box_corners, pair_poles)

CFG = load_configuration()["goal_detection"]
CELL = CFG["cell_size"]


def test_find_poles_rejects_flat_and_wide():
    pole = np.column_stack((np.zeros(20), np.zeros(20), np.linspace(0, 2.4, 20)))
    flat = np.column_stack((np.linspace(20, 25, 20), np.zeros(20), np.zeros(20) + 2.0))
    diagnostics = {}
    poles = find_poles(np.vstack((pole, flat)), CFG["pole"], CELL,
                       diagnostics=diagnostics)
    assert len(poles) == 1
    assert poles[0].height == pytest.approx(2.4, abs=0.05)
    assert poles[0].count == 20
    assert diagnostics["input_points"] == 40
    assert diagnostics["output_poles"] == 1
    assert diagnostics["reason_counts"]


def test_find_poles_merges_adjacent_cells():
    # 一根斜柱跨两个格，应合并为一个候选
    z = np.linspace(0, 2.4, 30)
    tilted = np.column_stack((z * 0.3, np.zeros(30), z))
    poles = find_poles(tilted, CFG["pole"], CELL)
    assert len(poles) == 1
    assert poles[0].count == 30


def test_grid_offset_can_recover_boundary_split():
    # A narrow column straddles the zero-offset boundary; an offset grid must
    # recover the same vertical structure without changing point provenance.
    z = np.linspace(0, 2.4, 30)
    split = np.column_stack((np.full(len(z), 0.79), np.zeros(len(z)), z))
    base = find_poles(split, CFG["pole"], CELL, grid_offset=(0.0, 0.0))
    shifted = find_poles(split, CFG["pole"], CELL, grid_offset=(0.4, 0.0))
    assert len(shifted) >= len(base)
    assert shifted and shifted[0].count == len(split)


def test_local_3d_fallback_merges_grid_boundary_points():
    z = np.linspace(0, 2.4, 30)
    points = np.column_stack((0.78 + 0.08 * np.sin(z * 8),
                              0.05 * np.cos(z * 5), z))
    diagnostics = {}
    poles = find_poles_neighbor(points, CFG["pole"], 0.8, diagnostics)
    assert poles and poles[0].count == len(points)
    assert diagnostics["output_poles"] == 1


def test_local_3d_fallback_separates_crossbar_before_post_merge():
    z = np.linspace(0.35, 2.44, 18)
    left = np.column_stack((np.zeros(len(z)), np.full(len(z), -2.7), z))
    right = np.column_stack((np.zeros(len(z)), np.full(len(z), 2.7), z))
    bar_y = np.linspace(-2.7, 2.7, 30)
    bar = np.column_stack((np.zeros(len(bar_y)), bar_y,
                           np.full(len(bar_y), 2.44)))
    points = np.vstack((left, right, bar))
    diagnostics = {}
    poles = find_poles_neighbor(points, CFG["pole"], 0.8, diagnostics,
                                crossbar_z=CFG["frame"]["crossbar_z"])
    assert len(poles) == 2
    assert sorted(p.center_xy[1] for p in poles) == pytest.approx([-2.7, 2.7], abs=0.2)
    assert all(p.height >= CFG["pole"]["height"][0] for p in poles)
    assert set(poles[0].indices).isdisjoint(set(poles[1].indices))
    assert all(0 <= index < len(points) for p in poles for index in p.indices)
    assert diagnostics["crossbar_layer_points"] > 0
    assert diagnostics["unassigned_points"] > 0


def test_local_3d_fallback_recovers_filtered_post_base_from_ground_hint():
    # Accumulated 0.5 m voxels can leave the first retained post sample near
    # z=0.7 after the ground gate.  Recovering the known gate boundary keeps
    # the physical 1.8 m height rule unchanged.
    z = np.array([0.7, 1.0, 1.3, 1.7, 2.3])
    points = np.column_stack((np.zeros(len(z)), np.zeros(len(z)), z))
    diagnostics = {}
    poles = find_poles_neighbor(points, CFG["pole"], 0.8, diagnostics,
                                crossbar_z=CFG["frame"]["crossbar_z"],
                                base_z_hint=0.3)
    assert len(poles) == 1
    assert poles[0].base_z == pytest.approx(0.3)
    assert poles[0].height == pytest.approx(2.0)
    assert diagnostics["base_recovered_poles"] == 1


def test_find_poles_empty():
    assert find_poles(np.empty((0, 3)), CFG["pole"], CELL) == []
    ground = np.column_stack((np.linspace(0, 10, 50), np.zeros(50), np.full(50, 0.1)))
    assert find_poles(ground, CFG["pole"], CELL) == []


def test_pair_poles_width_and_height_rules():
    # 真值门宽 ~5.4m（用户 2026-09-19 确认）；7.3m 间距是树，宽度带必须拒绝
    left = VerticalPole(center_xy=np.array([100.0, -2.7]), base_z=0.0, top_z=2.44,
                        radius=0.2, count=30)
    right = VerticalPole(center_xy=np.array([100.0, 2.7]), base_z=0.0, top_z=2.44,
                         radius=0.2, count=30)
    assert pair_poles([left, right], CFG["pole"], CFG["frame"])
    # 高差超容差
    tall = VerticalPole(center_xy=right.center_xy, base_z=0.0, top_z=4.5, radius=0.2, count=30)
    assert not pair_poles([left, tall], CFG["pole"], CFG["frame"])
    # 间距过近
    close = VerticalPole(center_xy=np.array([100.0, 1.0]), base_z=0.0, top_z=2.44,
                         radius=0.2, count=30)
    assert not pair_poles([left, close], CFG["pole"], CFG["frame"])
    # 间距过远（树，7.32m 超出 [4.8, 6.5] 门宽带）
    far = VerticalPole(center_xy=np.array([100.0, 4.62]), base_z=0.0, top_z=2.44,
                       radius=0.2, count=30)
    assert not pair_poles([left, far], CFG["pole"], CFG["frame"])


def test_crossbar_coverage_full_and_empty():
    left = VerticalPole(center_xy=np.array([100.0, -3.66]), base_z=0.0, top_z=2.44,
                        radius=0.2, count=30)
    right = VerticalPole(center_xy=np.array([100.0, 3.66]), base_z=0.0, top_z=2.44,
                         radius=0.2, count=30)
    samples = np.linspace(0, 1, 41)
    bar = np.column_stack((np.full(41, 100.0), -3.66 + samples * 7.32,
                           np.full(41, 2.44)))
    assert crossbar_coverage(bar, left, right, CFG["frame"]["crossbar_z"]) > 0.9
    assert crossbar_coverage(np.empty((0, 3)), left, right, CFG["frame"]["crossbar_z"]) == 0.0
    # 横梁高度不在窗口内：顶部太高时直接 0 分
    high_left = VerticalPole(center_xy=left.center_xy, base_z=0.0, top_z=6.0,
                             radius=0.2, count=30)
    assert crossbar_coverage(bar, high_left, right, CFG["frame"]["crossbar_z"]) == 0.0


def test_bbox_for_pair_orientation_and_size():
    left = VerticalPole(center_xy=np.array([100.0, -3.66]), base_z=0.0, top_z=2.44,
                        radius=0.2, count=30)
    right = VerticalPole(center_xy=np.array([100.0, 3.66]), base_z=0.1, top_z=2.5,
                         radius=0.2, count=30)
    bbox = bbox_for_pair(left, right)
    assert bbox["size"][1] == pytest.approx(7.32, abs=0.01)
    assert bbox["yaw"] == pytest.approx(np.pi / 2, abs=0.01)
    assert bbox["center"][0] == pytest.approx(100.0)
    assert len(bbox["corners"]) == 8
    assert np.ptp(np.asarray(bbox["corners"])[:, 1]) == pytest.approx(7.32, abs=0.01)
    assert np.ptp(np.asarray(bbox["corners"])[:, 0]) == pytest.approx(0.4, abs=0.01)
    assert np.allclose(np.min(bbox["corners"], axis=0), bbox["minimum"])
    assert np.allclose(np.max(bbox["corners"], axis=0), bbox["maximum"])


@pytest.mark.parametrize("yaw", [0.0, np.pi / 6, np.pi / 2, -np.pi / 3])
def test_oriented_box_corners_match_yaw_and_aabb(yaw):
    center = np.array([25.0, -4.0, 1.2])
    corners = np.asarray(oriented_box_corners(center, [0.6, 5.4, 2.4], yaw))
    assert corners.shape == (8, 3)
    assert corners.mean(axis=0) == pytest.approx(center)
    assert corners[:, 2].min() == pytest.approx(0.0)
    assert corners[:, 2].max() == pytest.approx(2.4)
    width_axis = np.array([np.cos(yaw), np.sin(yaw)])
    projected = (corners[:, :2] - center[:2]) @ width_axis
    assert projected.min() == pytest.approx(-2.7)
    assert projected.max() == pytest.approx(2.7)

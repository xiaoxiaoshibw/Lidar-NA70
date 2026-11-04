"""SYNTHETIC unit tests for the brush-region goal geometry analysis."""
import numpy as np
import pytest

from backend.goal_detection.annotate_analysis import analyze_brush_region
from backend.goal_detection.config import load_configuration

CFG = load_configuration()["annotate_analysis"]
DET = load_configuration()["goal_detection"]


def goal_points(center_x=90.0, center_y=-20.0, yaw=0.0, half=2.6, seed=7):
    """An idealized goal: two posts (z 0–2.44) plus a crossbar and sparse net.

    默认 half=2.6 对应 5.2 m 真值门宽（goal_detection.frame.width 常规区间内）。"""
    rng = np.random.default_rng(seed)
    direction = np.array([np.cos(yaw), np.sin(yaw)])
    perp = np.array([-direction[1], direction[0]])
    left_xy = np.array([center_x, center_y]) - direction * half
    right_xy = np.array([center_x, center_y]) + direction * half
    points = []
    for post_xy in (left_xy, right_xy):
        angles = rng.uniform(0, 2 * np.pi, 300)
        radius = np.abs(rng.normal(0, 0.05, 300))
        base = post_xy[None, :] + np.stack([np.cos(angles), np.sin(angles)], axis=1) * radius[:, None]
        zs = rng.uniform(0, 2.44, 300)
        points.append(np.column_stack([base, zs]))
    along = np.linspace(-half, half, 200)
    bar_xy = np.array([center_x, center_y])[None, :] + direction[None, :] * along[:, None] \
        + perp[None, :] * rng.normal(0, 0.02, (200, 1))
    points.append(np.column_stack([bar_xy, np.full(200, 2.44) + rng.normal(0, 0.01, 200)]))
    net_xy = np.array([center_x, center_y])[None, :] - perp[None, :] * 0.35 \
        + direction[None, :] * rng.uniform(-half, half, (120, 1))
    points.append(np.column_stack([net_xy, rng.uniform(0, 2.2, 120)]))
    return np.vstack(points)


def test_measures_width_height_and_yaw_of_synthetic_goal():
    result = analyze_brush_region(goal_points(), [[[85.5, -20.0], [94.5, -20.0]]],
                                  1.0, CFG, DET)
    assert result["status"] == "ok", result
    box = result["box"]
    assert box["size"][1] == pytest.approx(5.2, abs=0.3)
    assert box["size"][2] == pytest.approx(2.44, abs=0.2)
    assert box["center"][0] == pytest.approx(90.0, abs=0.3)
    assert box["center"][1] == pytest.approx(-20.0, abs=0.3)
    assert box["center"][2] == pytest.approx(1.22, abs=0.25)
    # 主轴方向不定号：0 与 ±π 等价，都是沿 x 的门线。
    assert min(abs(box["yaw"]), abs(abs(box["yaw"]) - np.pi)) == pytest.approx(0.0, abs=0.05)
    assert box["size"][0] == pytest.approx(0.25, abs=0.25)
    assert result["checks"]["width_in_expected_range"] is True
    assert result["checks"]["height_in_expected_range"] is True
    assert result["warnings"] == []


def test_out_of_range_width_is_flagged_not_hidden():
    # 7.32 m 旧默认门宽超出当前常规区间：提案照常输出，但检查必须报警。
    result = analyze_brush_region(goal_points(half=3.66), [[[85.0, -20.0], [95.0, -20.0]]],
                                  1.0, CFG, DET)
    assert result["status"] == "ok"
    assert result["box"]["size"][1] == pytest.approx(7.32, abs=0.3)
    assert result["checks"]["width_in_expected_range"] is False
    assert any("宽度" in warning for warning in result["warnings"])


def test_rotated_goal_yaw_follows_post_line():
    result = analyze_brush_region(goal_points(center_x=50.0, center_y=10.0, yaw=np.pi / 2),
                                  [[[50.0, 5.5], [50.0, 14.5]]], 1.0, CFG, DET)
    assert result["status"] == "ok", result
    assert result["box"]["size"][1] == pytest.approx(5.2, abs=0.3)
    # 门线沿 y：±π/2 等价（轴向符号任意）。
    assert min(abs(result["box"]["yaw"] - np.pi / 2), abs(result["box"]["yaw"] + np.pi / 2)) \
        == pytest.approx(0.0, abs=0.05)


def test_paint_over_one_post_only_reports_degenerate_span():
    result = analyze_brush_region(goal_points(), [[[85.8, -20.0], [87.2, -20.0]]],
                                  1.0, CFG, DET)
    assert result["status"] == "degenerate_span"
    assert "box" not in result


def test_sparse_region_is_reported_not_fabricated():
    points = np.array([[90.0 + i * 0.1, -20.0, 1.0] for i in range(10)])
    result = analyze_brush_region(points, [[[89.0, -20.0], [98.0, -20.0]]], 1.0, CFG, DET)
    assert result["status"] == "insufficient_points"
    assert result["region_points"] == len(points)


def test_two_goals_in_one_group_split_into_two_proposals():
    # 复现真实误用：两个球筐画进同一组画笔。不能再合成一个 57m 的假球门。
    points = np.vstack([
        goal_points(center_x=60.0, center_y=-20.0, seed=3),
        goal_points(center_x=90.0, center_y=-20.0, seed=5),
    ])
    result = analyze_brush_region(points, [[[57.0, -20.0], [93.0, -20.0]]], 1.0, CFG, DET)
    assert result["status"] == "ok_multiple", result
    assert len(result["proposals"]) == 2
    widths = sorted(proposal["box"]["size"][1] for proposal in result["proposals"])
    assert widths[0] == pytest.approx(5.2, abs=0.4)
    assert widths[1] == pytest.approx(5.2, abs=0.4)
    centers = sorted(proposal["box"]["center"][0] for proposal in result["proposals"])
    assert centers[0] == pytest.approx(60.0, abs=0.5)
    assert centers[1] == pytest.approx(90.0, abs=0.5)
    for proposal in result["proposals"]:
        assert proposal["checks"]["width_in_expected_range"] is True


def test_single_goal_span_below_threshold_never_splits():
    # 只画两根立柱、不画球网：立柱间距 5.2m 低于拆分阈值，必须保持单一提案。
    posts = np.vstack([
        goal_points(center_x=90.0, center_y=-20.0)[np.abs(
            goal_points(center_x=90.0, center_y=-20.0)[:, 0] - 87.4) < 0.4],
        goal_points(center_x=90.0, center_y=-20.0)[np.abs(
            goal_points(center_x=90.0, center_y=-20.0)[:, 0] - 92.6) < 0.4],
    ])
    result = analyze_brush_region(posts, [[[85.5, -20.0], [94.5, -20.0]]], 1.0, CFG, DET)
    assert result["status"] in ("ok", "degenerate_span")
    assert result["status"] != "ok_multiple"


def test_brush_mask_is_capsule_not_infinite_band():
    # 与折线段共线但超出端点的点不属于画笔区域（半径 0.5 的端头圆盘之外）。
    points = np.array([[0.0, 0.0, 1.0], [0.4, 0.0, 1.0], [1.2, 0.0, 1.0], [0.0, 0.9, 1.0]])
    result = analyze_brush_region(points, [[[0.0, 0.0], [0.5, 0.0]]], 0.5, CFG, DET)
    assert result["region_points"] == 2  # 端点圆盘内的 (0,0) 与 (0.4,0)

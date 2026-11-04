"""BEV 采集级合成测试（SYNTHETIC）：双柱对提议、墙体否决、拖影容忍、窗口归一。"""
import numpy as np

from backend.goal_detection.bev import bev_regions
from backend.goal_detection.config import load_configuration

CFG = load_configuration()
POSTS = ((77.0, 3.0), (81.2, 6.6))
TRUE_CENTER = np.array([(77.0 + 81.2) / 2, (3.0 + 6.6) / 2])


def build_scene(frames=20, posts=POSTS, with_ground=True, with_net=True,
                post_points_per_frame=30, seed=11):
    """逐帧生成 XY 点后堆叠：立柱致密、门内低密度（网）、地面稀疏本底。"""
    rng = np.random.default_rng(seed)
    per_frame = []
    direction = np.array(posts[1], dtype=np.float64) - np.array(posts[0], dtype=np.float64)
    perp = np.array([-direction[1], direction[0]]) / np.linalg.norm(direction)
    for _ in range(frames):
        parts = [rng.normal(post, 0.2, size=(post_points_per_frame, 2)) for post in posts]
        if with_net:
            t = rng.uniform(0.15, 0.85, size=(4, 1))
            lateral = rng.uniform(-0.5, 0.5, size=(4, 1))
            parts.append(np.array(posts[0], dtype=np.float64)
                         + t * direction + lateral * perp)
        if with_ground:
            parts.append(np.column_stack((rng.uniform(60, 100, 130),
                                          rng.uniform(-15, 25, 130))))
        per_frame.append(np.vstack(parts))
    xyz = np.vstack([np.column_stack((frame, np.zeros(len(frame)))) for frame in per_frame])
    return xyz, frames


def regions_for(xyz, frames):
    return bev_regions(xyz, frames, CFG["bev"], CFG["goal_detection"]["frame"])


def add_wall(frames, x=90.0, seed=3):
    rng = np.random.default_rng(seed)
    parts = []
    for _ in range(frames):
        ys = np.arange(-10, 20, 0.3)
        parts.append(np.column_stack((np.full(len(ys), x) + rng.normal(0, 0.05, len(ys)), ys)))
    wall = np.vstack([np.column_stack((p, np.zeros(len(p)))) for p in parts])
    return wall


def test_pair_of_posts_found():
    xyz, frames = build_scene()
    regions = regions_for(xyz, frames)
    assert regions
    best = regions[0]
    assert np.linalg.norm(best.center_xy - TRUE_CENTER) < 1.2
    assert abs(best.width - 5.53) < 0.8
    assert best.contrast >= CFG["bev"]["min_contrast"]


def test_wall_alone_yields_no_region():
    xyz = add_wall(20)
    diagnostics = {}
    assert bev_regions(xyz, 20, CFG["bev"], CFG["goal_detection"]["frame"],
                       diagnostics=diagnostics) == []
    assert diagnostics["input_points"] == len(xyz)
    assert diagnostics["reason_counts"]
    assert diagnostics["output_regions"] == 0


def test_region_prefers_posts_over_wall():
    goal, frames = build_scene()
    xyz = np.vstack((goal, add_wall(frames)))
    regions = regions_for(xyz, frames)
    assert regions
    assert np.linalg.norm(regions[0].center_xy - TRUE_CENTER) < 1.5


def test_window_length_normalization():
    """密度按帧归一：同样的每帧场景，窗口 10 与 20 都应检出。"""
    for frames in (10, 20):
        xyz, _ = build_scene(frames=frames, seed=5 + frames)
        assert regions_for(xyz, frames), f"frames={frames} 未检出"


def test_below_min_window_gated():
    xyz, _ = build_scene()
    assert regions_for(xyz, CFG["bev"]["min_window_frames"] - 1) == []


def test_ghost_smear_tolerated():
    """时变 yaw 拖影：立柱沿弧线拉长成多格 blob，仍应给出一个区域。"""
    smeared = (POSTS[0], (POSTS[0][0] + 1.5, POSTS[0][1]),
               POSTS[1], (POSTS[1][0] + 1.5, POSTS[1][1]))
    xyz, frames = build_scene(posts=smeared, post_points_per_frame=18, seed=17)
    regions = regions_for(xyz, frames)
    assert regions
    assert min(np.linalg.norm(r.center_xy - TRUE_CENTER) for r in regions) < 1.5


def test_empty_and_nan_input():
    assert regions_for(np.empty((0, 3)), 20) == []
    assert regions_for(np.full((10, 3), np.nan), 20) == []


def test_regions_sorted_and_deduplicated():
    goal, frames = build_scene()
    regions = regions_for(goal, frames)
    scores = [r.score for r in regions]
    assert scores == sorted(scores, reverse=True)
    for i, a in enumerate(regions):
        for b in regions[i + 1:]:
            assert np.linalg.norm(a.center_xy - b.center_xy) > CFG["bev"]["nms_distance"]

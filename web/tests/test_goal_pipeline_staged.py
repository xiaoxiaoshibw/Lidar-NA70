"""staged 策略管线合成测试（SYNTHETIC）：BEV 采集→FOV 聚焦→z 修正→单假设跟踪。"""
import numpy as np

from backend.goal_detection.config import load_configuration
from backend.goal_detection.pipeline import GoalDemoPipeline

WIDTH = 5.4


def scene_frame(rng, with_goal=True, ground=300, intensity=100.0):
    """一帧：(75, 0) 处标准球门 + 稀疏地面本底；地面 z=0，立柱 z 0–2.44。"""
    parts = []
    if with_goal:
        for py in (-WIDTH / 2, WIDTH / 2):
            z = np.arange(0.0, 2.44, 0.15)
            xy = rng.normal([75.0, py], 0.03, size=(len(z), 2))
            parts.append(np.column_stack((xy, z)))
        bar_t = np.linspace(0.1, 0.9, 20)
        parts.append(np.column_stack((np.full(len(bar_t), 75.0),
                                      -WIDTH / 2 + bar_t * WIDTH,
                                      np.full(len(bar_t), 2.44))))
    gx = rng.uniform(55, 95, ground)
    gy = rng.uniform(-20, 20, ground)
    parts.append(np.column_stack((gx, gy, np.zeros(ground))))
    points = np.vstack(parts)
    return np.column_stack((points, np.full(len(points), intensity)))


def run(frames, seed=7, overrides=None):
    # 真值几何（用户 2026-09-19 确认）：球筐立柱对宽约 5.2–5.5m，7.3m 间距的
    # 是树（负样本）。WIDTH=5.4 与 goal_detection.yaml 的门宽带 [4.8, 6.5] 一致。
    # voxel 0.25：0.5m 体素会把 2.44m 干净立柱的底部体素带质心压到地面门限
    # 之下（z>0.3 被剔除），有效 z 跨度跌破立柱最小高度——合成太干净会踩中
    # 该量化边界，真实立柱回波 z 向更散不受影响。
    cfg = load_configuration(overrides=overrides
                             or {"accumulation": {"window_size": 8, "voxel_size": 0.25}})
    pipeline = GoalDemoPipeline(cfg)
    rng = np.random.default_rng(seed)
    results = []
    for index in range(frames):
        with_goal = index >= 6  # 前 6 帧：只有地面，球门尚未进入视野
        results.append(pipeline.process_frame(scene_frame(rng, with_goal=with_goal), index))
    return results


def test_staged_pipeline_locks_single_goal():
    results = run(10)
    last = results[-1]
    assert last["strategy"] == "staged"
    # 采集级真的跑过：球门出现那一帧（或下一帧）给出 BEV 区域提议
    assert any(r["bev_regions"] for r in results[6:])
    # 提议后 FOV 收缩
    assert last["focus"]["active"] is True
    assert last["focus"]["center"] is not None
    # 锁定单一球门
    goal = last["goal"]
    assert goal["detected"]
    assert goal["state"] in ("LOCKING", "LOCKED", "TRACKING")
    assert goal["id"] == 1
    assert abs(goal["center"][0] - 75.0) < 1.5
    assert abs(goal["center"][1]) < 2.0


def test_staged_z_refine_corrects_height():
    results = run(10)
    last = results[-1]
    assert last["z_refine"] is not None
    # 地面 z≈0（FOV 内点数最多的 z 带），高度=立柱顶-地面 ≈ 2.44
    assert abs(last["z_refine"]["ground_z"]) < 0.4
    assert abs(last["z_refine"]["height"] - 2.44) < 0.2
    assert abs(last["goal"]["center"][2] - 1.22) < 0.6
    assert last["selected_measurement"] is not None
    assert last["selected_measurement"]["box_role"] == "measurement"
    assert last["selected_measurement"]["corners"]
    assert last["goal"]["box_role"] == "track_estimate"
    assert last["goal"]["measurement_frame_index"] <= last["frame_index"]
    assert all(stage["latency_ms"] is None or stage["latency_ms"] >= 0
               for stage in last["execution_trace"]["accumulated"])
    assert all(stage["latency_ms"] is None
               for stage in last["execution_trace"]["accumulated"]
               if stage["status"] == "skipped")


def test_accumulated_candidate_support_resolves_to_original_frames():
    results = run(10)
    candidate = results[-1]["accumulated_candidates"][0]
    refs = candidate["supporting_raw_point_refs"]
    assert refs
    assert all(len(ref) == 2 for ref in refs)
    assert all(0 <= frame <= 9 and point >= 0 for frame, point in refs)
    assert candidate["source_cloud_id"] == results[-1]["accumulated_cloud_id"]


def test_staged_acquisition_silent_before_goal():
    results = run(10)
    for result in results[:6]:
        assert result["bev_regions"] == []
        assert result["accumulated_candidates"] == []
        assert result["goal"]["detected"] is False


def test_poles_strategy_keeps_legacy_behavior():
    results = run(10, overrides={"strategy": {"mode": "poles"},
                                 "accumulation": {"window_size": 8}})
    last = results[-1]
    assert last["strategy"] == "poles"
    assert last["focus"] is None
    assert last["bev_regions"] == []
    # 旧策略在球门出现后全图检测直接给候选
    assert any(r["accumulated_candidates"] for r in results[6:])
    assert last["goal"]["detected"]


def moving_sensor_frame(rng, frame_index, seed_offset=0):
    """SYNTHETIC static world observed by a translating/rotating LiDAR.

    The target is fixed in world coordinates; only sensor coordinates move.
    A tall broad tree and a building-like wall exercise the height/blob reject
    gates.  This is deliberately short-horizon motion, not world localisation.
    """
    world = scene_frame(rng, with_goal=True, ground=500)[:, :3]
    tree_z = np.linspace(0, 9, 90)
    tree = np.column_stack((
        rng.normal(68, 1.4, len(tree_z)),
        rng.normal(11, 1.4, len(tree_z)), tree_z))
    wall_y = np.repeat(np.linspace(-16, -7, 18), 8)
    wall = np.column_stack((np.full(len(wall_y), 83.0), wall_y,
                            np.tile(np.linspace(0, 7, 8), 18)))
    world = np.vstack((world, tree, wall))
    yaw = np.deg2rad(0.32 * frame_index + 0.03 * seed_offset)
    translation = np.array([0.14 * frame_index, 0.04 * frame_index])
    relative = world[:, :2] - translation
    c, s = np.cos(yaw), np.sin(yaw)
    sensor_xy = relative @ np.array([[c, -s], [s, c]])
    sensor = np.column_stack((sensor_xy, world[:, 2]))
    return np.column_stack((sensor, np.full(len(sensor), 100.0)))


def test_short_horizon_sensor_motion_keeps_one_track_across_random_scenes():
    """SYNTHETIC randomized mild translation+yaw keeps a stable sensor-frame ID."""
    for seed in range(5):
        cfg = load_configuration(overrides={
            "accumulation": {"window_size": 8, "voxel_size": 0.25},
            "tracking": {"max_center_jump": 6.5},
        })
        pipeline = GoalDemoPipeline(cfg)
        rng = np.random.default_rng(100 + seed)
        ids = []
        centers = []
        for index in range(16):
            result = pipeline.process_frame(moving_sensor_frame(rng, index, seed), index)
            goal = result["goal"]
            if goal.get("id", 0) > 0:
                ids.append(goal["id"])
                centers.append(goal["center"])
        assert ids, f"seed {seed}: target never locked"
        assert set(ids) == {1}, f"seed {seed}: track ID changed: {ids}"
        assert len(centers) >= 5
        # The fixed world goal legitimately moves in sensor coordinates; the
        # tracker should follow continuously rather than freeze a world claim.
        steps = np.linalg.norm(np.diff(np.asarray(centers)[:, :2], axis=0), axis=1)
        assert float(steps.max(initial=0.0)) < 3.0

"""Facade strategy: ROI facade measurement and tracking (SYNTHETIC).

Regression context (20260920 root-cause audit): on the real static window the
grid pole extractor failed with per-cell ``height_too_short`` and the cluster
fallback failed with ``seed_radius_too_large`` because the net connects both
posts into one wide 3-D component.  The facade detector measures the goal as
a facade plane with density/coverage boundaries instead, so those fixtures
must now detect.
"""
import numpy as np
import pytest

from backend.goal_detection.config import load_configuration
from backend.goal_detection.facade import FacadeGoalDetector
from backend.goal_detection.geometry import find_poles, find_poles_neighbor
from backend.goal_detection.pipeline import GoalDemoPipeline
from backend.goal_detection.synthetic import _ground_points, netted_goal_points

CFG = load_configuration()


def detector() -> FacadeGoalDetector:
    return FacadeGoalDetector(CFG["goal_detection"])


def facade_pipeline(window_size: int = 10) -> GoalDemoPipeline:
    cfg = load_configuration(overrides={
        "accumulation": {"window_size": window_size},
        "strategy": {"mode": "facade"},
    })
    cfg["accumulation"]["roi"] = [65.0, 85.0, -10.0, 10.0, -2.0, 6.0]
    return GoalDemoPipeline(cfg)


def netted_frame(rng, seed_offset: float = 0.0, ground_count: int = 400):
    goal = netted_goal_points(rng, np.array([75.0 + seed_offset, 0.0, 1.22]), 5.4, 2.44)
    ground = _ground_points(rng, ground_count)
    world = np.vstack([goal, ground])
    xyz = world + rng.normal(0.0, 0.025, world.shape)
    return np.column_stack((xyz, rng.uniform(60.0, 180.0, len(xyz)))).astype(np.float32)


def test_detects_netted_goal_with_measured_size():
    rng = np.random.default_rng(3)
    points = netted_goal_points(rng, np.array([75.0, 0.0, 1.22]), 5.4, 2.44)
    cloud = np.column_stack((points, rng.uniform(60, 180, len(points))))
    det = detector()
    det.set_range(50.0, 130.0)
    candidates = det.detect(cloud)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.accepted
    width, height = candidate.size[1], candidate.size[2]
    assert abs(width - 5.4) <= 0.5
    assert abs(height - 2.44) <= 0.3
    assert abs(candidate.center[0] - 75.0) <= 0.3
    assert abs(candidate.center[1]) <= 0.3
    assert candidate.confidence >= CFG["goal_detection"]["scoring"]["lock_confidence"]
    facade = candidate.score_breakdown["facade"]
    assert facade["left"]["count"] >= CFG["goal_detection"]["facade"]["edge_min_points"]
    assert facade["right"]["count"] >= CFG["goal_detection"]["facade"]["edge_min_points"]
    assert facade["top"]["coverage"] >= CFG["goal_detection"]["facade"]["top_min_coverage"]
    # 左右立边和上沿分别具有真实支撑（G2 门禁语义）
    assert facade["left"]["span"] >= CFG["goal_detection"]["facade"]["edge_min_height"]
    assert facade["right"]["span"] >= CFG["goal_detection"]["facade"]["edge_min_height"]


def test_real_failure_regression_net_connects_posts():
    """20260920 audit blocker: a net filling the mouth merges both posts into
    one wide 3-D component (real data killed the grid path with per-cell
    ``height_too_short`` and the fallback with ``seed_radius_too_large``).
    The facade detector must measure such a goal without any 3-D
    connectivity."""
    rng = np.random.default_rng(11)
    points = netted_goal_points(rng, np.array([75.0, 0.0, 1.22]), 5.4, 2.44)
    det = detector()
    candidates = det.detect(points)
    assert len(candidates) == 1
    assert candidates[0].accepted
    assert abs(candidates[0].size[1] - 5.4) <= 0.5


def test_one_missing_post_is_not_a_complete_goal():
    rng = np.random.default_rng(3)
    center = np.array([75.0, 0.0, 1.22])
    width, height = 5.4, 2.44
    count = 20
    z = np.linspace(center[2] - height / 2, center[2] + height / 2, count)
    left = np.column_stack((rng.normal([center[0], center[1] - width / 2], 0.15,
                                       size=(count, 2)), z))
    bar_y = np.linspace(center[1] - width / 2, center[1], 15)
    bar = np.column_stack((rng.normal(center[0], 0.15, 15), bar_y,
                           np.full(15, center[2] + height / 2)))
    net_count = 160
    net_y = rng.uniform(center[1] - width / 2, center[1] + width / 2, net_count)
    net_z = rng.uniform(center[2] - height / 2 + 0.05, center[2] + height / 2, net_count)
    net = np.column_stack((rng.normal(center[0], 0.35, net_count), net_y, net_z))
    det = detector()
    candidates = det.detect(np.vstack([left, bar, net]))
    assert candidates == []
    assert all(not candidate.accepted for candidate in det.last_rejected)


def test_background_clutter_yields_nothing():
    rng = np.random.default_rng(5)
    count = 300
    clutter = np.column_stack((
        rng.uniform(70, 80, count), rng.uniform(-8, 8, count),
        rng.uniform(0.3, 3.0, count)))
    det = detector()
    assert det.detect(clutter) == []


def test_width_gate_rejects_oversized_structure():
    rng = np.random.default_rng(6)
    huge = netted_goal_points(rng, np.array([75.0, 0.0, 1.6]), 15.0, 3.0, density=3.0)
    det = detector()
    assert det.detect(huge) == []
    assert det.last_rejected \
        and det.last_rejected[0].reject_reason == "width_out_of_band"


def test_empty_and_nan_input_is_safe():
    det = detector()
    assert det.detect(np.empty((0, 4), dtype=np.float32)) == []
    cloud = np.full((10, 4), np.nan, dtype=np.float32)
    assert det.detect(cloud) == []


def test_low_confidence_never_accepted():
    rng = np.random.default_rng(9)
    sparse = netted_goal_points(rng, np.array([75.0, 0.0, 1.22]), 5.4, 2.44, density=0.12)
    det = detector()
    candidates = det.detect(sparse)
    assert all(not candidate.accepted for candidate in candidates)


def test_pipeline_locks_and_tracks_netted_goal():
    pipeline = facade_pipeline()
    rng = np.random.default_rng(7)
    states = []
    for index in range(25):
        result = pipeline.process_frame(netted_frame(rng), index,
                                        device_time=index * 0.1,
                                        motion_status="confirmed_static")
        states.append(result["goal"]["state"])
    assert "LOCKED" in states or "TRACKING" in states
    assert states[-1] in ("LOCKED", "TRACKING")
    assert result["goal"]["id"] == 1
    center = result["goal"]["center"]
    assert abs(center[0] - 75.0) <= 0.5
    assert abs(center[1]) <= 0.5
    tracked = [index for index, state in enumerate(states)
               if state in ("LOCKED", "TRACKING")]
    assert len(tracked) >= 15


def test_pipeline_confirmation_uses_current_measurements():
    pipeline = facade_pipeline()
    rng = np.random.default_rng(7)
    roles = []
    for index in range(20):
        result = pipeline.process_frame(netted_frame(rng), index,
                                        device_time=index * 0.1,
                                        motion_status="confirmed_static")
        measurement = result.get("selected_measurement")
        if result["goal"]["state"] in ("LOCKED", "TRACKING") and measurement:
            roles.append(measurement.get("evidence_role"))
    assert roles and set(roles) == {"MEASURED_CURRENT"}


def test_pipeline_accepts_roi_or_full_field():
    """roi 存在走人工 ROI 路径；roi 为空合法，走全场提案（构造不报错）。"""
    cfg = load_configuration(overrides={"strategy": {"mode": "facade"}})
    GoalDemoPipeline(cfg)  # roi null → full-field proposal
    cfg["accumulation"]["roi"] = [65.0, 85.0, -10.0, 10.0, -2.0, 6.0]
    GoalDemoPipeline(cfg)  # roi present → manual-ROI path


def test_pipeline_refuses_measurement_without_ground_reference():
    pipeline = facade_pipeline()
    rng = np.random.default_rng(7)
    for index in range(12):
        result = pipeline.process_frame(netted_frame(rng), index,
                                        device_time=index * 0.1,
                                        motion_status="unknown")
    assert result["single_candidates"] == []
    assert result["accumulated_candidates"] == []
    assert result["goal"]["state"] == "SEARCHING"
    stages = [stage.get("reason") for stage in result["execution_trace"]["accumulated"]]
    assert "no_ground_reference" in stages


def test_moving_goal_roi_can_use_current_full_field_ground():
    cfg = load_configuration(overrides={
        "accumulation": {"window_size": 10},
        "strategy": {"mode": "facade"},
        "goal_detection": {"z_leveling": {"reference_roi": "full_field"}},
    })
    cfg["accumulation"]["roi"] = [65.0, 85.0, -10.0, 10.0, -2.0, 6.0]
    pipeline = GoalDemoPipeline(cfg)
    rng = np.random.default_rng(37)
    for index in range(12):
        cloud = netted_frame(rng)
        cloud[:, 0] += index * 0.02
        cloud[:, 1] += index * 0.01
        result = pipeline.process_frame(cloud, index, device_time=index * .1,
                                        motion_status="moving")
    stages = result["execution_trace"]["accumulated"]
    assert not any(stage.get("reason") == "no_ground_reference" for stage in stages)
    ground = next(stage for stage in stages if stage["stage"] == "ground_leveling")
    assert ground["status"] == "completed"
    assert result["accumulated_candidates"] == []
    gated = [candidate for candidate in result["accumulated_rejected_candidates"]
             if candidate["reject_reason"] == "uncompensated_moving_window"]
    assert gated
    assert all(candidate["coordinate_frame"] == "unaligned_sensor_window"
               and candidate["evidence_role"] == "UNCOMPENSATED_MOTION"
               for candidate in gated)
    motion_gate = next(stage for stage in stages
                       if stage["stage"] == "motion_window_gate")
    assert motion_gate["rejected_entities"] == len(gated)


def test_top_clipped_by_band_is_not_a_crossbar():
    """A wall extending past the z band truncates at the ceiling; that flat
    cut must not pose as a measured top line (full-field false positive)."""
    rng = np.random.default_rng(4)
    wall_y = np.repeat(np.linspace(-10.0, 0.0, 20), 5)
    wall = np.column_stack((np.full(len(wall_y), 75.0), wall_y,
                            np.tile(np.linspace(0.0, 9.0, 5), 20)))
    det = detector()
    assert det.detect(wall) == []
    reasons = [stage.get("reason") for stage in det.last_trace]
    assert "top_clipped_by_band" in reasons or all(
        not candidate.accepted for candidate in det.last_rejected)


def test_full_field_proposal_finds_goal_among_distractors():
    """No-ROI facade mode: the goal is proposed out of the full cloud and
    measured; tree/wall regions do not yield accepted candidates."""
    from backend.goal_detection.synthetic import _distractors
    cfg = load_configuration(overrides={"strategy": {"mode": "facade"}})
    pipeline = GoalDemoPipeline(cfg)  # roi 保持 null → 全场提案
    rng = np.random.default_rng(8)
    states = []
    for index in range(25):
        goal = netted_goal_points(rng, np.array([75.0, 0.0, 1.22]), 5.4, 2.44)
        ground = _ground_points(rng, 400)
        world = np.vstack([goal, ground, *_distractors(rng, "tree_wall_fence")])
        xyz = world + rng.normal(0.0, 0.025, world.shape)
        points = np.column_stack((xyz, rng.uniform(60, 180, len(xyz)))).astype(np.float32)
        result = pipeline.process_frame(points, index, device_time=index * 0.1,
                                        motion_status="confirmed_static")
        states.append(result["goal"]["state"])
    assert states[-1] in ("LOCKED", "TRACKING")
    center = result["goal"]["center"]
    assert abs(center[0] - 75.0) <= 0.8
    assert abs(center[1]) <= 0.8

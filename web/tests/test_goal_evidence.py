"""P3 time-evidence contract tests (SYNTHETIC, no real-label mutation)."""

import numpy as np

from backend.goal_detection.accumulation import Accumulator
from backend.goal_detection.buffer import StampedCloud
from backend.goal_detection.config import load_configuration
from backend.goal_detection.detector import GoalCandidate
from backend.goal_detection.pipeline import (
    GoalDemoPipeline,
    _apply_ground_level,
    _estimate_ground_level,
    _fresh_accumulated_candidates,
    _set_candidate_freshness,
)
from backend.goal_detection.tracker import GoalState, GoalTracker
from backend.goal_detection.synthetic import generate_sequence


CFG = load_configuration()


def candidate(signature=None, role="MEASURED_CURRENT", confidence=0.95):
    item = GoalCandidate(
        center=[100.0, 0.0, 1.2], size=[0.6, 5.4, 2.4], yaw=1.57,
        confidence=confidence, crossbar=1.0, pole_count=2,
    )
    if signature is not None:
        item.freshness = {
            "evidence_role": role,
            "observation_signature": signature,
            "newest_source_frame": 0,
            "newest_source_device_time": 0.0,
            "source_age_frames": 0,
            "source_age_seconds": 0.0,
            "unique_observation_frames": 1,
            "time_quality": "device_time",
        }
    return item


def tracker():
    return GoalTracker(CFG["tracking"], CFG["locking"], CFG["goal_detection"]["scoring"])


def test_same_observation_signature_only_counts_once_for_locking():
    t = tracker()
    for index in range(10):
        state = t.update([candidate("obs:same")], index, index * 0.1)
    assert state.state != GoalState.LOCKED
    assert state.goal_id == 0


def test_three_distinct_observation_signatures_complete_locking():
    t = tracker()
    for index in range(3):
        state = t.update([candidate(f"obs:{index}")], index, index * 0.1)
    assert state.state == GoalState.LOCKED
    assert state.goal_id == 1


def test_predicted_recent_never_becomes_measurement():
    t = tracker()
    for index in range(3):
        t.update([candidate(f"obs:{index}")], index, index * 0.1)
    state = t.update([candidate("obs:recent", "PREDICTED_RECENT")], 3, 0.3)
    snapshot = state.as_dict()
    assert snapshot["state"] == "TRACKING"
    assert snapshot["has_measurement"] is False
    assert snapshot["detected"] is False
    assert snapshot["predicted"] is True


def test_expired_history_is_reported_and_rejected_from_measurement():
    item = candidate()
    evidence_cfg = CFG["tracking"]["evidence"]
    _set_candidate_freshness(item, [(0, 7, 0.0)], 10, 1.0,
                             "device_time", evidence_cfg)
    assert item.freshness["evidence_role"] == "EXPIRED_HISTORY"
    fresh, recent, trace = _fresh_accumulated_candidates([item], 10, 1)
    assert fresh == []
    assert recent == []
    assert trace["expired_source_evidence"][0]["candidate_id"] == ""
    assert item.freshness["freshness_reject_reason"] == "expired_source_evidence"


def test_age_one_and_two_history_is_prediction_only():
    evidence_cfg = CFG["tracking"]["evidence"]
    candidates = []
    for age in (1, 2):
        item = candidate()
        _set_candidate_freshness(item, [(10 - age, age, (10 - age) * 0.1)],
                                 10, 1.0, "device_time", evidence_cfg)
        candidates.append(item)
    fresh, recent, trace = _fresh_accumulated_candidates(candidates, 10, 1)
    assert fresh == []
    assert len(recent) == 2
    assert all(item.freshness["evidence_role"] == "PREDICTED_RECENT"
               for item in recent)
    assert trace["predicted_entities"] == 2


def test_device_time_missing_backwards_and_jump_have_explicit_fallback_quality():
    pipeline = GoalDemoPipeline(CFG)
    assert pipeline._resolve_evidence_time_quality(10.0) == "device_time"
    assert pipeline._resolve_evidence_time_quality(None) == "frame_fallback_missing_device_time"
    assert pipeline._resolve_evidence_time_quality(9.0) == "frame_fallback_non_monotonic_device_time"
    assert pipeline._resolve_evidence_time_quality(10.0) == "frame_fallback_time_jump"
    tracker_instance = tracker()
    assert tracker_instance.update([candidate("obs:missing")], 0, None).time_quality \
        == "frame_fallback_missing_device_time"


def test_reacquire_requires_two_new_observation_signatures():
    t = tracker()
    for index in range(3):
        t.update([candidate(f"obs:{index}")], index, index * 0.1)
    for index in range(3, 9):
        state = t.update([], index, index * 0.1)
    assert state.state == GoalState.REACQUIRE
    pending = t.update([candidate("obs:reenter-1")], 9, 0.9).as_dict()
    assert pending["state"] == "REACQUIRE"
    assert pending["has_measurement"] is False
    recovered = t.update([candidate("obs:reenter-2")], 10, 1.0).as_dict()
    assert recovered["state"] == "TRACKING"
    assert recovered["has_measurement"] is True


def test_same_target_reentry_increments_segment_but_far_target_gets_new_id():
    t = tracker()
    for index in range(3):
        t.update([candidate(f"obs:{index}")], index, index * 0.1)
    original_segment = t.track.track_segment_id
    for index in range(3, 9):
        t.update([], index, index * 0.1)
    t.update([candidate("obs:near-1")], 9, 0.9)
    same = t.update([candidate("obs:near-2")], 10, 1.0).as_dict()
    assert same["id"] == 1
    assert same["track_segment_id"] == original_segment + 1

    # Let the strict reacquire window expire, then present a geometrically
    # distant target.  It must not inherit the old ID or Kalman history.
    for index in range(11, 50):
        state = t.update([], index, index * 0.1)
    assert state.state == GoalState.LOST
    far1 = candidate("obs:far-1")
    far1.center = [150.0, 20.0, 1.2]
    far2 = candidate("obs:far-2")
    far2.center = [150.0, 20.0, 1.2]
    t.update([far1], 50, 5.0)
    far_state = t.update([far2], 51, 5.1).as_dict()
    assert far_state["state"] == "TRACKING"
    assert far_state["id"] == 2
    assert far_state["track_segment_id"] == 1


def test_provenance_keeps_frame_point_and_device_time_alignment():
    cfg = dict(CFG["accumulation"])
    cfg["window_size"] = 2
    cfg["voxel_size"] = 0.5
    cfg["roi"] = None
    cfg["range"] = {"min": 0.0, "max": 200.0}
    accumulator = Accumulator(cfg)
    points = np.array([[100.1, 0.1, 1.0, 2.0], [100.2, 0.1, 1.0, 3.0]], dtype=np.float32)
    accumulator.push(StampedCloud(points, 4, 12.5))
    refs = accumulator.provenance.sources_for(0)
    times = accumulator.provenance.device_times_for(0)
    assert refs.shape[1] == 2
    assert set(refs[:, 0]) == {4}
    assert np.allclose(times, 12.5)


def test_pipeline_candidate_contains_flattened_time_evidence_fields():
    cfg = load_configuration(overrides={"strategy": {"mode": "poles"},
                                        "accumulation": {"window_size": 1}})
    pipeline = GoalDemoPipeline(cfg)
    # Empty input still exercises the serialisation contract for every frame.
    result = pipeline.process_frame(np.empty((0, 4), dtype=np.float32), 0, 1.0)
    assert result["goal"]["state"] == "SEARCHING"
    assert result["execution_trace"]["accumulated"][-1]["stage"] == "association"


def test_pitch_roll_ground_leveling_is_plane_normalization_not_threshold_widening():
    item = generate_sequence("pitch_roll", 0, frames=200)[117]
    leveling_cfg = CFG["goal_detection"]["z_leveling"]
    level = _estimate_ground_level(item.points, item.points, CFG, leveling_cfg)
    assert level["enabled"] is True
    assert level["slope"] > leveling_cfg["min_abs_slope"]
    leveled = _apply_ground_level(item.points, level)
    # Ground normalization removes the frame tilt while leaving point count
    # and x/y provenance unchanged.
    assert leveled.shape == item.points.shape
    assert np.allclose(leveled[:, :2], item.points[:, :2])
    assert abs(float(np.median(leveled[:, 2]))) < 0.5


def _ground_fallback_scene():
    """SYNTHETIC known sloped plane in a sparse-scan target ROI."""
    cfg = load_configuration()
    cfg["accumulation"]["roi"] = [84.0, 104.0, -36.0, -17.0, -2.0, 6.0]
    x, y = np.meshgrid(np.linspace(85, 103, 32), np.linspace(-35, -18, 32))
    z = 0.01 * x - 0.01 * y - 2.5
    plane = np.column_stack((x.ravel(), y.ravel(), z.ravel(),
                             np.full(x.size, 100.0)))
    return cfg, plane


def test_ground_source_falls_back_after_roi_and_ground_band_filtering():
    cfg, plane = _ground_fallback_scene()
    # Plenty of raw points must not disable fallback when only a few survive
    # spatial filtering, or when the lower quantile has too few observations.
    outside = np.tile([5.0, 0.0, 0.0, 100.0], (2000, 1))
    for in_roi, reason in ((19, "insufficient_ground_points"),
                           (200, "insufficient_ground_band")):
        current = np.vstack((outside, plane[:in_roi]))
        original = current.copy()
        level = _estimate_ground_level(current, plane, cfg,
                                       cfg["goal_detection"]["z_leveling"],
                                       source_context={"motion_status": "confirmed_static",
                                                       "source_age_seconds": 1.9})
        assert level["enabled"]
        assert level["source"] == "accumulated"
        assert level["source_attempts"][0]["reason"] == reason
        assert np.allclose([level["slope_x"], level["slope_y"], level["intercept"]],
                           [0.01, -0.01, -2.5], atol=1e-6)
        assert np.allclose(_apply_ground_level(plane, level)[:, 2], 0, atol=1e-6)
        assert np.array_equal(current, original)


def test_ground_source_prefers_current_and_does_not_force_sparse_plane():
    cfg, plane = _ground_fallback_scene()
    older = plane.copy()
    older[:, 2] += 0.5
    level_cfg = cfg["goal_detection"]["z_leveling"]
    current = _estimate_ground_level(plane, older, cfg, level_cfg)
    assert current["source"] == "single"
    assert abs(current["intercept"] + 2.5) < 1e-6
    sparse = _estimate_ground_level(plane[:19], older[:19], cfg, level_cfg,
        source_context={"motion_status": "confirmed_static", "source_age_seconds": 1.9})
    assert not sparse["enabled"]
    assert sparse["reason"] == "insufficient_ground_points"
    assert len(sparse["source_attempts"]) == 2

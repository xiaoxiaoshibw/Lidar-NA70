"""Goal state machine: lock, track, lose, reacquire (SYNTHETIC)."""
import numpy as np
import pytest

from backend.goal_detection.config import load_configuration
from backend.goal_detection.detector import GoalCandidate
from backend.goal_detection.tracker import GoalState, GoalTracker

CFG = load_configuration()


def tracker():
    return GoalTracker(CFG["tracking"], CFG["locking"], CFG["goal_detection"]["scoring"])


def cand(cx=100.0, cy=0.0, cz=1.5, confidence=0.9, width=7.3):
    return GoalCandidate(center=[cx, cy, cz], size=[0.8, width, 2.4], yaw=1.57,
                         confidence=confidence, crossbar=1.0, pole_count=2)


def test_searching_until_candidate():
    t = tracker()
    assert t.update([], 0) is None
    state = t.update([cand()], 1)
    assert state.state == GoalState.CANDIDATE
    assert state.goal_id == 0  # 未锁定不发 ID


def test_lock_requires_min_consistent_frames():
    t = tracker()
    t.update([cand()], 0)
    t.update([cand()], 1)
    state = t.update([cand()], 2)
    assert state.state == GoalState.LOCKED
    assert state.goal_id == 1
    state = t.update([cand()], 3)
    assert state.state == GoalState.TRACKING
    assert state.age == 1


def test_low_confidence_never_locks():
    t = tracker()
    for i in range(6):
        state = t.update([cand(confidence=0.55)], i)
    assert state.state in (GoalState.CANDIDATE, GoalState.LOCKING)
    assert state.goal_id == 0


def test_candidate_tolerates_short_gap_resets_on_long_gap():
    t = tracker()
    t.update([cand()], 0)
    t.update([], 1)  # 缺席 1 帧 ≤ candidate_gap：保持确认进度
    state = t.update([cand()], 2)
    assert state.state == GoalState.CANDIDATE
    assert state.goal_id == 0
    # 第 3 次确认即锁定
    state = t.update([cand()], 3)
    assert state.state == GoalState.LOCKED
    # 超过 candidate_gap 的连续缺席才重置回搜索、从头计数
    t2 = tracker()
    t2.update([cand()], 0)
    for i in range(1, 6):
        t2.update([], i)
    state = t2.update([cand()], 6)
    assert state.state == GoalState.CANDIDATE
    assert state.goal_id == 0


def test_strong_candidate_preempts_weak_cycle():
    t = tracker()
    t.update([cand(cx=60.0, cy=0.0, confidence=0.6)], 0)
    t.update([cand(cx=60.0, cy=0.0, confidence=0.6)], 1)
    # 40m 外出现达到锁定置信度的候选：预锁定阶段立即跟随，不被弱周期粘住
    state = t.update([cand(cx=100.0, cy=0.0, confidence=0.9)], 2)
    assert abs(state.center[0] - 100.0) < 1.0
    t.update([cand(cx=100.0, cy=0.0, confidence=0.9)], 3)
    state = t.update([cand(cx=100.0, cy=0.0, confidence=0.9)], 4)
    assert state.state == GoalState.LOCKED
    assert abs(state.center[0] - 100.0) < 1.0


def test_tracking_id_stable_and_smoothed():
    t = tracker()
    for i in range(3):
        state = t.update([cand()], i)
    assert state.state == GoalState.LOCKED
    # 中心小幅漂移：ID 不变，中心被平滑
    for i, dy in enumerate([0.1, 0.2, 0.3, 0.4], start=3):
        state = t.update([cand(cy=dy)], i)
        assert state.goal_id == 1
    assert state.state == GoalState.TRACKING
    assert abs(state.center[1] - 0.4) < 0.4


def test_lost_after_gap_and_reacquire_keeps_id():
    t = tracker()
    for i in range(3):
        t.update([cand()], i)
    state = None
    for i in range(3, 10):  # 丢 7 帧 > lost_frame_threshold=5
        state = t.update([], i)
    assert state.state in (GoalState.REACQUIRE, GoalState.LOST)
    assert state.goal_id == 1
    # 原位置附近重现 → 恢复 TRACKING，ID 不变
    state = t.update([cand(cy=0.5)], 10)
    assert state.state == GoalState.TRACKING
    assert state.goal_id == 1


def test_lost_beyond_jump_starts_fresh_track():
    cfg = CFG
    t = GoalTracker(cfg["tracking"], cfg["locking"], cfg["goal_detection"]["scoring"])
    for i in range(3):
        t.update([cand()], i)
    for i in range(3, 40):  # 长时间丢失，超过 reacquire_timeout
        state = t.update([], i)
    assert state.state == GoalState.LOST
    # 远处的候选是另一个目标：与原位置差 50m，不应复用 ID
    far = [cand(cx=150.0, cy=20.0, confidence=0.95)]
    state = t.update(far, 40)
    assert state.state == GoalState.CANDIDATE
    assert state.goal_id == 1  # 尚未锁定，ID 仍是旧值


def test_no_new_track_per_frame():
    t = tracker()
    for i in range(20):
        state = t.update([cand(confidence=0.6)], i)  # 永远到不了 lock_confidence
    assert state.goal_id == 0  # 20 帧也只有 0 个 ID
    state = t.update([cand(confidence=0.9)], 20)
    assert state.state in (GoalState.LOCKED, GoalState.LOCKING)
    if state.state == GoalState.LOCKED:
        assert state.goal_id == 1


def test_reacquire_window_times_out():
    t = tracker()
    for i in range(3):
        t.update([cand()], i)
    for i in range(3, 3 + 40):
        state = t.update([], i)
    assert state.state == GoalState.LOST


def test_history_bounded():
    t = tracker()
    for i in range(500):
        t.update([cand(cy=0.001 * i)], i)
    assert len(t.track.history) <= 300


def test_missing_measurement_prediction_and_lost_history_are_not_detections():
    t = tracker()
    for i in range(3):
        state = t.update([cand()], i, i * 0.1)
    measured = state.as_dict()
    assert measured["detected"] is True
    assert measured["has_measurement"] is True
    assert measured["predicted"] is False
    last_measurement = measured["last_measurement"]

    predicted = t.update([], 3, 0.3).as_dict()
    assert predicted["detected"] is False
    assert predicted["has_measurement"] is False
    assert predicted["predicted"] is True
    assert predicted["last_measurement"] == last_measurement

    for i in range(4, 45):
        lost = t.update([], i, i * 0.1).as_dict()
    assert lost["state"] == "LOST"
    assert lost["detected"] is False
    assert lost["predicted"] is False
    assert lost["lost"] is True
    assert lost["display_role"] == "lost_history"
    assert lost["last_measurement"] == last_measurement


def test_reacquire_requires_multiple_consistent_measurements():
    t = tracker()
    for i in range(3):
        t.update([cand()], i, i * 0.1)
    for i in range(3, 9):
        state = t.update([], i, i * 0.1)
    assert state.state == GoalState.REACQUIRE
    pending = t.update([cand(cy=0.3)], 9, 0.9).as_dict()
    assert pending["state"] == "REACQUIRE"
    assert pending["detected"] is False
    assert pending["has_measurement"] is False
    recovered = t.update([cand(cy=0.35)], 10, 1.0).as_dict()
    assert recovered["state"] == "TRACKING"
    assert recovered["detected"] is True
    assert recovered["has_measurement"] is True


def test_reacquire_rejects_distant_high_confidence_structure():
    t = tracker()
    for i in range(3):
        t.update([cand(cx=94.0, cy=-26.0)], i, i * 0.1)
    for i in range(3, 9):
        state = t.update([], i, i * 0.1)
    assert state.state == GoalState.REACQUIRE
    far = cand(cx=93.0, cy=29.0, confidence=0.99)
    far.candidate_id = "opposite-structure"
    state = t.update([far], 9, 0.9)
    assert state.state == GoalState.REACQUIRE
    assert state.goal_id == 1
    assert state.has_measurement is False
    assert np.linalg.norm(state.center[:2] - [94.0, -26.0]) < 1.0
    assert t.last_association["reject_reason"] == "center_jump_exceeds_gate"
    assert t.last_association["edges"][0]["gate_override"] is False
    state = t.update([cand(cx=94.2, cy=-25.8)], 10, 1.0)
    assert state.state == GoalState.TRACKING
    assert state.goal_id == 1
    assert np.linalg.norm(state.center[:2] - [94.2, -25.8]) < 1.0


def test_device_time_and_association_edge_are_the_actual_selection():
    t = tracker()
    first = cand(cx=100.0, cy=0.0)
    first.candidate_id = "first"
    t.update([first], 0, 10.0)
    near = cand(cx=100.1, cy=0.1, confidence=0.85)
    near.candidate_id = "near"
    far = cand(cx=104.0, cy=3.0, confidence=0.99)
    far.candidate_id = "far"
    state = t.update([far, near], 1, 10.12)
    assert state.time_quality == "device_time"
    assert state.dt == pytest.approx(0.12)
    association = t.last_association
    assert association["candidate_id"] == "near"
    selected_edge = next(edge for edge in association["edges"] if edge["accepted"])
    assert selected_edge["candidate_id"] == association["candidate_id"]
    assert selected_edge["association_score"] == association["association_score"]
    fallback = t.update([near], 2, 9.0)
    assert fallback.time_quality == "device_time_non_monotonic_fallback"
    assert fallback.dt == pytest.approx(0.1)

"""Goal state machine and tracker (LiDAR-only).

States: SEARCHING -> CANDIDATE -> LOCKING -> LOCKED -> TRACKING -> (LOST ->
REACQUIRE).  A ``goal_id`` is only assigned at LOCKED and is kept through
LOST/REACQUIRE; candidates never spawn a new track every frame.  Detection is
the proposal source, the tracker owns temporal consistency.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from .geometry import axis_aligned_bounds, oriented_box_corners


class GoalState(str, Enum):
    SEARCHING = "SEARCHING"
    CANDIDATE = "CANDIDATE"
    LOCKING = "LOCKING"
    LOCKED = "LOCKED"
    TRACKING = "TRACKING"
    LOST = "LOST"
    REACQUIRE = "REACQUIRE"


@dataclass
class TrackState:
    """Public per-track snapshot for the demo API."""

    goal_id: int
    state: GoalState
    center: np.ndarray
    size: list[float]
    yaw: float
    confidence: float
    age: int
    last_seen_index: int
    history: list[list] = field(default_factory=list)
    has_measurement: bool = False
    time_quality: str = "frame_fallback"
    dt: float = 0.1
    track_segment_id: int = 0
    last_measurement: dict | None = None
    evidence_role: str = "NONE"

    def as_dict(self) -> dict:
        center = [round(float(v), 2) for v in self.center]
        size = [round(float(v), 2) for v in self.size]
        corners = oriented_box_corners(center, size, self.yaw) if len(size) == 3 else []
        minimum, maximum = axis_aligned_bounds(corners) if corners else ([], [])
        predicted = (not self.has_measurement
                     and self.state in (GoalState.TRACKING, GoalState.REACQUIRE))
        lost = self.state == GoalState.LOST
        confirmed = self.goal_id > 0 and self.state in (
            GoalState.LOCKED, GoalState.TRACKING, GoalState.REACQUIRE, GoalState.LOST)
        return {
            "id": self.goal_id, "state": self.state.value,
            "detected": bool(self.has_measurement and self.state in (
                GoalState.LOCKED, GoalState.TRACKING)),
            "has_measurement": bool(self.has_measurement),
            "predicted": bool(predicted),
            "lost": bool(lost),
            "confirmed": bool(confirmed),
            "distance": round(float(np.linalg.norm(self.center)), 2),
            "confidence": round(float(self.confidence), 3),
            "center": center,
            "size": size,
            "yaw": round(float(self.yaw), 3),
            "corners": [[round(float(v), 3) for v in point] for point in corners],
            "bbox_min": [round(float(v), 3) for v in minimum],
            "bbox_max": [round(float(v), 3) for v in maximum],
            "box_role": "track_estimate",
            "display_role": ("measurement_estimate" if self.has_measurement else
                             "prediction" if predicted else
                             "lost_history" if lost else "candidate_estimate"),
            "measurement_frame_index": self.last_seen_index,
            "last_measurement": self.last_measurement,
            "time_quality": self.time_quality,
            "dt": round(float(self.dt), 6),
            "track_segment_id": self.track_segment_id,
            "evidence_role": self.evidence_role,
            "age": self.age,
            "last_seen_index": self.last_seen_index,
        }


@dataclass
class _Track:
    goal_id: int
    state: GoalState = GoalState.SEARCHING
    center: np.ndarray | None = None
    size: list[float] = field(default_factory=list)
    yaw: float = 0.0
    confidence: float = 0.0
    candidate_frames: int = 0
    candidate_missed: int = 0
    missed_frames: int = 0
    age: int = 0
    last_seen_index: int = -1
    history: list = field(default_factory=list)
    # Constant-velocity Kalman per axis: [position, velocity]; P is 2x2.
    kalman: list = field(default_factory=list)
    track_segment_id: int = 0
    last_measurement_center: list[float] | None = None
    last_measurement_size: list[float] = field(default_factory=list)
    last_measurement_yaw: float = 0.0
    pending_center: np.ndarray | None = None
    reuse_goal_id: bool = False
    observation_signatures: set[str] = field(default_factory=set)
    reacquire_signatures: set[str] = field(default_factory=set)
    last_measurement_evidence: dict = field(default_factory=dict)
    reacquire_target_mode: str | None = None  # same | new (strict evidence)


class GoalTracker:
    """One tracked goal.  Feed per-frame candidates; the state machine runs."""

    def __init__(self, tracking_cfg: dict, locking_cfg: dict, scoring_cfg: dict):
        self.min_confidence = float(scoring_cfg["min_confidence"])
        self.lock_confidence = float(scoring_cfg["lock_confidence"])
        self.min_frames = int(locking_cfg["min_frames"])
        self.candidate_gap = int(locking_cfg.get("candidate_gap", 0))
        self.lost_threshold = int(tracking_cfg["lost_frame_threshold"])
        self.reacquire = bool(tracking_cfg["reacquire"])
        self.max_jump = float(tracking_cfg["max_center_jump"])
        self.use_kalman = bool(tracking_cfg["kalman"])
        self.reacquire_timeout = int(tracking_cfg.get("reacquire_timeout", 30))
        self.reacquire_min_frames = int(tracking_cfg.get("reacquire_min_frames", 2))
        self.nominal_dt = float(tracking_cfg.get("nominal_dt", 0.1))
        self.min_dt = float(tracking_cfg.get("min_dt", 0.01))
        self.max_dt = float(tracking_cfg.get("max_dt", 0.5))
        self.association_cfg = dict(tracking_cfg.get("association", {}))
        evidence_cfg = dict(tracking_cfg.get("evidence", {}))
        self.signature_history_size = int(evidence_cfg.get("signature_history_size", 256))
        self._track = _Track(goal_id=0)
        self._next_id = 1
        self._last_update_frame: int | None = None
        self._last_device_time: float | None = None
        self._frame_has_measurement = False
        self._current_dt = self.nominal_dt
        self._time_quality = "frame_fallback"
        self._frame_evidence_role = "NONE"
        self.last_association: dict = {
            "accepted": False, "association_score": 0.0,
            "reject_reason": "no_candidate", "candidate_id": None,
        }

    @property
    def track(self) -> TrackState | None:
        t = self._track
        if t.state == GoalState.SEARCHING and t.center is None:
            return None
        return TrackState(goal_id=t.goal_id, state=t.state,
                          center=np.array(t.center if t.center is not None else [0, 0, 0]),
                          size=list(t.size), yaw=t.yaw, confidence=t.confidence,
                          age=t.age, last_seen_index=t.last_seen_index,
                          history=list(t.history),
                          has_measurement=self._frame_has_measurement,
                           time_quality=self._time_quality, dt=self._current_dt,
                           track_segment_id=t.track_segment_id,
                           last_measurement=self._last_measurement_dict(t),
                           evidence_role=self._frame_evidence_role)

    def update(self, candidates, frame_index: int,
               device_time: float | None = None) -> TrackState | None:
        """Advance the state machine with this frame's candidates (best first)."""
        self._current_dt, self._time_quality = self._resolve_dt(frame_index, device_time)
        self._frame_has_measurement = False
        self._frame_evidence_role = "NONE"
        t = self._track
        candidate = (self._match(candidates, t.center, gate=self._gate_for(t.state))
                     if t.state == GoalState.SEARCHING else None)
        if t.state == GoalState.SEARCHING:
            if candidate is not None:
                role = self._candidate_role(candidate)
                self._frame_evidence_role = role
                t.state = GoalState.CANDIDATE
                t.center = np.array(candidate.center, dtype=np.float64)
                t.size = list(candidate.size)
                t.yaw = candidate.yaw
                t.confidence = candidate.confidence
                t.candidate_frames = 1 if role == "MEASURED_CURRENT" else 0
                t.candidate_missed = 0
                t.last_seen_index = frame_index
                if role == "MEASURED_CURRENT":
                    self._remember_observation(t, candidate, reacquire=False,
                                               frame_index=frame_index)
                    self._record_measurement(t, candidate, frame_index)
            return self.track
        if t.state == GoalState.CANDIDATE:
            candidate = self._match(candidates, t.center, self._gate_for(t.state),
                                    strong_floor=self.lock_confidence)
            if candidate is not None:
                role = self._candidate_role(candidate)
                self._frame_evidence_role = role
                if role != "MEASURED_CURRENT":
                    t.candidate_missed += 1
                    if t.candidate_missed > self.candidate_gap:
                        t.state = (GoalState.LOST if t.goal_id > 0
                                   else GoalState.SEARCHING)
                        t.candidate_frames = 0
                        if t.goal_id == 0:
                            t.center = None
                    return self.track
                new_observation = self._remember_observation(
                    t, candidate, reacquire=False, frame_index=frame_index)
                if self._jump(candidate, t) > self.max_jump:
                    # 强候选跳转：开启新一轮确认周期，不把中心拉扯成混合值。
                    self._restart_at(t, candidate, frame_index)
                else:
                    if new_observation:
                        t.candidate_frames += 1
                    t.candidate_missed = 0
                    self._absorb(candidate, frame_index)
                    if new_observation and t.candidate_frames >= self.min_frames \
                            and t.confidence >= self.lock_confidence:
                        t.state = GoalState.LOCKED
                        if not (t.reuse_goal_id and t.goal_id > 0):
                            t.goal_id = self._next_id
                            self._next_id += 1
                        t.reuse_goal_id = False
                        if t.track_segment_id == 0:
                            t.track_segment_id = 1
                        t.age = 0
                    elif new_observation and t.candidate_frames >= self.min_frames:
                        t.state = GoalState.LOCKING
            else:
                t.candidate_missed += 1
                if t.candidate_missed > self.candidate_gap:
                    t.state = (GoalState.LOST if t.goal_id > 0
                               else GoalState.SEARCHING)
                    t.candidate_frames = 0
                    if t.goal_id == 0:
                        t.center = None
                    t.reuse_goal_id = False
            return self.track
        if t.state == GoalState.LOCKING:
            candidate = self._match(candidates, t.center, self._gate_for(t.state),
                                    strong_floor=self.lock_confidence)
            if candidate is not None:
                role = self._candidate_role(candidate)
                self._frame_evidence_role = role
                if role != "MEASURED_CURRENT":
                    t.candidate_missed += 1
                    if t.candidate_missed > self.candidate_gap:
                        t.state = GoalState.CANDIDATE
                    return self.track
                new_observation = self._remember_observation(
                    t, candidate, reacquire=False, frame_index=frame_index)
                if self._jump(candidate, t) > self.max_jump:
                    t.state = GoalState.CANDIDATE
                    self._restart_at(t, candidate, frame_index)
                else:
                    self._absorb(candidate, frame_index)
                    t.candidate_missed = 0
                    if new_observation and t.confidence >= self.lock_confidence:
                        t.state = GoalState.LOCKED
                        if not (t.reuse_goal_id and t.goal_id > 0):
                            t.goal_id = self._next_id
                            self._next_id += 1
                        t.reuse_goal_id = False
                        if t.track_segment_id == 0:
                            t.track_segment_id = 1
                        t.age = 0
            else:
                t.candidate_missed += 1
                if t.candidate_missed > self.candidate_gap:
                    t.state = GoalState.CANDIDATE
                    t.candidate_frames = self.min_frames
            return self.track
        # LOCKED / TRACKING / LOST / REACQUIRE
        if t.state == GoalState.REACQUIRE:
            # An existing ID may only be reacquired near its last track centre.
            # The pre-lock strong-candidate override would otherwise accept a
            # high-confidence facade tens of metres away and blend both places
            # into one Kalman track.  A new distant structure must wait for a
            # fresh track cycle after this ID is lost.
            reference = (t.center if t.reacquire_target_mode == "same" else
                         t.pending_center if t.pending_center is not None else t.center)
            near = self._match(candidates, reference, self.max_jump * 2.0)
            if near is not None:
                role = self._candidate_role(near)
                self._frame_evidence_role = role
                if role != "MEASURED_CURRENT":
                    # Recent history can keep the search centre alive, but it
                    # is never a measurement or confirmation observation.
                    t.pending_center = np.asarray(near.center, dtype=np.float64)
                    return self.track
                new_observation = self._remember_observation(
                    t, near, reacquire=True, frame_index=frame_index)
                t.pending_center = np.asarray(near.center, dtype=np.float64)
                if new_observation:
                    t.candidate_frames += 1
                if new_observation and t.candidate_frames >= self.reacquire_min_frames:
                    mode = t.reacquire_target_mode
                    if mode == "new":
                        t.goal_id = self._next_id
                        self._next_id += 1
                        t.track_segment_id = 1
                        t.history = []
                        t.last_measurement_center = None
                        t.last_measurement_size = []
                        t.last_measurement_evidence = {}
                    elif mode == "same":
                        t.track_segment_id = max(1, t.track_segment_id + 1)
                    t.state = GoalState.TRACKING
                    self._absorb(near, frame_index)
                    t.missed_frames = 0
                    t.candidate_frames = 0
                    t.candidate_missed = 0
                    t.reacquire_signatures.clear()
                    t.reacquire_target_mode = None
                    t.pending_center = None
                    t.age += 1
                return self.track
            t.pending_center = None
            if t.reacquire_signatures:
                t.candidate_missed += 1
                expired = t.candidate_missed > self.reacquire_timeout
            else:
                t.candidate_frames += 1
                expired = t.candidate_frames > self.reacquire_timeout
            if expired:
                t.state = GoalState.LOST
            return self.track
        if t.state == GoalState.LOST:
            # LOST only publishes historical state.  Any new observation must
            # pass a fresh multi-frame candidate cycle before it becomes a
            # current detection; a stale LOST box never turns into a one-frame
            # measurement merely because something appeared near it.
            near = self._match(candidates, t.center, self.max_jump * 2.0)
            fresh = near if near is not None else (candidates[0] if candidates else None)
            if fresh is not None and fresh.confidence >= self.min_confidence:
                role = self._candidate_role(fresh)
                self._frame_evidence_role = role
                if role == "MEASURED_CURRENT" and self._has_explicit_evidence(fresh):
                    # A strict evidence run always re-enters through REACQUIRE;
                    # the first new source observation is only a pending anchor.
                    t.state = GoalState.REACQUIRE
                    t.pending_center = np.asarray(fresh.center, dtype=np.float64)
                    t.center = np.asarray(fresh.center, dtype=np.float64)
                    t.candidate_frames = 1
                    t.reacquire_signatures = {self._observation_signature(fresh, frame_index)}
                    t.reuse_goal_id = near is not None and t.goal_id > 0
                    t.reacquire_target_mode = "same" if t.reuse_goal_id else "new"
                    t.missed_frames = 0
                    if t.reacquire_target_mode == "new":
                        t.kalman = []
                        t.history = []
                        t.last_measurement_center = None
                        t.last_measurement_size = []
                        t.last_measurement_evidence = {}
                elif role == "MEASURED_CURRENT":
                    # Legacy unit callers without provenance keep the old API
                    # behaviour; production candidates always take the strict path.
                    t.state = GoalState.CANDIDATE
                    self._restart_at(t, fresh, frame_index, new_segment=True)
                    t.reuse_goal_id = near is not None and t.goal_id > 0
            return self.track
        # LOCKED / TRACKING：门限内吸收，缺席计数
        candidate = self._match(candidates, t.center, gate=self._gate_for(t.state))
        if candidate is not None and self._candidate_role(candidate) == "MEASURED_CURRENT":
            self._frame_evidence_role = "MEASURED_CURRENT"
            if t.state == GoalState.LOCKED:
                t.state = GoalState.TRACKING
            self._absorb(candidate, frame_index)
            t.missed_frames = 0
            t.age += 1
        else:
            if t.state in (GoalState.LOCKED, GoalState.TRACKING):
                t.missed_frames += 1
                if t.missed_frames > self.lost_threshold:
                    t.state = GoalState.REACQUIRE if self.reacquire else GoalState.LOST
                    t.candidate_frames = 0
                    t.candidate_missed = 0
                    t.reacquire_signatures.clear()
                    t.reacquire_target_mode = "same" if self.reacquire else None
                    t.pending_center = None
                else:
                    t.state = GoalState.TRACKING
            elif t.state == GoalState.REACQUIRE:
                t.candidate_frames += 1
                if t.candidate_frames > self.reacquire_timeout:
                    t.state = GoalState.LOST
            elif t.state == GoalState.LOST:
                pass
        return self.track

    def _gate_for(self, state: GoalState) -> float:
        if state in (GoalState.LOST, GoalState.REACQUIRE):
            return self.max_jump * 2.0
        return self.max_jump

    def _match(self, candidates, center, gate: float, strong_floor: float | None = None):
        """Pick the candidate to absorb this frame.

        ``strong_floor``（预锁定阶段传锁定置信度）：达到阈值的候选构成强池，
        强池存在时弱候选全部让位，且强候选不受跳变门限约束——预锁定阶段
        出现真正门形的候选时值得立即跟随，不能被弱候选周期粘住。
        """
        if not candidates:
            self.last_association = {
                "accepted": False, "association_score": 0.0,
                "reject_reason": "no_candidate", "candidate_id": None,
                "gate_m": round(float(gate), 3),
                "edges": [], "time_quality": self._time_quality,
                "dt": round(float(self._current_dt), 6),
            }
            return None
        strong = ([c for c in candidates if c.confidence >= strong_floor]
                  if strong_floor is not None else [])
        pool = strong or candidates
        edges = []
        eligible = []
        for candidate in candidates:
            in_pool = candidate in pool
            details = self._association_details(candidate, center, gate,
                                                accepted=False,
                                                reject_reason=None)
            center_ok = center is None or gate <= 0 \
                or details["center_distance_m"] <= gate or bool(strong and in_pool)
            score_ok = details["association_score"] >= float(
                self.association_cfg.get("min_score", 0.0))
            if not in_pool:
                reason = "weaker_than_strong_pool"
            elif not center_ok:
                reason = "center_jump_exceeds_gate"
            elif not score_ok:
                reason = "association_score_below_gate"
            else:
                reason = None
                eligible.append((details["association_score"], candidate, details))
            details["reject_reason"] = reason
            details["gate_override"] = bool(strong and in_pool and center is not None
                                              and details["center_distance_m"] > gate)
            edges.append(details)
        best = None
        if eligible:
            _, best, selected_details = max(eligible, key=lambda item: item[0])
            selected_details["accepted"] = True
            selected_details["reject_reason"] = None
            self.last_association = {**selected_details, "edges": edges}
        else:
            representative = max(edges, key=lambda item: item["association_score"])
            self.last_association = {**representative, "accepted": False,
                                     "edges": edges}
        return best

    def _association_details(self, candidate, center, gate: float, accepted: bool,
                             reject_reason: str | None) -> dict:
        if center is None:
            distance = 0.0
            distance_similarity = 1.0
        else:
            distance = float(np.linalg.norm(
                np.asarray(candidate.center) - np.asarray(center)))
            distance_similarity = max(0.0, 1.0 - distance / max(float(gate), 1e-6))
        old_size = np.asarray(self._track.size, dtype=float)
        new_size = np.asarray(candidate.size, dtype=float)
        if old_size.shape == new_size.shape and old_size.size:
            bbox_similarity = max(0.0, 1.0 - float(np.linalg.norm(old_size - new_size))
                                  / max(float(np.linalg.norm(old_size)), 1e-6))
        else:
            bbox_similarity = 1.0
        old_bearing = (float(np.arctan2(center[1], center[0]))
                       if center is not None else
                       float(np.arctan2(candidate.center[1], candidate.center[0])))
        new_bearing = float(np.arctan2(candidate.center[1], candidate.center[0]))
        bearing_delta = abs(float(np.arctan2(np.sin(new_bearing - old_bearing),
                                             np.cos(new_bearing - old_bearing))))
        bearing_scale = float(self.association_cfg.get("bearing_scale_rad", np.pi / 6.0))
        bearing_similarity = max(0.0, 1.0 - bearing_delta / max(bearing_scale, 1e-6))
        # Goal-line yaw is undirected: theta and theta+pi are equivalent.
        yaw_delta = abs(((float(candidate.yaw) - float(self._track.yaw)
                          + np.pi / 2.0) % np.pi) - np.pi / 2.0)
        geometry_similarity = max(0.0, 1.0 - yaw_delta / (np.pi / 2.0))
        history = min(float(self._track.age) / 10.0, 1.0)
        weights = self.association_cfg.get("weights", {})
        score = (
            float(weights.get("center", 0.35)) * distance_similarity
            + float(weights.get("bearing", 0.10)) * bearing_similarity
            + float(weights.get("size", 0.15)) * bbox_similarity
            + float(weights.get("yaw", 0.10)) * geometry_similarity
            + float(weights.get("detection", 0.25)) * float(candidate.confidence)
            + float(weights.get("history", 0.05)) * history)
        return {
            "accepted": bool(accepted),
            "candidate_id": getattr(candidate, "candidate_id", None),
            "association_score": round(float(score), 3),
            "detection_score": round(float(candidate.confidence), 3),
            "center_distance_m": round(distance, 3),
            "gate_m": round(float(gate), 3),
            "bearing_similarity": round(bearing_similarity, 3),
            "bearing_delta_rad": round(bearing_delta, 5),
            "distance_similarity": round(distance_similarity, 3),
            "bbox_similarity": round(bbox_similarity, 3),
            "geometry_similarity": round(geometry_similarity, 3),
            "yaw_delta_rad": round(yaw_delta, 5),
            "track_history": round(history, 3),
            "reject_reason": reject_reason,
            "time_quality": self._time_quality,
            "dt": round(float(self._current_dt), 6),
        }

    @staticmethod
    def _jump(candidate, t) -> float:
        return float(np.linalg.norm(np.asarray(candidate.center) - np.asarray(t.center)))

    def _restart_at(self, t, candidate, frame_index: int,
                    new_segment: bool = False) -> None:
        """开启新一轮候选周期：中心直接落到新候选上，确认从头计数。"""
        t.candidate_frames = 1
        t.candidate_missed = 0
        t.center = np.array(candidate.center, dtype=np.float64)
        # 旧位置的 Kalman 状态（含速度项）对新位置是毒药：必须一并重置，
        # 否则滤波器把中心拉回旧轨迹，下一帧又被判为跳转、周期无法收敛。
        t.kalman = [[float(v), 0.0, 1.0, 0.0] for v in t.center]
        t.size = list(candidate.size)
        t.yaw = candidate.yaw
        t.confidence = candidate.confidence
        t.last_seen_index = frame_index
        t.missed_frames = 0
        t.pending_center = None
        t.reuse_goal_id = False
        t.observation_signatures.clear()
        t.reacquire_signatures.clear()
        if self._candidate_role(candidate) == "MEASURED_CURRENT":
            t.observation_signatures.add(self._observation_signature(candidate, frame_index))
        if new_segment:
            t.track_segment_id += 1
            t.history = []
        self._record_measurement(t, candidate, frame_index)

    def _absorb(self, candidate, frame_index: int) -> None:
        t = self._track
        measurement = np.asarray(candidate.center, dtype=np.float64)
        alpha = 0.4
        if self.use_kalman and t.center is not None:
            t.center = self._kalman_step(t, measurement)
        elif t.center is None:
            t.center = measurement
            t.kalman = [[float(v), 0.0, 1.0, 0.0] for v in measurement]
        else:
            t.center = alpha * measurement + (1.0 - alpha) * t.center
        t.size = list(candidate.size)
        t.yaw = float(candidate.yaw)
        t.confidence = alpha * candidate.confidence + (1.0 - alpha) * t.confidence
        t.last_seen_index = frame_index
        self._record_measurement(t, candidate, frame_index)
        t.history.append([frame_index, [float(v) for v in t.center]])
        if len(t.history) > 300:
            t.history = t.history[-300:]

    def _kalman_step(self, t: _Track, measurement: np.ndarray) -> np.ndarray:
        """Per-axis constant-velocity filter with a fixed small process noise."""
        dt = self._current_dt
        if len(t.kalman) != len(measurement):
            t.kalman = [[float(v), 0.0, 1.0, 0.0] for v in measurement]
        estimated = []
        for axis, value in enumerate(measurement):
            pos, vel, p_pos, p_vel = t.kalman[axis]
            # Predict
            pos_pred = pos + vel * dt
            p_pos_pred = p_pos + 4.0 * p_vel + 4.0 * 0.01
            p_vel_pred = p_vel + 0.01
            # Update (measurement noise 0.25 m^2)
            innovation = float(value) - pos_pred
            s = p_pos_pred + 0.25
            gain = p_pos_pred / s
            pos_new = pos_pred + gain * innovation
            vel_new = vel + gain * innovation / max(dt, 1e-6) * 0.5
            p_pos_new = (1.0 - gain) * p_pos_pred
            p_vel_new = p_vel_pred * (1.0 - gain)
            t.kalman[axis] = [pos_new, vel_new, p_pos_new, p_vel_new]
            estimated.append(pos_new)
        return np.array(estimated)

    def _resolve_dt(self, frame_index: int,
                    device_time: float | None) -> tuple[float, str]:
        quality = ("device_time" if device_time is not None
                   and np.isfinite(device_time)
                   else "frame_fallback_missing_device_time")
        dt = self.nominal_dt
        if device_time is not None and np.isfinite(device_time):
            value = float(device_time)
            if self._last_device_time is not None and value > self._last_device_time:
                raw = value - self._last_device_time
                if self.min_dt <= raw <= self.max_dt:
                    dt, quality = raw, "device_time"
                else:
                    quality = "device_time_out_of_range_fallback"
            elif self._last_device_time is not None:
                quality = "device_time_non_monotonic_fallback"
            self._last_device_time = value
        if quality != "device_time" and self._last_update_frame is not None:
            frame_delta = frame_index - self._last_update_frame
            if frame_delta > 0:
                dt = min(max(frame_delta * self.nominal_dt, self.min_dt), self.max_dt)
        self._last_update_frame = frame_index
        return float(dt), quality

    def _record_measurement(self, t: _Track, candidate, frame_index: int) -> None:
        self._frame_has_measurement = True
        self._frame_evidence_role = "MEASURED_CURRENT"
        t.last_measurement_center = [float(v) for v in candidate.center]
        t.last_measurement_size = [float(v) for v in candidate.size]
        t.last_measurement_yaw = float(candidate.yaw)
        t.last_seen_index = frame_index
        t.last_measurement_evidence = dict(candidate.freshness or {})

    @staticmethod
    def _last_measurement_dict(t: _Track) -> dict | None:
        if t.last_measurement_center is None:
            return None
        return {
            "center": list(t.last_measurement_center),
            "size": list(t.last_measurement_size),
            "yaw": float(t.last_measurement_yaw),
            "frame_index": int(t.last_seen_index),
            "box_role": "last_measurement",
            "evidence_role": "MEASURED_CURRENT",
            "observation_signature": t.last_measurement_evidence.get(
                "observation_signature"),
            "newest_source_frame": t.last_measurement_evidence.get(
                "newest_source_frame"),
            "newest_source_device_time": t.last_measurement_evidence.get(
                "newest_source_device_time"),
        }

    @staticmethod
    def _candidate_role(candidate) -> str:
        return str((getattr(candidate, "freshness", {}) or {}).get(
            "evidence_role", "MEASURED_CURRENT"))

    @staticmethod
    def _has_explicit_evidence(candidate) -> bool:
        evidence = getattr(candidate, "freshness", {}) or {}
        return bool(evidence.get("observation_signature"))

    @staticmethod
    def _observation_signature(candidate, frame_index: int) -> str:
        evidence = getattr(candidate, "freshness", {}) or {}
        return str(evidence.get("observation_signature") or
                   f"legacy-frame:{int(frame_index)}")

    def _remember_observation(self, t, candidate, reacquire: bool,
                              frame_index: int) -> bool:
        """Return true only for a new raw observation signature."""
        signature = self._observation_signature(candidate, frame_index)
        seen = t.reacquire_signatures if reacquire else t.observation_signatures
        if signature in seen:
            return False
        seen.add(signature)
        if len(seen) > self.signature_history_size:
            # Sets are deliberately bounded; deterministic FIFO is not needed
            # for correctness because old signatures cannot re-enter the same
            # confirmation cycle after a state reset.
            seen.pop()
        return True

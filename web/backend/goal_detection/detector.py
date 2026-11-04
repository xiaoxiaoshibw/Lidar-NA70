"""Football-goal candidate detection on a single or accumulated cloud."""
from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter

import numpy as np

from .geometry import (axis_aligned_bounds, bbox_for_pair, crossbar_coverage,
                       find_poles, find_poles_neighbor, oriented_box_corners,
                       pair_poles)


@dataclass
class GoalCandidate:
    """One geometric goal-frame hypothesis with a 0-1 confidence."""

    center: list[float]
    size: list[float]
    yaw: float
    confidence: float
    crossbar: float
    pole_count: int
    left_pole: dict = field(default_factory=dict)
    right_pole: dict = field(default_factory=dict)
    candidate_id: str = ""
    supporting_point_indices: list[int] = field(default_factory=list)
    score_breakdown: dict = field(default_factory=dict)
    accepted: bool = True
    reject_reason: str | None = None
    freshness: dict = field(default_factory=dict)
    sensor_corners: list | None = None
    height_reference: str = "sensor_z"
    coordinate_frame: str = "lidar_link"

    def as_dict(self) -> dict:
        distance = float(np.linalg.norm(np.asarray(self.center[:2], dtype=np.float64)))
        corners = (np.asarray(self.sensor_corners, dtype=float) if self.sensor_corners is not None
                   else oriented_box_corners(self.center, self.size, self.yaw))
        minimum, maximum = axis_aligned_bounds(corners)
        evidence = self.freshness or {}
        return {
            "candidate_id": self.candidate_id,
            "center": self.center, "size": self.size, "yaw": self.yaw,
            "confidence": round(self.confidence, 3), "crossbar": round(self.crossbar, 3),
            "detection_score": round(self.confidence, 3),
            "score_breakdown": self.score_breakdown,
            "supporting_point_indices": self.supporting_point_indices,
            "point_count": len(self.supporting_point_indices),
            "distance": round(distance, 3),
            "bearing": round(float(np.arctan2(self.center[1], self.center[0])), 5),
            "width": round(float(self.size[1]), 3),
            "height": round(float(self.size[2]), 3),
            "corners": [[round(float(v), 3) for v in point] for point in corners],
            "bbox_min": [round(float(v), 3) for v in minimum],
            "bbox_max": [round(float(v), 3) for v in maximum],
            "box_role": "measurement",
            "coordinate_frame": self.coordinate_frame,
            "height_reference": self.height_reference,
            "left_pole": self.left_pole,
            "right_pole": self.right_pole,
            "accepted": self.accepted,
            "reject_reason": self.reject_reason,
            "freshness": self.freshness,
            # Flatten the time-evidence contract as well as retaining the
            # nested ``freshness`` object for older Inspector clients.
            "evidence_role": evidence.get("evidence_role"),
            "newest_source_frame": evidence.get("newest_source_frame"),
            "newest_source_device_time": evidence.get("newest_source_device_time"),
            "source_age_frames": evidence.get("source_age_frames"),
            "source_age_seconds": evidence.get("source_age_seconds"),
            "unique_observation_frames": evidence.get("unique_observation_frames", 0),
            "observation_signature": evidence.get("observation_signature"),
            "freshness_reject_reason": evidence.get("freshness_reject_reason"),
            "time_quality": evidence.get("time_quality"),
        }

class GoalDetector:
    """Range gate -> ground filter -> column cells -> pole pairing -> scoring."""

    def __init__(self, detection_cfg: dict):
        self.cell_size = float(detection_cfg.get("cell_size", 0.8))
        self.ground_z = (float(detection_cfg["ground_z"][0]), float(detection_cfg["ground_z"][1]))
        self.pole_cfg = detection_cfg["pole"]
        self.grid_offsets = [tuple(float(v) for v in item)
                             for item in self.pole_cfg.get("grid_offsets", [[0.0, 0.0]])]
        self.neighbor_radius = float(self.pole_cfg.get("neighbor_radius", 0.8))
        self.frame_cfg = detection_cfg["frame"]
        self.scoring = detection_cfg["scoring"]
        self.diagnostics = detection_cfg["diagnostics"]
        self.range_cfg = None
        self.last_rejected: list[GoalCandidate] = []
        self.last_suppressed: list[GoalCandidate] = []
        self.last_trace: list[dict] = []
        self.last_masks: dict[str, np.ndarray] = {}
        self.last_rejected_summary = {"total": 0, "returned": 0, "truncated": False}
        self._rejected_total = 0
        self._rejected_truncated = False

    def set_range(self, range_min: float, range_max: float) -> None:
        self.range_cfg = (float(range_min), float(range_max))

    def detect(self, points: np.ndarray) -> list[GoalCandidate]:
        """Return candidates sorted by confidence, best first.  Empty input -> []."""
        self.last_rejected = []
        self.last_suppressed = []
        self.last_trace = []
        self.last_masks = {}
        self.last_rejected_summary = {"total": 0, "returned": 0, "truncated": False}
        self._rejected_total = 0
        self._rejected_truncated = False
        points = np.asarray(points)
        if points.ndim != 2 or points.shape[1] < 3 or len(points) == 0:
            self.last_trace = [_detector_trace("finite", 0, 0, None, "skipped")]
            return []
        xyz = points[:, :3].astype(np.float64, copy=False)
        stage_started = perf_counter()
        keep = np.isfinite(xyz).all(axis=1)
        self.last_masks["finite"] = keep.copy()
        finite_elapsed = (perf_counter() - stage_started) * 1000.0
        range_started = perf_counter()
        if self.range_cfg is not None:
            distance = np.linalg.norm(xyz, axis=1)
            keep &= (distance >= self.range_cfg[0]) & (distance <= self.range_cfg[1])
        self.last_masks["range"] = keep.copy()
        range_elapsed = (perf_counter() - range_started) * 1000.0
        # The ground band is absolute (radar mounting height is fixed per install);
        # points below it are terrain clutter and cannot belong to an upright frame.
        height_started = perf_counter()
        keep &= xyz[:, 2] > self.ground_z[1]
        self.last_masks["z_height"] = keep.copy()
        height_elapsed = (perf_counter() - height_started) * 1000.0
        self.last_trace = [
            _detector_trace("finite", len(points), int(self.last_masks["finite"].sum()),
                            finite_elapsed),
            _detector_trace("range", int(self.last_masks["finite"].sum()),
                            int(self.last_masks["range"].sum()), range_elapsed),
            _detector_trace("z_height", int(self.last_masks["range"].sum()),
                            int(self.last_masks["z_height"].sum()), height_elapsed),
        ]
        source_indices = np.flatnonzero(keep)
        xyz = xyz[keep]
        if len(xyz) < int(self.pole_cfg["min_points"]):
            self.last_trace.extend([
                _detector_trace("pole_extract", len(xyz), 0, None, "skipped",
                                reason="insufficient_points"),
                _candidate_trace("pair_score", 0, 0, None, "skipped"),
                _candidate_trace("nms", 0, 0, None, "skipped"),
            ])
            return []
        pole_started = perf_counter()
        pole_diagnostics = {}
        poles = find_poles(xyz, self.pole_cfg, self.cell_size,
                           diagnostics=pole_diagnostics,
                           grid_offset=self.grid_offsets[0])
        selected_offset = self.grid_offsets[0]
        fallback_attempts = []
        if len(poles) < 2 and len(self.grid_offsets) > 1:
            for offset in self.grid_offsets[1:]:
                candidate_diagnostics = {}
                candidate_poles = find_poles(
                    xyz, self.pole_cfg, self.cell_size,
                    diagnostics=candidate_diagnostics, grid_offset=offset)
                fallback_attempts.append({"offset": list(offset),
                                          "poles": len(candidate_poles),
                                          "reasons": candidate_diagnostics.get("reason_counts", {})})
                if len(candidate_poles) > len(poles):
                    poles = candidate_poles
                    pole_diagnostics = candidate_diagnostics
                    selected_offset = offset
        neighbor_fallback = None
        if len(poles) < 2:
            neighbor_started = perf_counter()
            neighbor_diagnostics = {}
            neighbor_poles = find_poles_neighbor(
                xyz, self.pole_cfg, self.neighbor_radius, neighbor_diagnostics,
                crossbar_z=self.frame_cfg["crossbar_z"],
                base_z_hint=self.ground_z[1])
            neighbor_fallback = {
                "stage": "local_3d_fallback", "status": "completed",
                "entity": "point", "input_points": len(xyz),
                "output_points": int(sum(p.count for p in neighbor_poles)),
                "output_entities": len(neighbor_poles),
                "latency_ms": round((perf_counter() - neighbor_started) * 1000.0, 3),
                "neighbor_radius": self.neighbor_radius,
                "layer_split_z": neighbor_diagnostics.get("layer_split_z"),
                "seed_points": neighbor_diagnostics.get("seed_points", 0),
                "crossbar_layer_points": neighbor_diagnostics.get(
                    "crossbar_layer_points", 0),
                "unassigned_points": neighbor_diagnostics.get("unassigned_points", 0),
                "base_recovered_poles": neighbor_diagnostics.get(
                    "base_recovered_poles", 0),
                "reason_counts": neighbor_diagnostics.get("reason_counts", {}),
            }
            if len(neighbor_poles) > len(poles):
                poles = neighbor_poles
        pole_elapsed = (perf_counter() - pole_started) * 1000.0
        pole_local = np.unique(np.concatenate([
            np.asarray(pole.indices, dtype=np.int64) for pole in poles
            if pole.indices is not None])) if poles else np.empty(0, dtype=np.int64)
        pole_mask = np.zeros(len(points), dtype=bool)
        if len(pole_local):
            pole_mask[source_indices[pole_local]] = True
        self.last_masks["pole_extract"] = pole_mask
        self.last_trace.append(_detector_trace(
            "pole_extract", len(xyz), int(len(pole_local)), pole_elapsed,
            output_entities=len(poles),
            reason_counts=pole_diagnostics.get("reason_counts", {}),
            cell_count=pole_diagnostics.get("cell_count", 0),
            tall_cells=pole_diagnostics.get("tall_cells", 0),
            isolated_cells=pole_diagnostics.get("isolated_cells", 0),
            grid_offset=list(selected_offset), fallback_attempts=fallback_attempts))
        if neighbor_fallback is not None:
            self.last_trace.append(neighbor_fallback)
        if len(poles) < 2:
            self.last_trace.extend([
                _candidate_trace("pair_score", 0, 0, None, "skipped",
                                 reason="fewer_than_two_poles"),
                _candidate_trace("nms", 0, 0, None, "skipped"),
            ])
            return []
        candidates = []
        pair_started = perf_counter()
        accepted_pairs = pair_poles(poles, self.pole_cfg, self.frame_cfg)
        accepted_ids = {(id(left), id(right)) for left, right in accepted_pairs}
        for left, right in accepted_pairs:
            crossbar = crossbar_coverage(xyz, left, right, self.frame_cfg["crossbar_z"])
            geometry = self._geometry_score(left, right)
            confidence = (float(self.scoring["geometry_weight"]) * geometry
                          + float(self.scoring["crossbar_weight"]) * crossbar)
            bbox = bbox_for_pair(left, right)
            support_local = self._support_indices(xyz, left, right)
            support_source = source_indices[support_local]
            candidates.append(GoalCandidate(
                center=bbox["center"], size=bbox["size"], yaw=bbox["yaw"],
                confidence=float(np.clip(confidence, 0.0, 1.0)),
                crossbar=crossbar, pole_count=len(poles),
                left_pole=self._pole_dict(left), right_pole=self._pole_dict(right)))
            candidates[-1].supporting_point_indices = [int(v) for v in support_source]
            candidates[-1].score_breakdown = {
                "geometry_score": round(float(geometry), 3),
                "crossbar_score": round(float(crossbar), 3),
                "geometry_weight": float(self.scoring["geometry_weight"]),
                "crossbar_weight": float(self.scoring["crossbar_weight"]),
            }
        candidates.sort(key=lambda c: c.confidence, reverse=True)
        pair_elapsed = (perf_counter() - pair_started) * 1000.0
        candidate_support = np.zeros(len(points), dtype=bool)
        for candidate in candidates:
            candidate_support[np.asarray(candidate.supporting_point_indices, dtype=np.int64)] = True
        self.last_masks["goal_structure"] = candidate_support
        self.last_trace.append(_candidate_trace(
            "pair_score", len(poles) * (len(poles) - 1) // 2,
            len(candidates), pair_elapsed, rejected_entities=(
                len(poles) * (len(poles) - 1) // 2 - len(candidates))))
        nms_started = perf_counter()
        kept = self._suppress_duplicates(candidates)
        nms_elapsed = (perf_counter() - nms_started) * 1000.0
        for index, candidate in enumerate(kept, start=1):
            candidate.candidate_id = f"candidate-{index}"
        diagnostic_started = perf_counter()
        pair_rejected = (self._rejected_pairs(xyz, source_indices, poles, accepted_ids)
                         if self.diagnostics["enabled"] else [])
        for candidate in self.last_suppressed:
            candidate.accepted = False
            candidate.reject_reason = "nms_suppressed"
        rejected_total = self._rejected_total + len(self.last_suppressed)
        diagnostic_limit = int(self.diagnostics["max_rejected_candidates"])
        self.last_rejected = [*pair_rejected, *self.last_suppressed][:diagnostic_limit]
        for index, candidate in enumerate(self.last_rejected, start=1):
            candidate.candidate_id = f"rejected-{index}"
        self.last_rejected_summary = {
            "total": int(rejected_total),
            "returned": len(self.last_rejected),
            "truncated": bool(rejected_total > len(self.last_rejected)),
        }
        self.last_trace.append(_candidate_trace(
            "rejected_candidate_diagnostics", rejected_total,
            len(self.last_rejected),
            ((perf_counter() - diagnostic_started) * 1000.0
             if self.diagnostics["enabled"] else None),
            status="completed" if self.diagnostics["enabled"] else "skipped",
            truncated=self.last_rejected_summary["truncated"]))
        selected_support = np.zeros(len(points), dtype=bool)
        for candidate in kept:
            selected_support[np.asarray(candidate.supporting_point_indices, dtype=np.int64)] = True
        self.last_masks["candidate_score"] = selected_support
        self.last_trace.append(_candidate_trace(
            "nms", len(candidates), len(kept), nms_elapsed,
            rejected_entities=len(candidates) - len(kept)))
        return kept

    def _rejected_pairs(self, xyz, source_indices, poles, accepted_ids) -> list[GoalCandidate]:
        """Describe near hypotheses rejected by pair gates, without changing decisions."""
        width_lo, width_hi = (float(v) for v in self.frame_cfg["width"])
        height_tol = float(self.pole_cfg["height_tol"])
        base_tol = float(self.pole_cfg["base_tol"])
        rejected = []
        for index, left in enumerate(poles):
            for right in poles[index + 1:]:
                if (id(left), id(right)) in accepted_ids:
                    continue
                width = float(np.linalg.norm(left.center_xy - right.center_xy))
                if width < width_lo:
                    reason = "width_too_small"
                elif width > width_hi:
                    reason = "width_too_large"
                elif abs(left.height - right.height) > height_tol:
                    reason = "height_mismatch"
                elif abs(left.base_z - right.base_z) > base_tol:
                    reason = "base_misaligned"
                else:
                    reason = "poor_geometry"
                crossbar = crossbar_coverage(xyz, left, right, self.frame_cfg["crossbar_z"])
                geometry = self._geometry_score(left, right)
                confidence = (float(self.scoring["geometry_weight"]) * geometry
                              + float(self.scoring["crossbar_weight"]) * crossbar)
                bbox = bbox_for_pair(left, right)
                local = self._support_indices(xyz, left, right)
                source = source_indices[local]
                rejected.append(GoalCandidate(
                    center=bbox["center"], size=bbox["size"], yaw=bbox["yaw"],
                    confidence=float(np.clip(confidence, 0.0, 1.0)),
                    crossbar=crossbar, pole_count=len(poles),
                    left_pole=self._pole_dict(left), right_pole=self._pole_dict(right),
                    supporting_point_indices=[int(v) for v in source],
                    score_breakdown={"geometry_score": round(float(geometry), 3),
                                     "crossbar_score": round(float(crossbar), 3)},
                    accepted=False, reject_reason=reason))
        # A dense fence can generate thousands of pairs.  Diagnostics retain
        # the 64 closest-to-valid hypotheses and report their exact reasons.
        target = 0.5 * (width_lo + width_hi)
        rejected.sort(key=lambda item: (abs(item.size[1] - target), -item.confidence))
        self._rejected_total = len(rejected)
        limit = int(self.diagnostics["max_rejected_candidates"])
        self._rejected_truncated = len(rejected) > limit
        rejected = rejected[:limit]
        for index, candidate in enumerate(rejected, start=1):
            candidate.candidate_id = f"rejected-{index}"
        return rejected

    def _support_indices(self, xyz: np.ndarray, left, right) -> np.ndarray:
        """Return the exact detector-input rows supporting a pole pair.

        Pole cell membership is exact.  Crossbar membership uses the same
        geometric band as ``crossbar_coverage`` and is included so clicking a
        candidate exposes both posts and the optional top bar.
        """
        parts = []
        if left.indices is not None:
            parts.append(np.asarray(left.indices, dtype=np.int64))
        if right.indices is not None:
            parts.append(np.asarray(right.indices, dtype=np.int64))
        direction = right.center_xy - left.center_xy
        length_sq = float(direction @ direction)
        if length_sq > 0:
            offsets = xyz[:, :2] - left.center_xy[None, :]
            projection = (offsets @ direction) / length_sq
            lateral = offsets - projection[:, None] * direction[None, :]
            z_lo, z_hi = (float(v) for v in self.frame_cfg["crossbar_z"])
            band = 0.5 * (z_hi - z_lo)
            tops_z = (left.top_z + right.top_z) / 2.0
            crossbar = np.flatnonzero(
                (projection > 0.02) & (projection < 0.98)
                & (np.linalg.norm(lateral, axis=1) <= band * 2.0)
                & (xyz[:, 2] >= tops_z - band) & (xyz[:, 2] <= tops_z + band))
            if len(crossbar):
                parts.append(crossbar)
        return np.unique(np.concatenate(parts)) if parts else np.empty(0, dtype=np.int64)

    def _suppress_duplicates(self, candidates: list[GoalCandidate]) -> list[GoalCandidate]:
        """Keep the best candidate within ``nms_distance`` of the already kept set."""
        radius = float(self.scoring["nms_distance"])
        kept: list[GoalCandidate] = []
        self.last_suppressed = []
        for candidate in candidates:
            center = np.asarray(candidate.center[:2])
            if all(float(np.linalg.norm(center - np.asarray(k.center[:2]))) > radius
                   for k in kept):
                kept.append(candidate)
            else:
                self.last_suppressed.append(candidate)
        return kept

    def _geometry_score(self, left, right) -> float:
        """Height agreement and base alignment, each normalised by its tolerance."""
        height_tol = float(self.pole_cfg["height_tol"])
        base_tol = float(self.pole_cfg["base_tol"])
        height_agreement = 1.0 - min(abs(left.height - right.height) / height_tol, 1.0)
        base_agreement = 1.0 - min(abs(left.base_z - right.base_z) / base_tol, 1.0)
        return 0.5 * height_agreement + 0.5 * base_agreement

    @staticmethod
    def _pole_dict(pole) -> dict:
        return {"center": [float(pole.center_xy[0]), float(pole.center_xy[1])],
                "base_z": pole.base_z, "top_z": pole.top_z,
                "observed_vertical_span": float(pole.top_z - pole.base_z),
                "estimated_full_height": None,
                "radius": round(pole.radius, 3), "count": pole.count}


def _detector_trace(stage: str, input_points: int, output_points: int,
                    latency_ms: float | None, status: str = "completed", **extra) -> dict:
    return {
        "stage": stage, "status": status, "entity": "point",
        "input_points": int(input_points), "output_points": int(output_points),
        "rejected_points": max(int(input_points) - int(output_points), 0),
        "latency_ms": None if latency_ms is None else round(float(latency_ms), 3),
        **extra,
    }


def _candidate_trace(stage: str, input_entities: int, output_entities: int,
                     latency_ms: float | None, status: str = "completed", **extra) -> dict:
    return {
        "stage": stage, "status": status, "entity": "candidate",
        "input_entities": int(input_entities), "output_entities": int(output_entities),
        "latency_ms": None if latency_ms is None else round(float(latency_ms), 3),
        **extra,
    }

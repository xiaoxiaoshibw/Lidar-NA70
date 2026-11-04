"""Composition of accumulator, detector and tracker — the demo pipeline.

The HTTP layer (``backend.demo_api``) and the offline evidence runner both
drive this class; it owns no I/O.  Two time scales are kept: single-frame
detection updates every frame, the sliding-window accumulated cloud feeds the
confirmation and bbox refinement.  The tracker consumes accumulated candidates
and falls back to single-frame ones when the window has no proposal.

Two strategies (``strategy.mode``):

- ``poles``（旧行为）：单帧与叠加云各自全图检测，tracker 负责去重。
- ``staged``（窗口长度可配置）：第 1 级 BEV 在纯 XY 平面提议球门区域
  （``bev.py``）；跟踪器持有中心后第 2 级把视野收缩到候选周围的 FOV
  （``focus.py``），检测只在 FOV 内进行——同一时间最多一个球门假设；
  第 3 级在 FOV 内做 z 轴分析修正包围盒高度。聚焦后优先用当前单帧的
  检测结果更新位置，叠加云只做确认，避免把拖影平均画进框里。
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from time import perf_counter

import numpy as np

from .accumulation import Accumulator
from .bev import BevRegion, bev_regions
from .buffer import StampedCloud
from .config import load_configuration
from .detector import GoalCandidate, GoalDetector
from .facade import FacadeGoalDetector
from .focus import crop_xy, refine_box_z
from .observability import build_observability
from .tracker import GoalState, GoalTracker

# 锁定后才收缩 FOV：CANDIDATE/LOCKING 期间保持全图 BEV 扫描，让更优候选
# 能持续替换（避免围栏缺口等低置信度结构提前吸住视野）；LOST 后回到全图
# BEV 搜索，REACQUIRE 仍守原 FOV。
_FOCUS_STATES = {"LOCKED", "TRACKING", "REACQUIRE"}


class GoalDemoPipeline:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or load_configuration()
        self.strategy = str(self.cfg.get("strategy", {}).get("mode", "poles"))
        if self.strategy not in ("staged", "poles", "facade"):
            raise ValueError(f"未知策略: {self.strategy}")
        self.accumulator = Accumulator(self.cfg["accumulation"])
        self.detector_single = GoalDetector(self.cfg["goal_detection"])
        self.detector_accumulated = GoalDetector(self.cfg["goal_detection"])
        range_cfg = self.cfg["accumulation"]["range"]
        for detector in (self.detector_single, self.detector_accumulated):
            detector.set_range(range_cfg["min"], range_cfg["max"])
        self.detector_facade_single: FacadeGoalDetector | None = None
        self.detector_facade_accumulated: FacadeGoalDetector | None = None
        if self.strategy == "facade":
            # roi 存在 → 人工 ROI 测量（G2）；roi 为空 → 全场提案搜索（G3 方向）。
            self.detector_facade_single = FacadeGoalDetector(self.cfg["goal_detection"])
            self.detector_facade_accumulated = FacadeGoalDetector(self.cfg["goal_detection"])
            for detector in (self.detector_facade_single, self.detector_facade_accumulated):
                detector.set_range(range_cfg["min"], range_cfg["max"])
        self.tracker = GoalTracker(self.cfg["tracking"], self.cfg["locking"],
                                   self.cfg["goal_detection"]["scoring"])
        self._last_device_time: float | None = None
        self._motion_statuses: dict[int, str] = {}
        self._evidence_time_quality = "frame_fallback_missing_device_time"
        if self.strategy == "staged":
            self.bev_cfg = self.cfg["bev"]
            self.focus_cfg = self.cfg["focus"]
            self.zrefine_cfg = self.cfg["zrefine"]
        self._frame_cfg = self.cfg["goal_detection"]["frame"]
        self.leveling_cfg = self.cfg["goal_detection"].get("z_leveling", {
            "enabled": False,
        })

    def process_frame(self, points: np.ndarray, frame_index: int,
                      device_time: float | None = None, *,
                      motion_status: str = "unknown") -> dict:
        """Feed one decoded frame; return the demo state snapshot for it."""
        cloud = StampedCloud(points, frame_index=frame_index, device_time=device_time)
        evidence_time_quality = self._resolve_evidence_time_quality(device_time)
        stats = self.accumulator.push(cloud)
        window = self.accumulator.buffer.window()
        self._motion_statuses[frame_index] = motion_status
        self._motion_statuses = {f.frame_index: self._motion_statuses.get(f.frame_index, "unknown")
                                 for f in window}
        times = [f.device_time for f in window]
        valid_times = all(t is not None and np.isfinite(t) for t in times)
        valid_times = valid_times and all(b >= a for a, b in zip(times, times[1:]))
        window_motion = ("confirmed_static" if all(v == "confirmed_static" for v in
                         self._motion_statuses.values()) else "moving" if "moving" in
                         self._motion_statuses.values() else "unknown")
        source_context = {
            "motion_status": window_motion,
            "source_frame_range": [window[0].frame_index, frame_index],
            "source_age_seconds": times[-1] - times[0] if valid_times else None,
        }
        acc_xyz, acc_intensity = self.accumulator.accumulated()
        acc_cloud = np.column_stack((acc_xyz, acc_intensity))
        # Ground fitting needs raw vertical samples, not 0.5 m voxel centroids.
        # Build the fallback only if the current frame cannot supply a plane.
        def raw_ground_window():
            blocks = []
            roi = (self.cfg['accumulation'].get('roi')
                   if self.leveling_cfg.get('reference_roi', 'detection') == 'detection'
                   else None)
            for source in window:
                block = source.points
                if roi is not None:
                    bounds = np.asarray(roi).reshape(3, 2)
                    keep = np.all((block[:, :3] >= bounds[:, 0]) &
                                  (block[:, :3] <= bounds[:, 1]), axis=1)
                    block = block[keep]
                blocks.append(block)
            return np.vstack(blocks)
        leveling = _estimate_ground_level(points, raw_ground_window, self.cfg,
                                          self.leveling_cfg, source_context=source_context)
        leveled_points = _apply_ground_level(points, leveling)
        leveled_acc_cloud = _apply_ground_level(acc_cloud, leveling)
        leveling_trace = _leveling_trace(leveling, len(points), len(acc_cloud))
        accumulation_trace = [{**stage, "source_cloud_id": self.accumulator.cloud_id}
                              for stage in self.accumulator.last_trace]
        if self.strategy == "staged":
            result = self._process_staged(
                points, acc_cloud, leveled_points, leveled_acc_cloud, stats,
                frame_index, device_time, evidence_time_quality,
                leveling, leveling_trace, accumulation_trace)
        elif self.strategy == "facade":
            result = self._process_facade(
                points, acc_cloud, leveled_points, leveled_acc_cloud, stats,
                frame_index, device_time, evidence_time_quality, window_motion,
                leveling, leveling_trace, accumulation_trace)
        else:
            result = self._process_poles(
                points, acc_cloud, leveled_points, leveled_acc_cloud, frame_index,
                device_time, evidence_time_quality, leveling, leveling_trace,
                accumulation_trace)
        result.update({
            "accumulated_frames": stats["accumulated_frames"],
            "accumulated_points": stats["accumulated_points"],
            "window_raw_points": stats["window_raw_points"],
            "accumulated_cloud": (acc_xyz, acc_intensity),
            "accumulated_cloud_id": self.accumulator.cloud_id,
            "accumulation_trace": accumulation_trace,
            "diagnostics_max_raw_refs": int(
                self.cfg["goal_detection"]["diagnostics"]["max_raw_refs_per_candidate"]),
        })
        _attach_single_source_refs(result, frame_index)
        raw_window = result.pop("_facade_raw_window", None)
        if raw_window is not None:
            _attach_raw_window_source_refs(
                result, raw_window["refs"], raw_window["cloud_id"],
                int(self.cfg["goal_detection"]["diagnostics"]["max_raw_refs_per_candidate"]))
        else:
            _attach_accumulated_source_refs(result, self.accumulator.provenance)
        return result

    # ------------------------------------------------------------- staged
    def _process_staged(self, points, acc_cloud, leveled_points, leveled_acc_cloud,
                        stats, frame_index, device_time, evidence_time_quality,
                        leveling, leveling_trace, accumulation_trace) -> dict:
        frames_in_window = int(stats["accumulated_frames"])
        half = float(self.focus_cfg["half_size"])
        prev = self.tracker.track
        focus_center = None
        if prev is not None and prev.center is not None \
                and prev.state.value in _FOCUS_STATES:
            focus_center = np.asarray(prev.center[:2], dtype=np.float64)

        regions: list[BevRegion] = []
        accumulated: list[GoalCandidate] = []
        single: list[GoalCandidate] = []
        accumulated_rejected: list[GoalCandidate] = []
        single_rejected: list[GoalCandidate] = []
        accumulated_debug = {"trace": [], "masks": {}}
        single_debug = {"trace": [], "masks": {}}
        acquisition_trace: list[dict] = []
        if focus_center is not None:
            accumulated, accumulated_rejected, accumulated_debug = _detect_cropped(
                self.detector_accumulated, leveled_acc_cloud, focus_center, half)
            single, single_rejected, single_debug = _detect_cropped(
                self.detector_single, leveled_points, focus_center, half)
        elif frames_in_window < int(self.bev_cfg["min_window_frames"]):
            # 窗口尚未填到 BEV 可用：退回全图检测，保证小窗口会话仍可用。
            accumulated = self.detector_accumulated.detect(leveled_acc_cloud)
            accumulated_rejected = list(self.detector_accumulated.last_rejected)
            accumulated_debug = _detector_debug(
                self.detector_accumulated, np.arange(len(leveled_acc_cloud)), len(leveled_acc_cloud),
                {"branch": "window_warmup"})
            single = self.detector_single.detect(np.asarray(leveled_points))
            single_rejected = list(self.detector_single.last_rejected)
            single_debug = _detector_debug(
                self.detector_single, np.arange(len(leveled_points)), len(leveled_points),
                {"branch": "window_warmup"})
        else:
            bev_started = perf_counter()
            bev_diagnostics = {}
            proposed_regions = bev_regions(leveled_acc_cloud[:, :3], frames_in_window,
                                           self.bev_cfg, self._frame_cfg,
                                           diagnostics=bev_diagnostics)
            acquisition_trace.append({
                "stage": "bev_proposal", "status": "completed", "entity": "region",
                "input_points": int(len(acc_cloud)),
                "output_entities": int(len(proposed_regions)),
                "latency_ms": round((perf_counter() - bev_started) * 1000.0, 3),
                "reason_counts": bev_diagnostics.get("reason_counts", {}),
                "dense_cells": bev_diagnostics.get("dense_cells", 0),
                "blob_count": bev_diagnostics.get("blob_count", 0),
                "input_entities": bev_diagnostics.get("pair_count", 0),
            })
            region_debug = []
            for region_index, region in enumerate(proposed_regions):
                regions.append(region)
                found, rejected, debug = _detect_cropped(
                    self.detector_accumulated, leveled_acc_cloud, region.center_xy, half)
                for stage in debug["trace"]:
                    stage["branch"] = f"bev-region-{region_index}"
                region_debug.append(debug)
                accumulated_rejected.extend(rejected)
                selection_started = perf_counter()
                if found:
                    accumulated.append(found[0])
                selection_elapsed = (perf_counter() - selection_started) * 1000.0
                acquisition_trace.append({
                    "stage": "region_best_selection", "status": "completed",
                    "entity": "candidate", "branch": f"bev-region-{region_index}",
                    "input_entities": len(found), "output_entities": int(bool(found)),
                    "rejected_entities": max(len(found) - 1, 0),
                    "latency_ms": round(selection_elapsed, 6),
                    "reason_counts": ({"lower_ranked_in_region": max(len(found) - 1, 0)}
                                      if len(found) > 1 else {}),
                })
            accumulated_debug = _merge_detector_debug(region_debug, len(acc_cloud))
            accumulated.sort(key=lambda candidate: candidate.confidence, reverse=True)
            for index, candidate in enumerate(accumulated, start=1):
                candidate.candidate_id = f"candidate-{index}"
        for index, candidate in enumerate(accumulated_rejected, start=1):
            candidate.candidate_id = f"rejected-{index}"
        for index, candidate in enumerate(single_rejected, start=1):
            candidate.candidate_id = f"rejected-{index}"

        evidence_cfg = self.cfg["tracking"]["evidence"]
        _annotate_single_candidates(
            [*single, *single_rejected], frame_index, device_time,
            evidence_time_quality, evidence_cfg)
        _annotate_accumulated_candidates(
            [*accumulated, *accumulated_rejected], self.accumulator.provenance,
            frame_index, device_time, evidence_time_quality, evidence_cfg)

        fresh_accumulated, recent_accumulated, freshness_trace = _fresh_accumulated_candidates(
            accumulated, frame_index,
            int(self.focus_cfg.get("accumulated_min_current_points", 1)))

        # 提议集：聚焦且当前帧有可信检测时只用当前帧（位置新鲜，抑制拖影
        # 平均）；否则交出全部已验证候选，由跟踪器做强候选/门限选择。
        if focus_center is not None and single \
                and single[0].confidence >= float(self.focus_cfg["single_min_confidence"]) \
                and bool(self.focus_cfg.get("prefer_single_frame", True)):
            proposals = [single[0]]
            proposal_source = "single"
        else:
            proposals = [*fresh_accumulated, *recent_accumulated]
            proposal_source = "accumulated"
        z_info = None
        refined: list[GoalCandidate] = []
        z_started = perf_counter()
        z_source_cloud = (leveled_points
                          if proposal_source == "single"
                          and leveling and leveling.get("enabled")
                          else leveled_acc_cloud)
        for candidate in proposals:
            fov = crop_xy(z_source_cloud, np.asarray(candidate.center[:2]), half)
            info = refine_box_z(fov, candidate.left_pole, candidate.right_pole,
                                self.zrefine_cfg)
            refined.append(_with_refined_z(candidate, info))
            if z_info is None:
                z_info = info
        _restore_candidate_levels(
            [*single, *single_rejected, *accumulated, *accumulated_rejected, *refined],
            leveling)
        z_info = _restore_z_info(
            z_info, leveling,
            proposals[0].center[:2] if proposals else None)
        z_trace = {
            "stage": "z_refine", "status": "completed" if proposals else "skipped",
            "entity": "candidate", "input_entities": len(proposals),
            "output_entities": len(refined),
            "latency_ms": (round((perf_counter() - z_started) * 1000.0, 3)
                           if proposals else None),
        }
        tracker_started = perf_counter()
        track = self.tracker.update(refined, frame_index, device_time)
        tracker_trace = {
            "stage": "association", "status": "completed", "entity": "candidate",
            "input_entities": len(refined),
            "output_entities": int(bool(self.tracker.last_association.get("accepted"))),
            "latency_ms": round((perf_counter() - tracker_started) * 1000.0, 3),
            "selected_candidate_id": self.tracker.last_association.get("candidate_id"),
            "reject_reason": self.tracker.last_association.get("reject_reason"),
        }
        goal = track.as_dict() if track else _searching()
        focus_info = {"active": focus_center is not None,
                      "center": [round(float(focus_center[0]), 2),
                                 round(float(focus_center[1]), 2)] if focus_center is not None else None,
                      "half_size": half}
        association = dict(self.tracker.last_association)
        selected = next((candidate for candidate in refined
                         if association.get("accepted")
                         and track is not None and track.has_measurement
                         and candidate.candidate_id == association.get("candidate_id")), None)
        return {
            "frame_index": frame_index,
            "device_time": device_time,
            "time_quality": evidence_time_quality,
            "imu": False,
            "single_candidates": [c.as_dict() for c in single],
            "accumulated_candidates": [c.as_dict() for c in accumulated],
            "single_rejected_candidates": [c.as_dict() for c in single_rejected],
            "accumulated_rejected_candidates": [c.as_dict() for c in accumulated_rejected],
            "rejected_candidate_summary": {
                "single": dict(self.detector_single.last_rejected_summary),
                "accumulated": dict(self.detector_accumulated.last_rejected_summary),
            },
            "goal": goal,
            "strategy": "staged",
            "focus": focus_info,
            "bev_regions": [region.as_dict() for region in regions],
            "z_refine": z_info,
            "association": association,
            "selected_measurement": selected.as_dict() if selected else None,
            "selected_measurement_source": proposal_source if selected else None,
            "execution_trace": {
                "single": [*single_debug["trace"], leveling_trace],
                "accumulated": [*accumulation_trace, *acquisition_trace,
                                *accumulated_debug["trace"], freshness_trace,
                                leveling_trace, z_trace, tracker_trace],
            },
            "observability": {
                "single": build_observability(points, self.cfg, frame_index, single,
                                                focus_info, goal, association, "single",
                                                 execution_trace=[*single_debug["trace"], leveling_trace],
                                                execution_masks=single_debug["masks"]),
                "accumulated": build_observability(
                    acc_cloud, self.cfg, frame_index, accumulated,
                    focus_info, goal, association, "accumulated",
                     execution_trace=[*accumulation_trace, *acquisition_trace,
                                      *accumulated_debug["trace"], freshness_trace,
                                      leveling_trace, z_trace, tracker_trace],
                    execution_masks=accumulated_debug["masks"]),
            },
        }

    # ------------------------------------------------------------- facade
    def _process_facade(self, points, acc_cloud, leveled_points, leveled_acc_cloud,
                        stats, frame_index, device_time, evidence_time_quality,
                        window_motion,
                        leveling, leveling_trace, accumulation_trace) -> dict:
        """Manual-ROI facade measurement — the full-field BEV search is
        deliberately bypassed (G2: the manual ROI is the search region).
        Measurement runs on the ROI-filtered *raw* window cloud, not on the
        0.5 m voxel accumulation: voxel centroids quantize the boundary
        measurements and the provenance of a raw window row is exact."""
        roi = self.cfg["accumulation"].get("roi")
        if leveling and leveling.get("enabled"):
            raw_cloud, raw_refs, raw_times = self._facade_raw_window()
            raw_leveled = _apply_ground_level(raw_cloud, leveling)
            if roi is not None:
                accumulated = self.detector_facade_accumulated.detect(raw_leveled)
                accumulated_rejected = list(self.detector_facade_accumulated.last_rejected)
                accumulated_debug = _detector_debug(
                    self.detector_facade_accumulated,
                    np.arange(len(raw_leveled)), len(raw_leveled),
                    {"branch": "facade", "cloud": "raw_window"})
            else:
                accumulated, accumulated_rejected, accumulated_debug = _facade_detect_regions(
                    self.detector_facade_accumulated, raw_leveled,
                    self.cfg["goal_detection"])
            window_cloud_id = f"raw-window:{frame_index:06d}-{len(raw_leveled)}"
            if roi is not None:
                single, single_rejected, single_debug = _facade_detect_cropped(
                    self.detector_facade_single, leveled_points,
                    roi, self.cfg["accumulation"]["range"])
            else:
                single, single_rejected, single_debug = _facade_detect_regions(
                    self.detector_facade_single, leveled_points,
                    self.cfg["goal_detection"])
        else:
            # Without a trusted ground model the height semantics are
            # undefined (G1), so measuring is refused instead of guessed.
            reason = (leveling or {}).get("reason", "unavailable")
            accumulated, single = [], []
            accumulated_rejected, single_rejected = [], []
            raw_refs, raw_times, window_cloud_id = [], [], None
            raw_leveled = leveled_acc_cloud
            accumulated_debug = {"trace": [_facade_refusal_trace(
                "no_ground_reference", reason, len(leveled_acc_cloud))], "masks": {}}
            single_debug = {"trace": [_facade_refusal_trace(
                "no_ground_reference", reason, len(np.asarray(points)))], "masks": {}}
        evidence_cfg = self.cfg["tracking"]["evidence"]
        _annotate_single_candidates(
            [*single, *single_rejected], frame_index, device_time,
            evidence_time_quality, evidence_cfg)
        _annotate_raw_window_candidates(
            [*accumulated, *accumulated_rejected], raw_refs, raw_times,
            frame_index, device_time, evidence_time_quality, evidence_cfg)
        motion_gate_trace = None
        if window_motion == "moving" and int(stats["accumulated_frames"]) > 1:
            # A current raw point does not make a fit across different moving
            # lidar_link frames a current-frame measurement. Preserve the
            # geometry for review, but withhold it from track confirmation.
            rejected_by_motion = len(accumulated)
            for candidate in [*accumulated, *accumulated_rejected]:
                candidate.coordinate_frame = "unaligned_sensor_window"
            for candidate in accumulated:
                candidate.accepted = False
                candidate.reject_reason = "uncompensated_moving_window"
                candidate.freshness.update({
                    "evidence_role": "UNCOMPENSATED_MOTION",
                    "freshness_reject_reason": "uncompensated_moving_window",
                    "accepted": False,
                })
            accumulated_rejected.extend(accumulated)
            accumulated = []
            motion_gate_trace = {
                "stage": "motion_window_gate", "status": "completed",
                "entity": "candidate", "input_entities": rejected_by_motion,
                "output_entities": 0, "rejected_entities": rejected_by_motion,
                "reason_counts": {"uncompensated_moving_window": rejected_by_motion},
                "motion_status": window_motion,
                "source_frame_count": int(stats["accumulated_frames"]),
                "latency_ms": None,
            }
        fresh_accumulated, recent_accumulated, freshness_trace = _fresh_accumulated_candidates(
            accumulated, frame_index,
            int(self.cfg["focus"].get("accumulated_min_current_points", 1)))
        proposals = [*fresh_accumulated, *recent_accumulated] or single
        proposal_source = ("accumulated" if [*fresh_accumulated, *recent_accumulated]
                           else "single")
        _restore_candidate_levels(
            [*single, *single_rejected, *accumulated, *accumulated_rejected], leveling)
        tracker_started = perf_counter()
        track = self.tracker.update(proposals, frame_index, device_time)
        tracker_trace = {
            "stage": "association", "status": "completed", "entity": "candidate",
            "input_entities": len(proposals),
            "output_entities": int(bool(self.tracker.last_association.get("accepted"))),
            "latency_ms": round((perf_counter() - tracker_started) * 1000.0, 3),
            "selected_candidate_id": self.tracker.last_association.get("candidate_id"),
            "reject_reason": self.tracker.last_association.get("reject_reason"),
        }
        goal = track.as_dict() if track else _searching()
        association = dict(self.tracker.last_association)
        selected = next((candidate for candidate in proposals
                         if association.get("accepted")
                         and track is not None and track.has_measurement
                         and candidate.candidate_id == association.get("candidate_id")), None)
        return {
            "frame_index": frame_index,
            "device_time": device_time,
            "time_quality": evidence_time_quality,
            "imu": False,
            "single_candidates": [c.as_dict() for c in single],
            "accumulated_candidates": [c.as_dict() for c in accumulated],
            "single_rejected_candidates": [c.as_dict() for c in single_rejected],
            "accumulated_rejected_candidates": [c.as_dict() for c in accumulated_rejected],
            "rejected_candidate_summary": {
                "single": dict(self.detector_facade_single.last_rejected_summary),
                "accumulated": dict(self.detector_facade_accumulated.last_rejected_summary),
                "motion_window_gate": int(motion_gate_trace["rejected_entities"])
                                      if motion_gate_trace else 0,
            },
            "goal": goal,
            "strategy": "facade",
            "focus": None,
            "bev_regions": [],
            "z_refine": None,
            "association": association,
            "selected_measurement": selected.as_dict() if selected else None,
            "selected_measurement_source": proposal_source if selected else None,
            "execution_trace": {
                "single": [*single_debug["trace"], leveling_trace],
                "accumulated": [*accumulation_trace, *accumulated_debug["trace"],
                                *([motion_gate_trace] if motion_gate_trace else []),
                                freshness_trace, leveling_trace, tracker_trace],
            },
            "observability": {
                "single": build_observability(points, self.cfg, frame_index, single,
                                                None, goal, association, "single",
                                                 execution_trace=[*single_debug["trace"], leveling_trace],
                                                execution_masks=single_debug["masks"]),
                "accumulated": build_observability(
                    raw_leveled, self.cfg, frame_index, accumulated,
                    None, goal, association, "accumulated",
                     execution_trace=[*accumulation_trace,
                                      *accumulated_debug["trace"],
                                      *([motion_gate_trace] if motion_gate_trace else []),
                                      freshness_trace,
                                      leveling_trace, tracker_trace],
                    execution_masks=accumulated_debug["masks"]),
            },
            "_facade_raw_window": {
                "refs": raw_refs, "times": raw_times, "cloud_id": window_cloud_id,
            },
        }

    def _facade_raw_window(self):
        """ROI-filtered raw window cloud with exact per-point source refs.

        Facade measurement runs on this cloud instead of the voxel
        accumulation: voxel centroids quantize boundary measurements, and raw
        rows carry exact ``(frame, point)`` provenance for the freshness
        contract.
        """
        roi = self.cfg["accumulation"].get("roi")
        bounds = (np.asarray(roi, dtype=np.float64).reshape(3, 2)
                  if roi is not None else None)
        blocks, refs, times = [], [], []
        for source in self.accumulator.buffer.window():
            block = np.asarray(source.points)
            if block.ndim != 2 or block.shape[1] < 3 or len(block) == 0:
                continue
            keep = np.isfinite(block[:, :3]).all(axis=1)
            if bounds is not None:
                keep &= np.all((block[:, :3] >= bounds[:, 0])
                               & (block[:, :3] <= bounds[:, 1]), axis=1)
            local = np.flatnonzero(keep)
            if not len(local):
                continue
            blocks.append(block[local])
            refs.extend((int(source.frame_index), int(index)) for index in local)
            times.extend([source.device_time] * len(local))
        if not blocks:
            return np.empty((0, 4), dtype=np.float32), [], []
        return np.vstack(blocks), refs, times

    # ------------------------------------------------------------- poles
    def _process_poles(self, points, acc_cloud, leveled_points, leveled_acc_cloud,
                       frame_index, device_time, evidence_time_quality,
                       leveling, leveling_trace, accumulation_trace) -> dict:
        accumulated = self.detector_accumulated.detect(leveled_acc_cloud)
        accumulated_rejected = list(self.detector_accumulated.last_rejected)
        accumulated_debug = _detector_debug(
            self.detector_accumulated, np.arange(len(leveled_acc_cloud)), len(leveled_acc_cloud),
            {"branch": "poles"})
        single = self.detector_single.detect(
            np.column_stack((np.asarray(leveled_points)[:, :3],
                             np.asarray(leveled_points)[:, 3])))
        single_rejected = list(self.detector_single.last_rejected)
        single_debug = _detector_debug(
            self.detector_single, np.arange(len(leveled_points)), len(leveled_points),
            {"branch": "poles"})
        evidence_cfg = self.cfg["tracking"]["evidence"]
        _annotate_single_candidates(
            [*single, *single_rejected], frame_index, device_time,
            evidence_time_quality, evidence_cfg)
        _annotate_accumulated_candidates(
            [*accumulated, *accumulated_rejected], self.accumulator.provenance,
            frame_index, device_time, evidence_time_quality, evidence_cfg)
        fresh_accumulated, recent_accumulated, freshness_trace = _fresh_accumulated_candidates(
            accumulated, frame_index,
            int(self.cfg["focus"].get("accumulated_min_current_points", 1)))
        accumulated_proposals = [*fresh_accumulated, *recent_accumulated]
        proposals = accumulated_proposals or single
        proposal_source = "accumulated" if accumulated_proposals else "single"
        _restore_candidate_levels(
            [*single, *single_rejected, *accumulated, *accumulated_rejected], leveling)
        tracker_started = perf_counter()
        track = self.tracker.update(proposals, frame_index, device_time)
        tracker_trace = {
            "stage": "association", "status": "completed", "entity": "candidate",
            "input_entities": len(proposals),
            "output_entities": int(bool(self.tracker.last_association.get("accepted"))),
            "latency_ms": round((perf_counter() - tracker_started) * 1000.0, 3),
            "selected_candidate_id": self.tracker.last_association.get("candidate_id"),
            "reject_reason": self.tracker.last_association.get("reject_reason"),
        }
        goal = track.as_dict() if track else _searching()
        association = dict(self.tracker.last_association)
        selected = next((candidate for candidate in proposals
                         if association.get("accepted")
                         and track is not None and track.has_measurement
                         and candidate.candidate_id == association.get("candidate_id")), None)
        return {
            "frame_index": frame_index,
            "device_time": device_time,
            "time_quality": evidence_time_quality,
            "imu": False,
            "single_candidates": [c.as_dict() for c in single],
            "accumulated_candidates": [c.as_dict() for c in accumulated],
            "single_rejected_candidates": [c.as_dict() for c in single_rejected],
            "accumulated_rejected_candidates": [c.as_dict() for c in accumulated_rejected],
            "rejected_candidate_summary": {
                "single": dict(self.detector_single.last_rejected_summary),
                "accumulated": dict(self.detector_accumulated.last_rejected_summary),
            },
            "goal": goal,
            "strategy": "poles",
            "focus": None,
            "bev_regions": [],
            "z_refine": None,
            "association": association,
            "selected_measurement": selected.as_dict() if selected else None,
            "selected_measurement_source": proposal_source if selected else None,
            "execution_trace": {
                "single": [*single_debug["trace"], leveling_trace],
                "accumulated": [*accumulation_trace, *accumulated_debug["trace"],
                                freshness_trace, leveling_trace, tracker_trace],
            },
            "observability": {
                "single": build_observability(points, self.cfg, frame_index, single,
                                                None, goal, association, "single",
                                                 execution_trace=[*single_debug["trace"], leveling_trace],
                                                execution_masks=single_debug["masks"]),
                "accumulated": build_observability(
                    acc_cloud, self.cfg, frame_index, accumulated,
                    None, goal, association, "accumulated",
                     execution_trace=[*accumulation_trace,
                                      *accumulated_debug["trace"], freshness_trace,
                                      leveling_trace, tracker_trace],
                    execution_masks=accumulated_debug["masks"]),
            },
        }

    @property
    def is_full_window(self) -> bool:
        return self.accumulator.buffer.is_full()

    def _resolve_evidence_time_quality(self, device_time: float | None) -> str:
        """Classify the current clock before source-age evidence is built."""
        if device_time is None or not np.isfinite(device_time):
            self._evidence_time_quality = "frame_fallback_missing_device_time"
            return self._evidence_time_quality
        value = float(device_time)
        if self._last_device_time is None:
            quality = "device_time"
        else:
            delta = value - self._last_device_time
            if delta <= 0:
                quality = "frame_fallback_non_monotonic_device_time"
            elif delta > float(self.cfg["tracking"]["max_dt"]):
                quality = "frame_fallback_time_jump"
            else:
                quality = "device_time"
        self._last_device_time = value
        self._evidence_time_quality = quality
        return quality


def _searching() -> dict:
    return {"detected": False, "state": GoalState.SEARCHING.value}


def _estimate_ground_level(points: np.ndarray, accumulated: np.ndarray,
                           cfg: dict, leveling_cfg: dict, source_context=None) -> dict:
    from .ground import estimate_ground
    return estimate_ground(points, accumulated, cfg, leveling_cfg, source_context)


def _apply_ground_level(cloud: np.ndarray, leveling: dict | None) -> np.ndarray:
    array = np.asarray(cloud)
    if not leveling or not leveling.get("enabled") or array.ndim != 2 \
            or array.shape[1] < 3 or len(array) == 0:
        return array
    output = np.array(array, dtype=np.float32, copy=True)
    output[:, 2] -= (float(leveling["slope_x"]) * output[:, 0]
                     + float(leveling["slope_y"]) * output[:, 1]
                     + float(leveling["intercept"]))
    return output


def _leveling_trace(leveling: dict | None, raw_points: int,
                    accumulated_points: int) -> dict:
    if not leveling or not leveling.get("enabled"):
        return {
            **(leveling or {}),
            "stage": "ground_leveling", "status": "skipped", "entity": "point",
            "input_points": int(raw_points), "output_points": int(raw_points),
            "rejected_points": 0, "latency_ms": None,
            "reason": leveling.get("reason", "unavailable")
            if leveling else "unavailable",
            "source": leveling.get("source") if leveling else None,
            "source_attempts": leveling.get("source_attempts", []) if leveling else [],
        }
    return {
        **leveling,
        "stage": "ground_leveling", "status": "completed", "entity": "point",
        "input_points": int(raw_points), "output_points": int(raw_points),
        "rejected_points": 0, "latency_ms": leveling.get("latency_ms"),
        "accumulated_points": int(accumulated_points),
        "source": leveling.get("source"),
        "source_attempts": leveling.get("source_attempts", []),
        "slope_x": round(float(leveling["slope_x"]), 6),
        "slope_y": round(float(leveling["slope_y"]), 6),
        "intercept": round(float(leveling["intercept"]), 6),
        "residual_std": round(float(leveling["residual_std"]), 6),
    }


def _restore_candidate_levels(candidates, leveling: dict | None) -> None:
    if not leveling or not leveling.get("enabled"):
        return
    visited = set()
    for candidate in candidates:
        if id(candidate) in visited:
            continue
        visited.add(id(candidate))
        from .geometry import oriented_box_corners
        corners = np.asarray(oriented_box_corners(candidate.center, candidate.size, candidate.yaw), dtype=float)
        corners[:, 2] += (float(leveling["slope_x"]) * corners[:, 0]
                         + float(leveling["slope_y"]) * corners[:, 1]
                         + float(leveling["intercept"]))
        candidate.sensor_corners = corners.tolist()
        candidate.height_reference = 'ground_relative_z'
        center = list(candidate.center)
        offset = (float(leveling["slope_x"]) * center[0]
                  + float(leveling["slope_y"]) * center[1]
                  + float(leveling["intercept"]))
        center[2] += offset
        candidate.center = center
        for pole_name in ("left_pole", "right_pole"):
            pole = getattr(candidate, pole_name, None)
            if not pole:
                continue
            pole = dict(pole)
            setattr(candidate, pole_name, pole)
            xy = pole.get("center") or [center[0], center[1]]
            pole_offset = (float(leveling["slope_x"]) * float(xy[0])
                           + float(leveling["slope_y"]) * float(xy[1])
                           + float(leveling["intercept"]))
            for key in ("base_z", "top_z"):
                if key in pole and pole[key] is not None:
                    pole[key] = float(pole[key]) + pole_offset


def _restore_z_info(info: dict | None, leveling: dict | None,
                    anchor_xy=None) -> dict | None:
    if not info or not leveling or not leveling.get("enabled"):
        return info
    xy = np.asarray(anchor_xy if anchor_xy is not None else [0.0, 0.0], dtype=float)
    offset = (float(leveling["slope_x"]) * xy[0]
              + float(leveling["slope_y"]) * xy[1]
              + float(leveling["intercept"]))
    restored = dict(info)
    for key in ("ground_z", "top_z", "center_z"):
        if restored.get(key) is not None:
            restored[key] = round(float(restored[key]) + offset, 3)
    return restored


def _fresh_accumulated_candidates(candidates, frame_index: int,
                                  min_current_points: int):
    """Keep only measurements with raw support from the current frame.

    Historical voxels may still propose a search region, but cannot pose as a
    current measurement after an object disappears.  The decision uses the
    existing voxel-to-raw provenance instead of a heuristic age counter.
    """
    fresh = []
    recent = []
    stale = 0
    reason_counts: dict[str, int] = {}
    expired = []
    for candidate in candidates:
        evidence = candidate.freshness or {}
        current_refs = int(evidence.get("current_frame_points", 0))
        accepted = (evidence.get("evidence_role") == "MEASURED_CURRENT"
                    and current_refs >= min_current_points)
        evidence["min_current_frame_points"] = int(min_current_points)
        evidence["accepted"] = accepted
        if accepted:
            evidence["freshness_reject_reason"] = None
            fresh.append(candidate)
        elif evidence.get("evidence_role") == "PREDICTED_RECENT":
            evidence["freshness_reject_reason"] = "not_current_frame"
            recent.append(candidate)
            stale += 1
            reason_counts["not_current_frame"] = reason_counts.get(
                "not_current_frame", 0) + 1
        else:
            stale += 1
            reason = ("no_current_frame_support"
                      if evidence.get("evidence_role") != "EXPIRED_HISTORY"
                      else "expired_source_evidence")
            evidence["freshness_reject_reason"] = reason
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if evidence.get("evidence_role") == "EXPIRED_HISTORY":
                expired.append({
                    "candidate_id": candidate.candidate_id,
                    "observation_signature": evidence.get("observation_signature"),
                    "newest_source_frame": evidence.get("newest_source_frame"),
                    "newest_source_device_time": evidence.get("newest_source_device_time"),
                    "source_age_frames": evidence.get("source_age_frames"),
                    "source_age_seconds": evidence.get("source_age_seconds"),
                    "reason": evidence.get("freshness_reject_reason"),
                })
        candidate.freshness = evidence
    return fresh, recent, {
        "stage": "measurement_freshness", "status": "completed",
        "entity": "candidate", "input_entities": len(candidates),
        "output_entities": len(fresh), "predicted_entities": len(recent),
        "rejected_entities": stale,
        "latency_ms": None,
        "reason_counts": reason_counts,
        "expired_source_evidence": expired,
        "frame_index": int(frame_index),
        "min_current_frame_points": int(min_current_points),
    }


def _annotate_single_candidates(candidates, frame_index: int,
                                device_time: float | None,
                                time_quality: str, evidence_cfg: dict) -> None:
    for candidate in candidates:
        refs = [(int(frame_index), int(index), device_time)
                for index in candidate.supporting_point_indices]
        _set_candidate_freshness(candidate, refs, frame_index, device_time,
                                  time_quality, evidence_cfg)


def _annotate_accumulated_candidates(candidates, provenance, frame_index: int,
                                     device_time: float | None,
                                     time_quality: str, evidence_cfg: dict) -> None:
    for candidate in candidates:
        refs = []
        for voxel_index in candidate.supporting_point_indices:
            if 0 <= int(voxel_index) < provenance.output_count:
                sources = provenance.sources_for(int(voxel_index))
                times = provenance.device_times_for(int(voxel_index))
                refs.extend((int(frame), int(point),
                             None if not np.isfinite(time) else float(time))
                            for (frame, point), time in zip(sources, times))
        _set_candidate_freshness(candidate, refs, frame_index, device_time,
                                  time_quality, evidence_cfg)


def _set_candidate_freshness(candidate: GoalCandidate, refs: list[tuple],
                             frame_index: int, device_time: float | None,
                             time_quality: str, evidence_cfg: dict) -> None:
    """Attach the immutable raw-observation evidence contract to a candidate."""
    unique_refs = sorted({(int(frame), int(point)) for frame, point, _ in refs})
    frames = sorted({frame for frame, _ in unique_refs})
    finite_times = [float(time) for _, _, time in refs
                    if time is not None and np.isfinite(time)]
    newest_frame = max(frames) if frames else None
    newest_time = max(finite_times) if finite_times else None
    age_frames = (max(0, int(frame_index) - newest_frame)
                  if newest_frame is not None else None)
    age_seconds = None
    effective_quality = time_quality
    if (time_quality == "device_time" and device_time is not None
            and np.isfinite(device_time) and newest_time is not None):
        delta = float(device_time) - newest_time
        if delta >= 0:
            age_seconds = round(delta, 6)
        else:
            effective_quality = "frame_fallback_non_monotonic_device_time"
    current_points = sum(1 for frame, _ in unique_refs if frame == int(frame_index))
    signature_body = json.dumps(unique_refs, separators=(",", ":"), ensure_ascii=True)
    signature = "obs:" + hashlib.sha256(signature_body.encode("utf-8")).hexdigest()[:20]
    if not unique_refs:
        role = "EXPIRED_HISTORY"
        reject_reason = "missing_provenance"
    elif int(frame_index) in frames:
        role = "MEASURED_CURRENT"
        reject_reason = None
    else:
        recent_frames = age_frames is not None and age_frames <= int(
            evidence_cfg["recent_max_age_frames"])
        recent_seconds = (age_seconds is not None and age_seconds <= float(
            evidence_cfg["recent_max_age_seconds"]))
        # When the clock is unavailable or fell back, frame age is the
        # authoritative boundary; device time is only an additional guard.
        recent = recent_frames and (recent_seconds if age_seconds is not None else True)
        role = "PREDICTED_RECENT" if recent else "EXPIRED_HISTORY"
        reject_reason = "not_current_frame" if recent else "source_age_exceeded"
    candidate.freshness = {
        "evidence_role": role,
        "newest_source_frame": newest_frame,
        "newest_source_device_time": newest_time,
        "source_age_frames": age_frames,
        "source_age_seconds": age_seconds,
        "unique_observation_frames": len(frames),
        "observation_signature": signature,
        "freshness_reject_reason": reject_reason,
        "time_quality": effective_quality,
        "current_frame_points": int(current_points),
        "source_ref_count": len(unique_refs),
    }


def _with_refined_z(candidate: GoalCandidate, z_info: dict | None) -> GoalCandidate:
    if not z_info:
        return candidate
    center = list(candidate.center)
    size = list(candidate.size)
    center[2] = float(z_info["center_z"])
    size[2] = float(z_info["height"])
    return replace(candidate, center=center, size=size)


def _detect_cropped(detector: GoalDetector, points: np.ndarray, center_xy,
                    half_size: float) -> tuple[list[GoalCandidate], list[GoalCandidate], dict]:
    """Detect in an XY crop while preserving indexes into the source cloud."""
    cloud = np.asarray(points)
    if cloud.ndim != 2 or len(cloud) == 0:
        return [], [], {"trace": [], "masks": {}}
    center = np.asarray(center_xy, dtype=np.float64)
    crop_started = perf_counter()
    mask = ((np.abs(cloud[:, 0] - center[0]) <= half_size)
            & (np.abs(cloud[:, 1] - center[1]) <= half_size))
    crop_elapsed = (perf_counter() - crop_started) * 1000.0
    source_indices = np.flatnonzero(mask)
    found = detector.detect(cloud[mask])
    rejected = list(detector.last_rejected)
    debug = _detector_debug(detector, source_indices, len(cloud), {
        "crop_center": [float(center[0]), float(center[1])],
        "crop_half_size": float(half_size),
    })
    crop_trace = {
        "stage": "focus_crop", "status": "completed", "entity": "point",
        "input_points": int(len(cloud)), "output_points": int(mask.sum()),
        "rejected_points": int(len(cloud) - mask.sum()),
        "latency_ms": round(crop_elapsed, 3),
        "crop_center": [float(center[0]), float(center[1])],
        "crop_half_size": float(half_size),
    }
    debug["trace"].insert(0, crop_trace)
    for candidate in [*found, *rejected]:
        local = np.asarray(candidate.supporting_point_indices, dtype=np.int64)
        local = local[(local >= 0) & (local < len(source_indices))]
        candidate.supporting_point_indices = [int(v) for v in source_indices[local]]
    return found, rejected, debug


def _facade_detect_cropped(detector: FacadeGoalDetector, points: np.ndarray,
                           roi, range_cfg: dict
                           ) -> tuple[list[GoalCandidate], list[GoalCandidate], dict]:
    """Detect on the ROI crop of a single frame, remapping indices to it."""
    cloud = np.asarray(points)
    if cloud.ndim != 2 or cloud.shape[1] < 3 or len(cloud) == 0 \
            or detector is None:
        return [], [], {"trace": [], "masks": {}}
    bounds = np.asarray(roi, dtype=np.float64).reshape(3, 2)
    xyz = cloud[:, :3].astype(np.float64, copy=False)
    mask = np.isfinite(xyz).all(axis=1)
    mask &= np.all((xyz[:, :2] >= bounds[:2, 0]) & (xyz[:, :2] <= bounds[:2, 1]),
                   axis=1)
    distance = np.linalg.norm(xyz, axis=1)
    mask &= (distance >= float(range_cfg["min"])) & (distance <= float(range_cfg["max"]))
    source_indices = np.flatnonzero(mask)
    found = detector.detect(cloud[source_indices]) if len(source_indices) else []
    rejected = list(detector.last_rejected)
    debug = _detector_debug(detector, source_indices, len(cloud),
                            {"branch": "facade", "crop": "roi"})
    for candidate in [*found, *rejected]:
        local = np.asarray(candidate.supporting_point_indices, dtype=np.int64)
        local = local[(local >= 0) & (local < len(source_indices))]
        candidate.supporting_point_indices = [int(v) for v in source_indices[local]]
    return found, rejected, debug


def _facade_detect_regions(detector: FacadeGoalDetector, points: np.ndarray,
                           detection_cfg: dict
                           ) -> tuple[list[GoalCandidate], list[GoalCandidate], dict]:
    """Full-field facade search: propose regions, measure each, merge and NMS.

    The proposal clusters vertical-structure points on an XY grid; each region
    is measured independently by the facade detector, so one bad region cannot
    tilt another's facade fit.  Supporting indices are remapped to the source
    cloud and duplicate hypotheses across regions are suppressed by the
    scoring NMS distance.
    """
    cloud = np.asarray(points)
    empty = {"trace": [], "masks": {}}
    if cloud.ndim != 2 or cloud.shape[1] < 3 or len(cloud) == 0 or detector is None:
        return [], [], empty
    from .facade import propose_regions
    cfg = detector.cfg
    xyz = cloud[:, :3].astype(np.float64, copy=False)
    keep = np.isfinite(xyz).all(axis=1)
    if detector.range_cfg is not None:
        distance = np.linalg.norm(xyz, axis=1)
        keep &= (distance >= detector.range_cfg[0]) & (distance <= detector.range_cfg[1])
    z_band = (float(cfg["z_band"][0]), float(cfg["z_band"][1]))
    keep &= (xyz[:, 2] >= z_band[0]) & (xyz[:, 2] <= z_band[1])
    proposal_started = perf_counter()
    band_indices = np.flatnonzero(keep)
    region_indices = propose_regions(xyz[keep, :2], cfg)
    proposal_trace = {
        "stage": "facade_proposal", "status": "completed", "entity": "region",
        "input_points": len(cloud), "band_points": int(keep.sum()),
        "output_entities": len(region_indices),
        "latency_ms": round((perf_counter() - proposal_started) * 1000.0, 3),
    }
    candidates: list[GoalCandidate] = []
    rejected: list[GoalCandidate] = []
    region_debug = []
    for region_index, indices in enumerate(region_indices):
        found, region_rejected, debug = _detector_debug_detect(
            detector, cloud, band_indices[indices])
        for stage in debug["trace"]:
            stage["branch"] = f"facade-region-{region_index}"
        region_debug.append(debug)
        candidates.extend(found)
        rejected.extend(region_rejected)
    radius = float(detector.scoring["nms_distance"])
    kept: list[GoalCandidate] = []
    for candidate in sorted(candidates, key=lambda c: c.confidence, reverse=True):
        center = np.asarray(candidate.center[:2])
        if all(float(np.linalg.norm(center - np.asarray(k.center[:2]))) > radius
               for k in kept):
            kept.append(candidate)
        else:
            candidate.accepted = False
            candidate.reject_reason = "nms_suppressed"
            rejected.append(candidate)
    for index, candidate in enumerate(kept, start=1):
        candidate.candidate_id = f"candidate-{index}"
    for index, candidate in enumerate(rejected, start=1):
        candidate.candidate_id = f"rejected-{index}"
    masks: dict[str, np.ndarray] = {}
    for item in region_debug:
        for name, mask in item["masks"].items():
            merged = masks.setdefault(name, np.zeros(len(cloud), dtype=bool))
            merged |= mask
    trace = [proposal_trace]
    for item in region_debug:
        trace.extend(item["trace"])
    return kept, rejected, {"trace": trace, "masks": masks}


def _detector_debug_detect(detector: FacadeGoalDetector, cloud: np.ndarray,
                           region_indices: np.ndarray
                           ) -> tuple[list[GoalCandidate], list[GoalCandidate], dict]:
    found = detector.detect(cloud[region_indices])
    rejected = list(detector.last_rejected)
    debug = _detector_debug(detector, region_indices, len(cloud),
                            {"branch": "facade-region"})
    for candidate in [*found, *rejected]:
        local = np.asarray(candidate.supporting_point_indices, dtype=np.int64)
        local = local[(local >= 0) & (local < len(region_indices))]
        candidate.supporting_point_indices = [int(v) for v in region_indices[local]]
    return found, rejected, debug


def _facade_refusal_trace(reason: str, detail, input_points: int) -> dict:
    return {
        "stage": "facade_measurement", "status": "skipped", "entity": "point",
        "input_points": int(input_points), "output_points": 0,
        "rejected_points": 0, "latency_ms": None,
        "reason": reason, "ground_reason": detail,
    }


def _detector_debug(detector: GoalDetector, source_indices: np.ndarray,
                    full_count: int, context: dict) -> dict:
    masks = {}
    for name, local_mask in detector.last_masks.items():
        full = np.zeros(full_count, dtype=bool)
        local_indexes = np.flatnonzero(local_mask)
        local_indexes = local_indexes[local_indexes < len(source_indices)]
        full[source_indices[local_indexes]] = True
        masks[name] = full
    trace = [{**stage, **context} for stage in detector.last_trace]
    return {"trace": trace, "masks": masks}


def _merge_detector_debug(items: list[dict], full_count: int) -> dict:
    if not items:
        return {"trace": [], "masks": {}}
    masks: dict[str, np.ndarray] = {}
    trace = []
    for item in items:
        trace.extend(item["trace"])
        for name, mask in item["masks"].items():
            masks.setdefault(name, np.zeros(full_count, dtype=bool))
            masks[name] |= mask
    return {"trace": trace, "masks": masks}


def _attach_single_source_refs(result: dict, frame_index: int) -> None:
    collections = [result.get("single_candidates", []),
                   result.get("single_rejected_candidates", [])]
    if result.get("selected_measurement_source") == "single" \
            and result.get("selected_measurement") is not None:
        collections.append([result["selected_measurement"]])
    for candidates in collections:
        for candidate in candidates:
            candidate["source_cloud_id"] = f"raw:{frame_index}"
            candidate["supporting_raw_point_refs"] = [
                [int(frame_index), int(index)]
                for index in candidate.get("supporting_point_indices", [])]


def _annotate_raw_window_candidates(candidates, refs, times, frame_index: int,
                                    device_time: float | None,
                                    time_quality: str, evidence_cfg: dict) -> None:
    """Freshness from exact raw-window rows instead of voxel provenance."""
    for candidate in candidates:
        triples = []
        for row in candidate.supporting_point_indices:
            if 0 <= int(row) < len(refs):
                time = times[int(row)]
                triples.append((refs[int(row)][0], refs[int(row)][1],
                                None if time is None or not np.isfinite(time)
                                else float(time)))
        _set_candidate_freshness(candidate, triples, frame_index, device_time,
                                 time_quality, evidence_cfg)


def _attach_raw_window_source_refs(result: dict, refs, cloud_id: str | None,
                                   max_refs: int) -> None:
    collections = [result.get("accumulated_candidates", []),
                   result.get("accumulated_rejected_candidates", [])]
    if result.get("selected_measurement_source") == "accumulated" \
            and result.get("selected_measurement") is not None:
        collections.append([result["selected_measurement"]])
    for candidates in collections:
        for candidate in candidates:
            unique = sorted({(int(refs[int(row)][0]), int(refs[int(row)][1]))
                             for row in candidate.get("supporting_point_indices", [])
                             if 0 <= int(row) < len(refs)})
            total = len(unique)
            candidate["source_cloud_id"] = cloud_id
            candidate["supporting_raw_refs_total"] = total
            candidate["supporting_raw_refs_truncated"] = total > max_refs
            candidate["supporting_raw_point_refs"] = [list(ref) for ref
                                                      in unique[:max_refs]]


def _attach_accumulated_source_refs(result: dict, provenance) -> None:
    collections = [result.get("accumulated_candidates", []),
                   result.get("accumulated_rejected_candidates", [])]
    if result.get("selected_measurement_source") == "accumulated" \
            and result.get("selected_measurement") is not None:
        collections.append([result["selected_measurement"]])
    for candidates in collections:
        for candidate in candidates:
            refs = []
            for voxel_index in candidate.get("supporting_point_indices", []):
                if 0 <= int(voxel_index) < provenance.output_count:
                    refs.extend(provenance.sources_for(int(voxel_index)).tolist())
            candidate["source_cloud_id"] = result.get("accumulated_cloud_id")
            candidate["supporting_raw_point_refs"] = sorted({
                (int(frame), int(point)) for frame, point in refs})
            total_refs = len(candidate["supporting_raw_point_refs"])
            limit = int(result.get("diagnostics_max_raw_refs", 4096))
            candidate["supporting_raw_refs_total"] = total_refs
            candidate["supporting_raw_refs_truncated"] = total_refs > limit
            candidate["supporting_raw_point_refs"] = candidate[
                "supporting_raw_point_refs"][:limit]
            candidate["supporting_raw_point_refs"] = [list(ref) for ref in
                                                       candidate["supporting_raw_point_refs"]]

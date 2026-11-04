"""Wire window-level pipeline stage orchestration.

`run_detection_window` runs, for one detection window (one RayBatch built
from a `SelectedWindow` decode):
  proposals -> fit -> finite-support validation -> evidence_classification.

The result is a DetectionOutput with:
  * every executed stage's trace row (stage,status,counts,reject reasons);
  * status bucket - LINE only if every gate passed, else UNKNOWN_LINE /
    NO_CURRENT_MEASUREMENT / UNOBSERVABLE with the first failing stage;
  * source-audit fields (WindowInputs.source_window_id, capture_sha256).
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

import numpy as np

from ..observations import RayBatch
from ..windows import SelectedWindow
from .config import WireConfig
from .constants import (GEOMETRY_LINE, GEOMETRY_UNKNOWN_LINE,
                        STATUS_MEASURED_CURRENT, STATUS_NO_CURRENT_MEASUREMENT,
                        STATUS_UNKNOWN, STATUS_UNOBSERVABLE)
from .fitting import fit_line_ransac
from .proposal_full_fov import propose_full_fov
from .proposals import make_manual_roi_proposal
from .validation import evaluate_finite_support


@dataclass(frozen=True)
class WindowInputs:
    batch: RayBatch
    selected_window: SelectedWindow
    source_window_id: str
    manual_roi_aabb_m: tuple | None
    config: WireConfig
    window_end_device_time_s: float | None
    availability: str = "available"  # available|partial_capture|invalid_time
    enable_full_fov: bool = False
    full_fov_config: FullFovConfig | None = None


@dataclass(frozen=True)
class DetectionOutput:
    status: str
    geometry_class: str | None
    first_blocking_stage: str | None
    reject_reason: str | None
    candidate_line: dict | None
    stage_trace: tuple
    window_id: str
    source_window_id: str
    capture_sha256: str

    def to_dict(self) -> dict:
        return {
            "schema_version": "na70-wire-pipeline-output-v1",
            "status": self.status,
            "geometry_class": self.geometry_class,
            "first_blocking_stage": self.first_blocking_stage,
            "reject_reason": self.reject_reason,
            "candidate_line": self.candidate_line,
            "stage_trace": [dict(row) for row in self.stage_trace],
            "window_id": self.window_id,
            "source_window_id": self.source_window_id,
            "capture_sha256": self.capture_sha256,
        }


def _trace(stage, stage_status, **extra):
    row = {"stage": stage, "status": stage_status}
    for key, value in extra.items():
        row[key] = value if not isinstance(value, np.ndarray) else value.tolist()
    return row


def run_detection_window(inputs: WindowInputs) -> DetectionOutput:
    batch = inputs.batch
    cfg = inputs.config
    stages: list[dict] = []

    def output(status, geometry, first_block, reject, candidate=None):
        return DetectionOutput(status, geometry, first_block, reject, candidate,
                               tuple(stages), inputs.selected_window.end_frame_index
                               if inputs.selected_window else None,
                               inputs.source_window_id, batch.capture_sha256)

    stages.append(_trace("decode", "completed", input_rays=batch.ray_count,
                         source_window_id=inputs.source_window_id))
    if inputs.availability == "invalid_time":
        stages.append(_trace("time_quality", "rejected", reject_reason="INVALID_TIME"))
        return output(STATUS_UNOBSERVABLE, None, "time_quality", "INVALID_TIME")
    stages.append(_trace("time_quality", "completed"))
    stages.append(_trace("direction_quality", "completed",
                         direction_known_count=int(batch.direction_known.sum()),
                         direction_unknown_count=int((~batch.direction_known).sum())))
    stages.append(_trace("motion_gate", "skipped",
                         note="static PCAP assumed; moving-without-pose rejected by W3 contract"))
    if inputs.availability == "partial_capture":
        stages.append(_trace("window_quality", "skipped",
                             note="partial capture kept for diagnosis",
                             reject_reason="PARTIAL_WINDOW"))
    else:
        stages.append(_trace("window_quality", "completed"))

    if not batch.valid_return.any():
        stages.append(_trace("valid_return", "rejected", reject_reason="NO_VALID_RETURN",
                             input_count=batch.ray_count, accepted_count=0))
        return output(STATUS_NO_CURRENT_MEASUREMENT, None, "valid_return", "NO_VALID_RETURN")
    stages.append(_trace("valid_return", "completed",
                         valid_count=int(batch.valid_return.sum())))

    if inputs.manual_roi_aabb_m:
        if not cfg.proposal.use_manual_roi:
            stages.append(_trace("roi_or_proposal", "rejected", reject_reason="NO_PROPOSAL"))
            return output(STATUS_UNKNOWN, None, "roi_or_proposal", "NO_PROPOSAL")
        proposals = (make_manual_roi_proposal(batch, inputs.manual_roi_aabb_m,
                                              require_direction_known=cfg.validation.require_direction_known),)
        stages.append(_trace("roi_or_proposal", "completed",
                             input_count=batch.ray_count,
                             output_count=proposals[0].ray_count,
                             proposal_kind="manual_roi",
                             aabb=list(inputs.manual_roi_aabb_m)))
    elif inputs.enable_full_fov:
        full_fov_cfg = inputs.full_fov_config
        if full_fov_cfg is None:
            stages.append(_trace("roi_or_proposal", "rejected",
                                 reject_reason="NO_PROPOSAL",
                                 note="enable_full_fov requires full_fov_config"))
            return output(STATUS_UNKNOWN, None, "roi_or_proposal", "NO_PROPOSAL")
        proposals = propose_full_fov(batch, full_fov_cfg)
        stages.append(_trace("roi_or_proposal", "completed",
                             input_count=batch.ray_count,
                             candidate_count=len(proposals),
                             proposal_kind="full_fov_component",
                             max_components=full_fov_cfg.max_components))
        if not proposals:
            return output(STATUS_NO_CURRENT_MEASUREMENT, None,
                          "roi_or_proposal", "NO_PROPOSAL")
    else:
        stages.append(_trace("roi_or_proposal", "skipped", note="no manual ROI provided",
                             reject_reason="NO_PROPOSAL"))
        return output(STATUS_UNKNOWN, None, "roi_or_proposal", "NO_PROPOSAL")

    best_fit = None
    best_support = None
    first_fit_reject = None
    first_support_reject = None
    proposal_outcomes: list[dict] = []
    for proposal_index, proposal in enumerate(proposals):
        fit = fit_line_ransac(batch, proposal, cfg.fit)
        if not fit.ok:
            if first_fit_reject is None:
                first_fit_reject = fit.reject_reason
            proposal_outcomes.append({
                "proposal_index": proposal_index, "proposal_kind": proposal.kind,
                "stage": "fit", "status": "rejected",
                "reject_reason": fit.reject_reason,
                "input_count": proposal.ray_count})
            continue
        support = evaluate_finite_support(batch, fit, cfg.validation,
                                          inputs.window_end_device_time_s)
        if not support.ok:
            if first_support_reject is None:
                first_support_reject = support.reject_reason
            proposal_outcomes.append({
                "proposal_index": proposal_index, "proposal_kind": proposal.kind,
                "stage": "finite_support", "status": "rejected",
                "reject_reason": support.reject_reason,
                "reject_counts": support.reject_counts,
                "input_count": proposal.ray_count,
                "unique_packet_count": support.unique_packet_count,
                "span_m": support.span_m,
                "coverage_fraction": support.coverage_fraction})
            continue
        proposal_outcomes.append({
            "proposal_index": proposal_index, "proposal_kind": proposal.kind,
            "stage": "finite_support", "status": "completed",
            "input_count": proposal.ray_count,
            "unique_packet_count": support.unique_packet_count,
            "span_m": support.span_m})
        if best_support is None or support.span_m > best_support.span_m:
            best_fit, best_support = fit, support
    stages.append(_trace("proposals_outcomes", "completed",
                         proposal_count=len(proposals),
                         outcomes=proposal_outcomes))
    if best_fit is None:
        reason = first_support_reject or first_fit_reject or "NO_PROPOSAL"
        stage = "finite_support" if first_support_reject else "fit"
        stages.append(_trace(stage, "rejected", reject_reason=reason,
                             proposal_count=len(proposals)))
        if first_support_reject:
            return output(STATUS_UNKNOWN, None, "finite_support", reason)
        return output(STATUS_UNKNOWN, None, "fit", reason)
    fit, support = best_fit, best_support
    stages.append(_trace("fit", "completed",
                         seed=cfg.fit.fixed_seed,
                         thresh_m=cfg.fit.thresh_m,
                         iterations=fit.iteration_count,
                         inlier_ray_count=int(fit.inlier_ray_indices.size)))
    support_trace_kwargs = {
        "ok": support.ok, "reject_reason": support.reject_reason,
        "reject_counts": support.reject_counts,
        "unique_packet_count": support.unique_packet_count,
        "unique_frame_count": support.unique_frame_count,
        "span_m": support.span_m, "span_bin_m": support.span_bin_m,
        "coverage_fraction": support.coverage_fraction,
        "largest_internal_gap_m": support.largest_internal_gap_m,
        "max_bin_lateral_spread_m": support.max_bin_lateral_spread_m,
        "sag_amplitude_m": support.sag_amplitude_m,
        "rms_orthogonal_m": support.rms_orthogonal_m,
        "p95_orthogonal_m": support.p95_orthogonal_m,
        "nearest_distance_m": support.nearest_distance_m,
        "source_timestamp_last_s": support.source_timestamp_last_s,
        "window_age_s": support.window_age_s,
    }
    stages.append(_trace("finite_support", "completed", **support_trace_kwargs))

    candidate = {
        "direction": [float(v) for v in fit.direction],
        "point_on_line": [float(v) for v in fit.point_on_line],
        "segment_endpoints_sensor_m": list(support.segment_sensor) if support.segment_sensor else None,
        "nearest_distance_m": support.nearest_distance_m,
        "unique_packet_count": support.unique_packet_count,
        "unique_frame_count": support.unique_frame_count,
        "span_m": support.span_m,
        "coverage_fraction": support.coverage_fraction,
        "largest_internal_gap_m": support.largest_internal_gap_m,
        "rms_orthogonal_m": support.rms_orthogonal_m,
        "p95_orthogonal_m": support.p95_orthogonal_m,
        "source_timestamp_last_s": support.source_timestamp_last_s,
        "window_age_s": support.window_age_s,
        "source_ray_indices": [int(i) for i in fit.inlier_ray_indices],
        "inlier_source_ids_count": int(fit.inlier_ray_indices.size),
        "support_density_per_m": support.support_density_per_m,
        "max_bin_lateral_spread_m": support.max_bin_lateral_spread_m,
        "sag_amplitude_m": support.sag_amplitude_m,
        "source_ids_sha256": hashlib.sha256(
            fit.inlier_source_ids.tobytes()).hexdigest() \
            if fit.inlier_source_ids is not None else None,
    }
    geometry_class = GEOMETRY_LINE
    status = STATUS_MEASURED_CURRENT
    stages.append(_trace("background_check", "skipped",
                         note="no background pairing available in this run"))
    stages.append(_trace("freshness", "completed",
                         window_age_s=support.window_age_s,
                         window_age_max_s=cfg.validation.max_window_age_s))
    stages.append(_trace("association", "skipped", note="single window run"))
    stages.append(_trace("output", "completed", geometry_class=geometry_class,
                         output_status=status))
    return output(status, geometry_class, None, None, candidate)

"""Machine-readable stage trace and point visibility for the goal pipeline.

This module mirrors the masks used by the detector without owning detection.
Its output is intentionally small JSON plus one uint8 class per source point:
0=rejected, 1=active, 2=candidate support.  The WebUI can therefore retain the
complete cloud while making the algorithm's current view explicit.
"""
from __future__ import annotations

from time import perf_counter

import numpy as np


STAGES = (
    ("raw", "RAW"),
    ("fov", "FOV"),
    ("xy_roi", "XY ROI"),
    ("z_height", "Z / HEIGHT"),
    ("cluster", "CLUSTER"),
    ("goal_structure", "GOAL STRUCTURE"),
    ("candidate_score", "CANDIDATE SCORE"),
    ("bounding_box", "BOUNDING BOX"),
    ("association", "ASSOCIATION"),
    ("tracking", "TRACKING"),
)


def build_observability(points: np.ndarray, cfg: dict, frame_index: int,
                        candidates, focus: dict | None, goal: dict,
                        association: dict | None, source: str,
                        execution_trace: list[dict] | None = None,
                        execution_masks: dict[str, np.ndarray] | None = None) -> dict:
    started = perf_counter()
    cloud = np.asarray(points)
    count = len(cloud) if cloud.ndim == 2 else 0
    xyz = cloud[:, :3] if count and cloud.shape[1] >= 3 else np.empty((0, 3))
    finite = np.isfinite(xyz).all(axis=1) if count else np.zeros(0, dtype=bool)

    masks: dict[str, np.ndarray] = {"raw": finite.copy()}
    ranges = cfg["accumulation"]["range"]
    distance = np.linalg.norm(xyz, axis=1) if count else np.zeros(0)
    fov = finite & (distance >= float(ranges["min"])) & (distance <= float(ranges["max"]))
    if focus and focus.get("active") and focus.get("center"):
        center = np.asarray(focus["center"], dtype=float)
        half = float(focus["half_size"])
        fov &= (np.abs(xyz[:, 0] - center[0]) <= half) \
            & (np.abs(xyz[:, 1] - center[1]) <= half)
    masks["fov"] = fov

    roi_mask = fov.copy()
    roi = cfg["accumulation"].get("roi")
    if roi is not None and count:
        roi_mask &= ((xyz[:, 0] >= roi[0]) & (xyz[:, 0] <= roi[1])
                     & (xyz[:, 1] >= roi[2]) & (xyz[:, 1] <= roi[3])
                     & (xyz[:, 2] >= roi[4]) & (xyz[:, 2] <= roi[5]))
    masks["xy_roi"] = roi_mask
    z_mask = roi_mask & (xyz[:, 2] > float(cfg["goal_detection"]["ground_z"][1]))
    masks["z_height"] = z_mask

    support = np.zeros(count, dtype=bool)
    accepted_support = np.zeros(count, dtype=bool)
    min_conf = float(cfg["goal_detection"]["scoring"]["min_confidence"])
    for candidate in candidates:
        indexes = np.asarray(getattr(candidate, "supporting_point_indices", []), dtype=np.int64)
        indexes = indexes[(indexes >= 0) & (indexes < count)]
        support[indexes] = True
        if float(candidate.confidence) >= min_conf:
            accepted_support[indexes] = True
    masks["cluster"] = z_mask & support
    masks["goal_structure"] = support
    masks["candidate_score"] = accepted_support

    selected = np.zeros(count, dtype=bool)
    if candidates and goal and goal.get("center"):
        goal_center = np.asarray(goal["center"], dtype=float)
        best = min(candidates,
                   key=lambda item: float(np.linalg.norm(np.asarray(item.center) - goal_center)))
        indexes = np.asarray(best.supporting_point_indices, dtype=np.int64)
        indexes = indexes[(indexes >= 0) & (indexes < count)]
        selected[indexes] = True
    masks["bounding_box"] = selected
    associated = selected if association and association.get("accepted") else np.zeros(count, dtype=bool)
    masks["association"] = associated
    tracking = associated if goal and goal.get("detected") else np.zeros(count, dtype=bool)
    masks["tracking"] = tracking

    # Prefer masks captured where the detector actually executed.  The legacy
    # projection above remains a fallback for old callers and stored runs.
    actual = execution_masks or {}
    for trace_name, public_name in {
            "finite": "raw", "range": "fov", "z_height": "z_height",
            "pole_extract": "cluster", "goal_structure": "goal_structure",
            "candidate_score": "candidate_score"}.items():
        if trace_name in actual and len(actual[trace_name]) == count:
            masks[public_name] = np.asarray(actual[trace_name], dtype=bool).copy()

    parameters = {
        "fov": {"range": [ranges["min"], ranges["max"]], "focus": focus},
        "xy_roi": {"roi": roi},
        "z_height": {"minimum_z": cfg["goal_detection"]["ground_z"][1]},
        "candidate_score": {"minimum_confidence": min_conf},
        "association": {"gate_m": cfg["tracking"]["max_center_jump"]},
    }
    reasons = {
        "raw": "non_finite",
        "fov": "outside_sensor_or_tracking_fov",
        "xy_roi": "outside_xy_roi",
        "z_height": "below_height_gate",
        "cluster": "not_in_vertical_structure",
        "goal_structure": "not_candidate_support",
        "candidate_score": "below_detection_score",
        "bounding_box": "not_selected_candidate",
        "association": "association_failed",
        "tracking": "track_not_active",
    }
    if execution_trace is not None:
        labels = {key: label for key, label in STAGES}
        labels.update({
            "finite": "FINITE", "range": "RANGE / SENSOR FOV",
            "pole_extract": "VERTICAL STRUCTURE", "pair_score": "GOAL PAIR",
            "nms": "CANDIDATE NMS", "bev_proposal": "BEV PROPOSAL",
            "z_refine": "Z REFINE", "association": "ASSOCIATION",
            "range_roi_filter": "RANGE / ROI FILTER",
            "voxel_downsample": "VOXEL DOWNSAMPLE",
            "rejected_candidate_diagnostics": "REJECTED CANDIDATES",
            "focus_crop": "SEARCH FOV CROP",
            "region_best_selection": "REGION CANDIDATE SELECTION",
        })
        stages = []
        for raw_stage in execution_trace:
            stage = dict(raw_stage)
            stage.update({"label": labels.get(stage.get("stage"),
                                               str(stage.get("stage", "")).upper()),
                          "frame_id": int(frame_index), "source": source,
                          "trace_kind": "execution"})
            stages.append(stage)
    else:
        stages = []
        previous = np.ones(count, dtype=bool)
        candidate_count = len(candidates)
        for stage_id, label in STAGES:
            mask = masks[stage_id]
            input_points = int(previous.sum())
            output_points = int(mask.sum())
            stages.append({
                "stage": stage_id, "label": label, "frame_id": int(frame_index),
                "source": source, "input_points": input_points,
                "output_points": output_points,
                "removed_points": max(input_points - output_points, 0),
                "latency_ms": None, "parameters": parameters.get(stage_id, {}),
                "candidate_count": candidate_count if stage_id in (
                    "cluster", "goal_structure", "candidate_score", "bounding_box") else 0,
                "reject_reason": None if output_points else reasons[stage_id],
                "trace_kind": "diagnostic_projection",
            })
            previous = mask
        elapsed = round((perf_counter() - started) * 1000.0, 3)
        if stages:
            stages[0]["projection_latency_ms"] = elapsed
    classes = {}
    for key, mask in masks.items():
        values = np.zeros(count, dtype=np.uint8)
        values[mask] = 1
        values[support & mask] = 2
        classes[key] = values
    return {"frame_index": int(frame_index), "source": source,
            "stages": stages, "classes": classes}

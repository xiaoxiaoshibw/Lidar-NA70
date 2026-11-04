"""Configuration loading for the goal demo.  JSON is a YAML subset."""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path


def load_configuration(path=None, overrides: dict | None = None) -> dict:
    """Load goal_detection.yaml and apply a whitelist-validated override dict.

    Overrides are merged recursively into the defaults; unknown keys and type
    mismatches raise ``ValueError`` so a bad request cannot silently change
    detection behaviour.
    """
    source = Path(path) if path else Path(__file__).parent.parent / "goal_detection.yaml"
    cfg = json.loads(source.read_text("utf-8"))
    if not isinstance(cfg, dict):
        raise ValueError("goal_detection 配置必须是对象")
    if overrides:
        _merge(cfg, overrides, parent="")
    _validate(cfg)
    return cfg


def configuration_version(cfg: dict) -> str:
    """Stable hash so a stored run can be tied to the exact thresholds used."""
    body = json.dumps(cfg, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def merge_configuration(base: dict, overrides: dict) -> dict:
    """Return a validated copy of ``base`` with whitelist-safe overrides."""
    cfg = deepcopy(base)
    if not isinstance(overrides, dict):
        raise ValueError("配置覆盖必须是对象")
    _merge(cfg, overrides, parent="")
    _validate(cfg)
    return cfg


def _merge(target: dict, overrides: dict, parent: str) -> None:
    for key, value in overrides.items():
        path = f"{parent}.{key}" if parent else key
        if key not in target:
            raise ValueError(f"未知配置字段: {path}")
        if isinstance(value, dict):
            if not isinstance(target[key], dict):
                raise ValueError(f"{path} 必须是对象")
            _merge(target[key], value, path)
        else:
            if isinstance(target[key], dict):
                raise ValueError(f"{path} 必须是对象")
            if type(target[key]) is not type(value) and not (
                isinstance(target[key], float) and isinstance(value, (int, float))
                and not isinstance(value, bool)
            ):
                raise ValueError(f"{path} 类型不符")
            target[key] = value


def _pair_ok(pair) -> bool:
    return (isinstance(pair, list) and len(pair) == 2
            and all(_number(v) for v in pair) and pair[0] < pair[1])


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value)


def _validate(cfg: dict) -> None:
    acc = cfg["accumulation"]
    if not isinstance(acc, dict) or isinstance(acc.get("enabled"), bool) is False:
        raise ValueError("accumulation 配置非法")
    if int(acc["window_size"]) < 1:
        raise ValueError("accumulation.window_size 至少为 1")
    if not _number(acc["voxel_size"]) or acc["voxel_size"] <= 0:
        raise ValueError("accumulation.voxel_size 必须为正")
    if int(acc["max_points"]) < 1:
        raise ValueError("accumulation.max_points 至少为 1")
    if not _pair_ok([acc["range"]["min"], acc["range"]["max"]]) \
            or acc["range"]["min"] < 0:
        raise ValueError("accumulation.range 必须满足 0 <= min < max")
    roi = acc["roi"]
    if roi is not None:
        if not isinstance(roi, list) or len(roi) != 6 or not all(_number(v) for v in roi):
            raise ValueError("accumulation.roi 必须为 6 个有限数值")
        for axis in range(3):
            if roi[axis * 2] > roi[axis * 2 + 1]:
                raise ValueError("accumulation.roi 区间非法")
    det = cfg["goal_detection"]
    if det["enabled"]:
        if not _number(det["cell_size"]) or det["cell_size"] <= 0:
            raise ValueError("goal_detection.cell_size 必须为正")
        if not _pair_ok(det["ground_z"]):
            raise ValueError("goal_detection.ground_z 区间非法")
        if not _pair_ok(det["frame"]["crossbar_z"]):
            raise ValueError("goal_detection.frame.crossbar_z 区间非法")
        if not _pair_ok(det["pole"]["height"]):
            raise ValueError("goal_detection.pole.height 区间非法")
        if int(det["pole"]["min_points"]) < 1:
            raise ValueError("goal_detection.pole.min_points 至少为 1")
        if not _pair_ok(det["frame"]["width"]):
            raise ValueError("goal_detection.frame.width 区间非法")
        for name in ("max_radius", "height_tol", "base_tol"):
            value = det["pole"][name]
            if not _number(value) or value <= 0:
                raise ValueError(f"goal_detection.pole.{name} 必须为正")
        offsets = det["pole"].get("grid_offsets")
        if not isinstance(offsets, list) or not offsets or len(offsets) > 8 \
                or any(not isinstance(item, list) or len(item) != 2
                       or not all(_number(v) for v in item) for item in offsets):
            raise ValueError("goal_detection.pole.grid_offsets 必须是有限的二维偏移列表")
        if not _number(det["pole"].get("neighbor_radius")) \
                or det["pole"]["neighbor_radius"] <= 0:
            raise ValueError("goal_detection.pole.neighbor_radius 必须为正")
        if not _number(det["pole"].get("neighbor_xy_radius")) \
                or det["pole"]["neighbor_xy_radius"] <= 0:
            raise ValueError("goal_detection.pole.neighbor_xy_radius 必须为正")
        if not isinstance(det["pole"].get("neighbor_min_seed_points"), int) \
                or isinstance(det["pole"]["neighbor_min_seed_points"], bool) \
                or det["pole"]["neighbor_min_seed_points"] < 1:
            raise ValueError("goal_detection.pole.neighbor_min_seed_points 至少为 1")
        if not _number(det["pole"].get("neighbor_max_base_gap")) \
                or det["pole"]["neighbor_max_base_gap"] < 0:
            raise ValueError("goal_detection.pole.neighbor_max_base_gap 不能为负")
        for name in ("min_confidence", "lock_confidence", "crossbar_weight", "geometry_weight"):
            value = det["scoring"][name]
            if not _number(value) or not 0 <= value <= 1:
                raise ValueError(f"goal_detection.scoring.{name} 必须在 [0,1]")
        if not _number(det["scoring"]["nms_distance"]) or det["scoring"]["nms_distance"] <= 0:
            raise ValueError("goal_detection.scoring.nms_distance 必须为正")
        diagnostics = det.get("diagnostics")
        if not isinstance(diagnostics, dict) or not isinstance(diagnostics.get("enabled"), bool):
            raise ValueError("goal_detection.diagnostics.enabled 必须是布尔")
        for name in ("max_rejected_candidates", "max_raw_refs_per_candidate"):
            if not isinstance(diagnostics.get(name), int) or isinstance(diagnostics.get(name), bool) \
                    or diagnostics[name] < 1:
                raise ValueError(f"goal_detection.diagnostics.{name} 至少为 1")
        leveling = det.get("z_leveling")
        if not isinstance(leveling, dict) or not isinstance(leveling.get("enabled"), bool):
            raise ValueError("goal_detection.z_leveling.enabled 必须是布尔")
        if leveling.get("reference_roi") not in ("detection", "full_field"):
            raise ValueError("goal_detection.z_leveling.reference_roi 必须是 detection 或 full_field")
        if not _number(leveling.get("fit_quantile")) \
                or not 0.05 <= leveling["fit_quantile"] <= 0.8:
            raise ValueError("goal_detection.z_leveling.fit_quantile 必须在 [0.05,0.8]")
        if not isinstance(leveling.get("min_points"), int) \
                or isinstance(leveling["min_points"], bool) \
                or leveling["min_points"] < 10:
            raise ValueError("goal_detection.z_leveling.min_points 至少为 10")
        if not _number(leveling.get("min_abs_slope")) \
                or leveling["min_abs_slope"] < 0:
            raise ValueError("goal_detection.z_leveling.min_abs_slope 不能为负")
        for name in ("max_abs_slope", "max_residual", "min_xy_span",
                     "min_xy_minor_std", "max_source_age_seconds"):
            if not _number(leveling.get(name)) or leveling[name] <= 0:
                raise ValueError(f"goal_detection.z_leveling.{name} 必须为正")
        if not _number(leveling.get("max_below_plane_fraction")) \
                or not 0 <= leveling["max_below_plane_fraction"] < 1:
            raise ValueError("z_leveling.max_below_plane_fraction 必须在 [0,1)")
    if int(cfg["locking"]["min_frames"]) < 1:
        raise ValueError("locking.min_frames 至少为 1")
    if int(cfg["locking"]["candidate_gap"]) < 0:
        raise ValueError("locking.candidate_gap 不能为负")
    strategy = cfg.get("strategy")
    if not isinstance(strategy, dict) or strategy.get("mode") not in ("staged", "poles", "facade"):
        raise ValueError("strategy.mode 必须是 staged、poles 或 facade")
    if strategy["mode"] == "facade":
        facade = det.get("facade")
        if not isinstance(facade, dict) or facade.get("enabled") is not True:
            raise ValueError("facade 策略需要 goal_detection.facade.enabled=true")
        if not _pair_ok(facade["z_band"]):
            raise ValueError("goal_detection.facade.z_band 区间非法")
        for name in ("min_points", "edge_bin_min_points", "edge_continuity_bins",
                     "edge_min_points", "top_bin_min_points", "top_min_bins"):
            value = facade[name]
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"goal_detection.facade.{name} 至少为 1")
        for name in ("edge_bin_size", "edge_fit_band", "edge_inlier_band",
                     "edge_min_height", "edge_max_residual", "top_tol",
                     "top_max_residual", "min_height", "max_height",
                     "lateral_max", "facade_trim_sigma"):
            if not _number(facade[name]) or facade[name] <= 0:
                raise ValueError(f"goal_detection.facade.{name} 必须为正")
        if facade["edge_inlier_band"] > facade["edge_fit_band"]:
            raise ValueError("goal_detection.facade.edge_inlier_band 不能大于 edge_fit_band")
        if not _number(facade["edge_min_inlier"]) \
                or not 0 < facade["edge_min_inlier"] <= 1:
            raise ValueError("goal_detection.facade.edge_min_inlier 必须在 (0,1]")
        if not _number(facade["top_margin"]) or facade["top_margin"] < 0:
            raise ValueError("goal_detection.facade.top_margin 不能为负")
        if not _number(facade["top_min_coverage"]) \
                or not 0 < facade["top_min_coverage"] <= 1:
            raise ValueError("goal_detection.facade.top_min_coverage 必须在 (0,1]")
        if not _pair_ok(facade["width_band"]) or facade["width_band"][0] < 0:
            raise ValueError("goal_detection.facade.width_band 区间非法")
        if facade["min_height"] >= facade["max_height"]:
            raise ValueError("goal_detection.facade.min_height 必须小于 max_height")
        for name in ("proposal_cell_size", "proposal_max_span"):
            if not _number(facade[name]) or facade[name] <= 0:
                raise ValueError(f"goal_detection.facade.{name} 必须为正")
        for name in ("proposal_min_points", "proposal_max_regions"):
            value = facade[name]
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"goal_detection.facade.{name} 至少为 1")
    if strategy["mode"] == "staged":
        bev = cfg.get("bev")
        if not isinstance(bev, dict):
            raise ValueError("staged 策略需要 bev 配置段")
        for name in ("cell_size", "min_cell_density", "contrast_ref", "nms_distance"):
            if not _number(bev[name]) or bev[name] <= 0:
                raise ValueError(f"bev.{name} 必须为正")
        if not _number(bev["span_density_floor"]) or bev["span_density_floor"] < 0:
            raise ValueError("bev.span_density_floor 不能为负")
        if not _number(bev["min_contrast"]) or bev["min_contrast"] < 1:
            raise ValueError("bev.min_contrast 必须 ≥ 1")
        for name in ("min_window_frames", "max_blob_cells", "max_regions"):
            if int(bev[name]) < 1:
                raise ValueError(f"bev.{name} 至少为 1")
        if "min_cell_points" in bev and int(bev["min_cell_points"]) < 1:
            raise ValueError("bev.min_cell_points 至少为 1")
        weights = bev.get("score_weights")
        if not isinstance(weights, dict) or not all(
                _number(weights[name]) and 0 <= weights[name] <= 1
                for name in ("density", "contrast", "width")):
            raise ValueError("bev.score_weights 必须为 [0,1] 数值")
        focus = cfg.get("focus")
        if not isinstance(focus, dict) or not _number(focus["half_size"]) \
                or focus["half_size"] <= 0:
            raise ValueError("focus.half_size 必须为正")
        if not isinstance(focus.get("prefer_single_frame"), bool):
            raise ValueError("focus.prefer_single_frame 必须是布尔")
        if not _number(focus["single_min_confidence"]) \
                or not 0 <= focus["single_min_confidence"] <= 1:
            raise ValueError("focus.single_min_confidence 必须在 [0,1]")
        if not isinstance(focus.get("accumulated_min_current_points"), int) \
                or isinstance(focus["accumulated_min_current_points"], bool) \
                or focus["accumulated_min_current_points"] < 1:
            raise ValueError("focus.accumulated_min_current_points 至少为 1")
        zrefine = cfg.get("zrefine")
        if not isinstance(zrefine, dict) or not _number(zrefine["bin_size"]) \
                or zrefine["bin_size"] <= 0 or not _number(zrefine["max_base_shift"]) \
                or zrefine["max_base_shift"] <= 0 or not _number(zrefine["min_height"]) \
                or zrefine["min_height"] <= 0:
            raise ValueError("zrefine.bin_size/max_base_shift/min_height 必须为正")
        if int(zrefine["min_points"]) < 1:
            raise ValueError("zrefine.min_points 至少为 1")
    if int(cfg["tracking"]["lost_frame_threshold"]) < 1:
        raise ValueError("tracking.lost_frame_threshold 至少为 1")
    if int(cfg["tracking"].get("reacquire_min_frames", 0)) < 1:
        raise ValueError("tracking.reacquire_min_frames 至少为 1")
    if int(cfg["tracking"].get("reacquire_timeout", 0)) < 1:
        raise ValueError("tracking.reacquire_timeout 至少为 1")
    if not _number(cfg["tracking"]["max_center_jump"]) \
            or cfg["tracking"]["max_center_jump"] <= 0:
        raise ValueError("tracking.max_center_jump 必须为正")
    for name in ("nominal_dt", "min_dt", "max_dt"):
        if not _number(cfg["tracking"].get(name)) or cfg["tracking"][name] <= 0:
            raise ValueError(f"tracking.{name} 必须为正")
    if not cfg["tracking"]["min_dt"] <= cfg["tracking"]["nominal_dt"] \
            <= cfg["tracking"]["max_dt"]:
        raise ValueError("tracking dt 必须满足 min_dt <= nominal_dt <= max_dt")
    evidence = cfg["tracking"].get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("tracking.evidence 配置非法")
    if not isinstance(evidence.get("recent_max_age_frames"), int) \
            or isinstance(evidence["recent_max_age_frames"], bool) \
            or evidence["recent_max_age_frames"] < 0:
        raise ValueError("tracking.evidence.recent_max_age_frames 不能为负")
    if not _number(evidence.get("recent_max_age_seconds")) \
            or evidence["recent_max_age_seconds"] < 0:
        raise ValueError("tracking.evidence.recent_max_age_seconds 不能为负")
    if not isinstance(evidence.get("signature_history_size"), int) \
            or isinstance(evidence["signature_history_size"], bool) \
            or evidence["signature_history_size"] < 1:
        raise ValueError("tracking.evidence.signature_history_size 至少为 1")
    association = cfg["tracking"].get("association")
    if not isinstance(association, dict) \
            or not _number(association.get("min_score")) \
            or not 0 <= association["min_score"] <= 1 \
            or not _number(association.get("bearing_scale_rad")) \
            or association["bearing_scale_rad"] <= 0:
        raise ValueError("tracking.association 配置非法")
    weights = association.get("weights")
    required_weights = ("center", "bearing", "size", "yaw", "detection", "history")
    if not isinstance(weights, dict) or not all(
            _number(weights.get(name)) and weights[name] >= 0 for name in required_weights) \
            or not math.isclose(sum(weights[name] for name in required_weights), 1.0,
                                rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("tracking.association.weights 必须非负且和为 1")
    webui = cfg["webui"]
    if not isinstance(webui.get("capture_name_filter"), list) \
            or not all(isinstance(v, str) for v in webui["capture_name_filter"]):
        raise ValueError("webui.capture_name_filter 必须为字符串列表")
    if webui.get("demo_roi") is not None:
        demo_roi = webui["demo_roi"]
        if not isinstance(demo_roi, list) or len(demo_roi) != 6 or not all(_number(v) for v in demo_roi):
            raise ValueError("webui.demo_roi 必须为 6 个有限数值")
        for axis in range(3):
            if demo_roi[axis * 2] > demo_roi[axis * 2 + 1]:
                raise ValueError("webui.demo_roi 区间非法")
    for key in ("window_choices", "speed_choices"):
        choices = webui.get(key)
        if not isinstance(choices, list) or not choices \
                or not all(_number(v) and v > 0 for v in choices):
            raise ValueError(f"webui.{key} 必须为正数列表")
    analysis = cfg.get("annotate_analysis")
    if not isinstance(analysis, dict):
        raise ValueError("annotate_analysis 配置非法")
    if not _number(analysis.get("brush_radius")) or not 0.1 <= analysis["brush_radius"] <= 5:
        raise ValueError("annotate_analysis.brush_radius 必须在 [0.1,5]")
    for name in ("min_region_points", "min_post_points"):
        if not isinstance(analysis.get(name), int) or isinstance(analysis[name], bool) \
                or analysis[name] < 1:
            raise ValueError(f"annotate_analysis.{name} 至少为 1")
    if not _number(analysis.get("trim_percentile")) or not 0 <= analysis["trim_percentile"] < 25:
        raise ValueError("annotate_analysis.trim_percentile 必须在 [0,25)")
    if not _number(analysis.get("end_band_fraction")) \
            or not 0.05 <= analysis["end_band_fraction"] <= 0.5:
        raise ValueError("annotate_analysis.end_band_fraction 必须在 [0.05,0.5]")
    for name in ("post_bin_size", "post_column_radius", "min_width",
                 "min_thickness", "max_thickness", "multi_split_width_threshold",
                 "cluster_cell_size"):
        if not _number(analysis.get(name)) or analysis[name] <= 0:
            raise ValueError(f"annotate_analysis.{name} 必须为正")
    for name in ("cluster_link_cells", "max_clusters"):
        if not isinstance(analysis.get(name), int) or isinstance(analysis[name], bool) \
                or analysis[name] < 1:
            raise ValueError(f"annotate_analysis.{name} 至少为 1")
    if not isinstance(analysis.get("min_cluster_points"), int) \
            or isinstance(analysis["min_cluster_points"], bool) \
            or analysis["min_cluster_points"] < 1:
        raise ValueError("annotate_analysis.min_cluster_points 至少为 1")
    if not isinstance(analysis.get("cluster_min_cell_points"), int) \
            or isinstance(analysis["cluster_min_cell_points"], bool) \
            or analysis["cluster_min_cell_points"] < 1:
        raise ValueError("annotate_analysis.cluster_min_cell_points 至少为 1")
    if analysis["min_thickness"] > analysis["max_thickness"]:
        raise ValueError("annotate_analysis.min_thickness 不能大于 max_thickness")
    for name in ("base_z_quantile", "top_z_quantile"):
        if not _number(analysis.get(name)) or not 0 < analysis[name] < 1:
            raise ValueError(f"annotate_analysis.{name} 必须在 (0,1)")

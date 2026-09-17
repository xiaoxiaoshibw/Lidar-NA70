"""Configuration loading for the goal demo.  JSON is a YAML subset."""
from __future__ import annotations

import hashlib
import json
import math
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
        if not _number(det["pole"]["max_radius"]) or det["pole"]["max_radius"] <= 0:
            raise ValueError("goal_detection.pole.max_radius 必须为正")
        for name in ("min_confidence", "lock_confidence"):
            value = det["scoring"][name]
            if not _number(value) or not 0 <= value <= 1:
                raise ValueError(f"goal_detection.scoring.{name} 必须在 [0,1]")
    if int(cfg["locking"]["min_frames"]) < 1:
        raise ValueError("locking.min_frames 至少为 1")
    if int(cfg["tracking"]["lost_frame_threshold"]) < 1:
        raise ValueError("tracking.lost_frame_threshold 至少为 1")
    if not _number(cfg["tracking"]["max_center_jump"]) \
            or cfg["tracking"]["max_center_jump"] <= 0:
        raise ValueError("tracking.max_center_jump 必须为正")

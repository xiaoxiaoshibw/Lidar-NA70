"""Deterministic SYNTHETIC goal scenes with independent frame truth.

The generator approximates sparse NA70 observations for development tests.  It
is not a physical sensor simulator.  Truth is returned beside each frame and is
never passed into the detector or tracker.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


SCENARIOS = (
    "static", "translation", "yaw", "translation_yaw", "pitch_roll",
    "density_drop", "missing_crossbar", "single_post_occlusion",
    "disappear_reenter", "tree_wall_fence", "similar_pair", "multi_candidate",
    "fov_edge", "empty_nan", "time_jump",
)


@dataclass(frozen=True)
class SyntheticTruth:
    frame_index: int
    device_time: float
    target_id: str
    visible: bool
    partially_occluded: bool
    center: list[float] | None
    size: list[float]
    yaw: float | None
    sensor_translation: list[float]
    sensor_yaw: float
    sensor_pitch: float
    sensor_roll: float

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass(frozen=True)
class SyntheticFrame:
    points: np.ndarray
    truth: SyntheticTruth


def generate_sequence(scenario: str, seed: int, frames: int = 200,
                      fps: float = 10.0) -> list[SyntheticFrame]:
    if scenario not in SCENARIOS:
        raise ValueError(f"未知 SYNTHETIC 场景: {scenario}")
    if frames < 1 or fps <= 0:
        raise ValueError("frames 和 fps 必须为正")
    rng = np.random.default_rng(int(seed))
    goal_center = np.array([75.0, 0.0, 1.22])
    goal_size = [0.6, 5.4, 2.44]
    output = []
    for index in range(frames):
        pose = _sensor_pose(scenario, index, frames)
        visible, partial = _visibility(scenario, index, frames)
        density = _density(scenario, index, frames)
        world_parts = []
        if visible:
            world_parts.append(_goal_points(
                rng, goal_center, width=goal_size[1], height=goal_size[2],
                density=density, crossbar=scenario != "missing_crossbar",
                hide_right=partial))
        world_parts.append(_ground_points(rng, 260))
        if scenario in ("tree_wall_fence", "similar_pair", "multi_candidate"):
            world_parts.extend(_distractors(rng, scenario))
        world = np.vstack([part for part in world_parts if len(part)])
        sensor_xyz = _world_to_sensor(world, pose)
        sensor_xyz += rng.normal(0.0, 0.025, sensor_xyz.shape)
        intensity = rng.uniform(60.0, 180.0, len(sensor_xyz))
        points = np.column_stack((sensor_xyz, intensity)).astype(np.float32)
        if scenario == "empty_nan":
            phase = index % 30
            if phase < 4:
                points = np.empty((0, 4), dtype=np.float32)
            elif phase < 8 and len(points):
                points[:min(8, len(points)), :3] = np.nan
        center_sensor = (_world_to_sensor(goal_center[None, :], pose)[0]
                         if visible else None)
        device_time = index / fps
        if scenario == "time_jump" and index >= frames // 2:
            device_time -= 0.4
        truth = SyntheticTruth(
            frame_index=index, device_time=float(device_time), target_id="goal-1",
            visible=visible, partially_occluded=partial,
            center=None if center_sensor is None else [float(v) for v in center_sensor],
            size=list(goal_size), yaw=(None if not visible else float(-pose[3])),
            sensor_translation=[float(v) for v in pose[:3]],
            sensor_yaw=float(pose[3]), sensor_pitch=float(pose[4]),
            sensor_roll=float(pose[5]))
        output.append(SyntheticFrame(points=points, truth=truth))
    return output


def scenario_manifest() -> dict:
    return {
        "label": "SYNTHETIC",
        "generator": "goal-synthetic-v1",
        "scenarios": list(SCENARIOS),
        "development_seeds": [0, 19],
        "validation_seeds": [1000, 1049],
        "default_frames": 200,
        "truth_not_provided_to_pipeline": True,
        "limitations": [
            "sparse geometric approximation, not a physical NA70 simulator",
            "no rolling scan distortion model",
            "intensity is randomized rather than material-calibrated",
        ],
    }


def _sensor_pose(scenario: str, index: int, frames: int) -> np.ndarray:
    progress = index / max(frames - 1, 1)
    translation = np.zeros(3)
    yaw = pitch = roll = 0.0
    if scenario in ("translation", "translation_yaw"):
        translation[:2] = [4.0 * progress, 1.5 * np.sin(progress * np.pi)]
    if scenario in ("yaw", "translation_yaw", "fov_edge"):
        yaw = np.deg2rad((10.0 if scenario != "fov_edge" else 24.0) * progress)
    if scenario == "pitch_roll":
        pitch = np.deg2rad(2.0 * np.sin(progress * 2.0 * np.pi))
        roll = np.deg2rad(1.5 * np.cos(progress * 2.0 * np.pi))
    return np.array([*translation, yaw, pitch, roll], dtype=np.float64)


def _visibility(scenario: str, index: int, frames: int) -> tuple[bool, bool]:
    if scenario == "disappear_reenter":
        return not (frames * 0.35 <= index < frames * 0.6), False
    if scenario == "single_post_occlusion":
        return True, frames * 0.3 <= index < frames * 0.7
    return True, False


def _density(scenario: str, index: int, frames: int) -> float:
    if scenario == "density_drop":
        return 0.2 + 0.8 * abs(np.cos(index / max(frames - 1, 1) * np.pi))
    return 1.0


def _goal_points(rng, center, width: float, height: float, density: float,
                 crossbar: bool, hide_right: bool) -> np.ndarray:
    count = max(4, int(20 * density))
    parts = []
    for side in (-1.0, 1.0):
        if hide_right and side > 0:
            continue
        z = np.linspace(center[2] - height / 2, center[2] + height / 2, count)
        xy = rng.normal([center[0], center[1] + side * width / 2], 0.035,
                        size=(count, 2))
        parts.append(np.column_stack((xy, z)))
    if crossbar:
        bar_count = max(5, int(28 * density))
        y = np.linspace(center[1] - width / 2, center[1] + width / 2, bar_count)
        parts.append(np.column_stack((rng.normal(center[0], 0.035, bar_count), y,
                                      np.full(bar_count, center[2] + height / 2))))
    return np.vstack(parts) if parts else np.empty((0, 3))


def _ground_points(rng, count: int) -> np.ndarray:
    return np.column_stack((rng.uniform(50, 100, count), rng.uniform(-22, 22, count),
                            rng.normal(0.0, 0.025, count)))


def netted_goal_points(rng, center, width: float, height: float, density: float = 1.0,
                       net_sag: float = 0.35) -> np.ndarray:
    """SYNTHETIC goal whose mouth is filled by a net (real-data-like).

    ``_goal_points`` above models posts + optional crossbar only.  Real NA70
    returns from a training goal are dominated by the net filling the opening,
    which is what merges both posts into one wide component for connectivity
    based extractors.  Posts here are smeared (sigma 0.15 m) like distant real
    returns instead of the tight sigma 0.035 m columns of ``_goal_points``.
    """
    count = max(6, int(20 * density))
    parts = []
    for side in (-1.0, 1.0):
        z = np.linspace(center[2] - height / 2, center[2] + height / 2, count)
        xy = rng.normal([center[0], center[1] + side * width / 2], 0.15,
                        size=(count, 2))
        parts.append(np.column_stack((xy, z)))
    bar_count = max(8, int(30 * density))
    y = np.linspace(center[1] - width / 2, center[1] + width / 2, bar_count)
    parts.append(np.column_stack((rng.normal(center[0], 0.15, bar_count), y,
                                  np.full(bar_count, center[2] + height / 2))))
    net_count = max(20, int(160 * density))
    net_y = rng.uniform(center[1] - width / 2, center[1] + width / 2, net_count)
    net_z = rng.uniform(center[2] - height / 2 + 0.05, center[2] + height / 2, net_count)
    net_x = rng.normal(center[0], net_sag, net_count)
    parts.append(np.column_stack((net_x, net_y, net_z)))
    return np.vstack(parts)


def _distractors(rng, scenario: str) -> list[np.ndarray]:
    z = np.linspace(0, 9, 100)
    tree = np.column_stack((rng.normal(68, 1.3, len(z)),
                            rng.normal(11, 1.3, len(z)), z))
    wall_y = np.repeat(np.linspace(-18, -8, 20), 9)
    wall = np.column_stack((np.full(len(wall_y), 84.0), wall_y,
                            np.tile(np.linspace(0, 7, 9), 20)))
    parts = [tree, wall]
    if scenario in ("similar_pair", "multi_candidate"):
        # Goal-like negative structure with wrong width or height.
        parts.append(_goal_points(rng, np.array([68.0, -12.0, 1.8]), 7.4, 3.6,
                                  0.8, crossbar=False, hide_right=False))
    return parts


def _world_to_sensor(points: np.ndarray, pose: np.ndarray) -> np.ndarray:
    translation = pose[:3]
    yaw, pitch, roll = pose[3:]
    cy, sy = np.cos(yaw), np.sin(yaw)
    cp, sp = np.cos(pitch), np.sin(pitch)
    cr, sr = np.cos(roll), np.sin(roll)
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    world_from_sensor = rz @ ry @ rx
    return (np.asarray(points, dtype=np.float64) - translation) @ world_from_sensor


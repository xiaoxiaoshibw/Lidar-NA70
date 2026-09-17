"""Accumulator: sliding-window merge, range/ROI gating, voxel downsample."""
import numpy as np
import pytest

from backend.goal_detection.accumulation import Accumulator
from backend.goal_detection.buffer import StampedCloud
from backend.goal_detection.config import configuration_version, load_configuration


def base_cfg(**overrides):
    cfg = load_configuration()["accumulation"]
    cfg.update(overrides)
    return cfg


def cloud(index, xyz, time=None):
    xyz = np.asarray(xyz, dtype=np.float32)
    points = np.column_stack((xyz, np.full(len(xyz), 100.0, dtype=np.float32)))
    return StampedCloud(points, frame_index=index, device_time=time)


def test_default_configuration_loads_and_hashes():
    cfg = load_configuration()
    assert cfg["accumulation"]["window_size"] == 10
    version = configuration_version(cfg)
    assert len(version) == 16 and version == configuration_version(load_configuration())


def test_unknown_override_rejected():
    with pytest.raises(ValueError):
        load_configuration(overrides={"not_a_field": 1})
    with pytest.raises(ValueError):
        load_configuration(overrides={"accumulation": {"bogus": 1}})
    with pytest.raises(ValueError):
        load_configuration(overrides={"accumulation": {"window_size": "ten"}})


def test_invalid_values_rejected():
    with pytest.raises(ValueError):
        load_configuration(overrides={"accumulation": {"window_size": 0}})
    with pytest.raises(ValueError):
        load_configuration(overrides={"accumulation": {"voxel_size": -1}})
    with pytest.raises(ValueError):
        load_configuration(overrides={"accumulation": {"range": {"min": 5.0, "max": 3.0}}})


def test_window_accumulates_up_to_size():
    acc = Accumulator(base_cfg())
    for i in range(4):
        stats = acc.push(cloud(i, [[100 + i, 0, 2]]))
        assert stats["accumulated_frames"] == i + 1
        assert stats["accumulated_points"] == i + 1


def test_window_size_slides():
    acc = Accumulator(base_cfg(window_size=3))
    for i in range(5):
        acc.push(cloud(i, [[100 + i, 0, 2]]))
    xyz, _ = acc.accumulated()
    assert len(xyz) == 3
    assert np.allclose(sorted(xyz[:, 0]), [102, 103, 104], atol=0.6)


def test_voxel_merge_collapses_coincident_points():
    acc = Accumulator(base_cfg(window_size=10, voxel_size=0.5))
    for i in range(10):
        acc.push(cloud(i, [[100.01, 0.01, 2.0]]))
    xyz, intensity = acc.accumulated()
    assert len(xyz) == 1
    assert intensity[0] == pytest.approx(100.0)


def test_range_gate_drops_near_and_far():
    acc = Accumulator(base_cfg(range={"min": 50.0, "max": 130.0}))
    acc.push(cloud(0, [[10, 0, 0], [100, 0, 0], [500, 0, 0]]))
    xyz, _ = acc.accumulated()
    assert len(xyz) == 1
    assert xyz[0, 0] == pytest.approx(100.0, abs=0.6)


def test_nan_and_inf_points_dropped():
    acc = Accumulator(base_cfg(range={"min": 0.0, "max": 130.0}))
    xyz = [[100, 0, 2], [np.nan, 0, 2], [np.inf, 0, 2], [90, 0, np.nan]]
    acc.push(cloud(0, xyz))
    out, _ = acc.accumulated()
    assert len(out) == 1


def test_empty_window_returns_empty():
    acc = Accumulator(base_cfg())
    xyz, intensity = acc.accumulated()
    assert len(xyz) == 0 and len(intensity) == 0


def test_empty_frame_is_tolerated():
    acc = Accumulator(base_cfg())
    stats = acc.push(StampedCloud(np.empty((0, 4), dtype=np.float32), frame_index=0))
    assert stats["accumulated_points"] == 0
    acc.push(cloud(1, [[100, 0, 2]]))
    xyz, _ = acc.accumulated()
    assert len(xyz) == 1


def test_roi_clip():
    acc = Accumulator(base_cfg(roi=[95.0, 105.0, -5.0, 5.0, -5.0, 5.0]))
    acc.push(cloud(0, [[100, 0, 2], [140, 0, 2]]))
    xyz, _ = acc.accumulated()
    assert len(xyz) == 1


def test_max_points_cap():
    acc = Accumulator(base_cfg(window_size=1, voxel_size=0.1, max_points=5))
    grid = [[10 * a + b, 0, 0] for a in range(10) for b in range(10)]
    acc.push(cloud(0, grid))
    xyz, _ = acc.accumulated()
    assert len(xyz) == 5


def test_disabled_accumulator_rejects_push():
    acc = Accumulator(base_cfg(enabled=False))
    with pytest.raises(ValueError):
        acc.push(cloud(0, [[1, 2, 3]]))


def test_compensator_hook_receives_and_returns_cloud():
    seen = []

    def compensate(cloud):
        seen.append(cloud.frame_index)
        shifted = cloud.points.copy()
        shifted[:, 0] += 1.0
        return StampedCloud(shifted, cloud.frame_index, cloud.device_time)

    acc = Accumulator(base_cfg(), compensator=compensate)
    acc.push(cloud(0, [[100, 0, 2]]))
    assert seen == [0]
    xyz, _ = acc.accumulated()
    assert xyz[0, 0] == pytest.approx(101.0, abs=0.6)

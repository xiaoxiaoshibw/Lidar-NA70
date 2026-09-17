"""SYNTHETIC algorithm tests for LiDAR-only odometry.

Everything here is generated data.  Passing these tests proves the maths, the
state machine and the rejection gates are correct; it proves nothing about a
real moving NA70 capture, and no test in this file may be quoted as
"true-data no-ghosting acceptance".
"""
import json
from pathlib import Path

import numpy as np
import pytest

from backend.mapping import (
    FRAME_STATES, VoxelMap, clean, configuration, configuration_version,
    downsample, estimate_normals, invert, is_planar, preprocess, register,
    rotation_deg, run_odometry, translation_m,
)


def synthetic_config(**overrides):
    cfg = configuration()
    cfg.update(dict(voxel_size=0.1, max_points=15000, max_iterations=20, min_points=40,
                    submap_voxel_size=0.1, submap_max_points=30000, keyframe_max_points=8000,
                    min_range=0.5, max_range=20.0, lost_after_failures=2))
    cfg.update(overrides)
    return cfg


def room(rng, size=(9.0, 7.0, 3.0), per_face=3000):
    """A room with floor, ceiling and four walls: full six-DoF observability."""
    sx, sy, sz = size
    faces = [
        np.column_stack((rng.uniform(0, sx, per_face), rng.uniform(0, sy, per_face), np.zeros(per_face))),
        np.column_stack((rng.uniform(0, sx, per_face), rng.uniform(0, sy, per_face), np.full(per_face, sz))),
        np.column_stack((np.zeros(per_face), rng.uniform(0, sy, per_face), rng.uniform(0, sz, per_face))),
        np.column_stack((np.full(per_face, sx), rng.uniform(0, sy, per_face), rng.uniform(0, sz, per_face))),
        np.column_stack((rng.uniform(0, sx, per_face), np.zeros(per_face), rng.uniform(0, sz, per_face))),
        np.column_stack((rng.uniform(0, sx, per_face), np.full(per_face, sy), rng.uniform(0, sz, per_face))),
    ]
    return np.vstack(faces)


def observe(world, pose, cfg, rng=None, noise=0.0):
    """Sensor-frame cloud for a given world<->sensor pose, plus intensity column."""
    local = (world - pose[:3, 3]) @ pose[:3, :3]
    distance = np.linalg.norm(local, axis=1)
    keep = (distance >= cfg["min_range"]) & (distance <= cfg["max_range"])
    points = local[keep]
    if noise and rng is not None:
        points = points + rng.normal(0, noise, points.shape)
    return np.column_stack((points, np.full(len(points), 120.0)))


def pose_from(translation, degrees_z=0.0):
    angle = np.radians(degrees_z)
    pose = np.eye(4)
    pose[:3, :3] = np.array([[np.cos(angle), -np.sin(angle), 0],
                             [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
    pose[:3, 3] = translation
    return pose


def drive(poses, world, cfg, rng, noise=0.0):
    return [observe(world, pose, cfg, rng, noise) for pose in poses]


def load(clouds):
    return lambda index: clouds[index]


# ------------------------------------------------------------------ 1/2 pose recovery

def test_synthetic_rigid_transform_is_recovered():
    rng = np.random.default_rng(11)
    target = room(rng, per_face=1500)
    cfg = synthetic_config(min_points=60)
    angle = np.radians(2.5)
    truth = np.eye(4)
    truth[:3, :3] = np.array([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
    truth[:3, 3] = [0.12, -0.08, 0.05]
    source = (target - truth[:3, 3]) @ truth[:3, :3]
    for method in ("point_to_point", "point_to_plane"):
        pose, quality = register(source, target, np.eye(4), synthetic_config(method=method))
        assert pose is not None, quality
        np.testing.assert_allclose(pose[:3, 3], truth[:3, 3], atol=2e-3)
        np.testing.assert_allclose(pose[:3, :3], truth[:3, :3], atol=1e-3)
        assert quality["overlap"] > 0.95 and quality["rmse"] < 0.02


def test_translation_and_rotation_error_stay_bounded_over_a_trajectory():
    rng = np.random.default_rng(3)
    world = room(rng)
    cfg = synthetic_config()
    truth = [pose_from([step * 0.12, step * 0.05, 0.0], step * 1.2) for step in range(9)]
    clouds = drive(truth, world, cfg, rng)
    report = run_odometry(load(clouds), list(range(9)), cfg)
    assert report["metrics"]["accepted_ratio"] >= 0.9
    for record in report["frames"]:
        if record["pose"] is None:
            continue
        error = invert(truth[record["index"]]) @ np.asarray(record["pose"])
        assert translation_m(error) < 0.12
        assert rotation_deg(error) < 1.5


# ------------------------------------------------------------------ 3/4/5/6/7 gates

def test_insufficient_points_short_circuits():
    cfg = synthetic_config()
    tiny = np.zeros((5, 4))
    pose, quality = register(tiny, room(np.random.default_rng(1), per_face=200), np.eye(4), cfg)
    assert pose is None and quality["state"] == "insufficient_points"
    assert preprocess(np.empty((0, 4)), cfg)[0].shape == (0, 3)


def test_nan_inf_zero_and_range_are_filtered():
    cfg = synthetic_config()
    points = np.array([[np.nan, 1, 2, 5], [np.inf, 0, 0, 5], [0, 0, 0, 5],
                       [3, 0, 0, 9], [100, 0, 0, 9], [0.1, 0, 0, 9]])
    xyz, intensity = clean(points, cfg)
    np.testing.assert_array_equal(xyz, [[3, 0, 0]])
    np.testing.assert_array_equal(intensity, [9])
    assert clean(np.array([[1, 2]]), cfg)[0].shape == (0, 3)
    # A cell keeps the mean intensity of everything that fell into it.
    points = np.array([[0.05, 0.05, 0.05, 10], [0.15, 0.15, 0.15, 20]])
    xyz, intensity = downsample(points[:, :3], points[:, 3], 1.0, 0)
    assert len(xyz) == 1 and intensity[0] == pytest.approx(15.0)


def test_planar_and_degenerate_geometry_is_rejected():
    cfg = synthetic_config()
    plane = np.column_stack((np.random.default_rng(5).uniform(-2, 2, 400),
                             np.random.default_rng(6).uniform(-2, 2, 400), np.zeros(400)))
    assert is_planar(plane, cfg)
    assert register(plane, plane, np.eye(4), cfg)[1]["state"] == "degenerate_geometry"


def test_low_overlap_is_rejected():
    """Only a small slice of the source can be matched, so overlap must fail."""
    cfg = synthetic_config()
    rng = np.random.default_rng(8)
    target = room(rng, per_face=1200)
    slice_of_target = np.vstack([target[face * 1200:face * 1200 + 100] for face in range(6)])
    far = target[600:] + np.array([40.0, 0.0, 0.0])
    source = np.vstack((slice_of_target, far[:5400]))
    pose, quality = register(source, target, np.eye(4), cfg)
    assert pose is None
    assert quality["state"] == "quality_failed" and quality["reason"] == "overlap_low"
    assert quality["overlap"] < cfg["min_overlap"]


def test_high_residual_is_rejected():
    cfg = synthetic_config(max_rmse=0.05)
    rng = np.random.default_rng(9)
    target = room(rng, per_face=1500)
    noisy = target + rng.normal(0, 0.25, target.shape)
    pose, quality = register(noisy, target, np.eye(4), cfg)
    assert pose is None and quality["state"] == "quality_failed"
    assert quality["reason"] == "rmse_high"


def test_motion_jump_is_rejected():
    cfg = synthetic_config(max_translation_step=0.05, max_rotation_step=1.0)
    rng = np.random.default_rng(10)
    target = room(rng, per_face=1200)
    source = (target - np.array([0.6, 0.0, 0.0])) @ np.eye(3)
    pose, quality = register(source, target, np.eye(4), cfg)
    assert pose is None and quality["state"] == "quality_failed"
    assert quality["reason"] in ("translation_jump", "overlap_low", "rmse_high")


# ------------------------------------------------------------------ 8/9/10 state machine

def test_timestamp_regression_is_flagged_and_not_fused():
    cfg = synthetic_config()
    rng = np.random.default_rng(21)
    world = room(rng)
    clouds = drive([np.eye(4)] * 3, world, cfg, rng)
    report = run_odometry(load(clouds), [0.0, 0.1, 0.05], cfg)
    assert report["frames"][2]["state"] == "timestamp_invalid"
    assert report["frames"][2]["pose"] is None
    assert report["map"].fused_points == report["frames"][0]["point_count"] + report["frames"][1]["point_count"]


def test_none_and_nan_timestamps_are_invalid():
    cfg = synthetic_config()
    rng = np.random.default_rng(22)
    clouds = drive([np.eye(4)] * 3, room(rng), cfg, rng)
    report = run_odometry(load(clouds), [0.0, float("nan"), 0.2], cfg)
    assert report["frames"][1]["state"] == "timestamp_invalid"


def test_tracking_loss_stops_fusion():
    cfg = synthetic_config()
    rng = np.random.default_rng(12)
    world = room(rng)
    good = observe(world, np.eye(4), cfg, rng)
    noise = lambda: np.column_stack((rng.uniform(-8, 8, (3000, 3)), np.full(3000, 90.0)))
    clouds = [good, good, noise(), noise(), noise()]
    report = run_odometry(load(clouds), list(range(5)), cfg)
    states = [record["state"] for record in report["frames"]]
    assert states[2] == "quality_failed"
    assert states[3] == "tracking_lost"
    assert states[4] == "tracking_lost"
    # Only the located frames may reach the map.
    assert report["frames"][3]["pose"] is None and report["frames"][4]["pose"] is None
    assert report["map"].fused_points <= 2 * len(good)


def test_reinitialisation_opens_a_new_segment():
    cfg = synthetic_config()
    rng = np.random.default_rng(13)
    world = room(rng)
    good = observe(world, np.eye(4), cfg, rng)
    noise = lambda: np.column_stack((rng.uniform(-8, 8, (3000, 3)), np.full(3000, 90.0)))
    clouds = [good, good, noise(), noise(), good]
    report = run_odometry(load(clouds), list(range(5)), cfg)
    states = [record["state"] for record in report["frames"]]
    assert states[4] == "reinitialized"
    assert report["frames"][4]["segment_id"] == 1
    assert [segment["segment_id"] for segment in report["trajectory"]["segments"]] == [0, 1]
    # The two segments must not be silently welded into one trajectory.
    assert report["trajectory"]["segments"][0]["end_frame_index"] <= 1


def test_insufficient_frame_is_reported_as_insufficient_points():
    cfg = synthetic_config()
    rng = np.random.default_rng(14)
    world = room(rng)
    good = observe(world, np.eye(4), cfg, rng)
    clouds = [good, good, np.zeros((4, 4))]
    report = run_odometry(load(clouds), [0.0, 0.1, 0.2], cfg)
    assert report["frames"][2]["state"] == "insufficient_points"


# ------------------------------------------------------------------ 11 keyframes

def test_keyframes_track_large_motion_only():
    cfg = synthetic_config(keyframe_translation=0.25, keyframe_rotation=2.0, keyframe_time=0)
    rng = np.random.default_rng(15)
    world = room(rng)
    poses = [pose_from([index * 0.1, 0.0, 0.0]) for index in range(12)]
    report = run_odometry(load(drive(poses, world, cfg, rng)), list(range(12)), cfg)
    frames = [record["frame_index"] for record in report["keyframes"]]
    assert frames[0] == 0
    assert len(frames) >= 4
    for earlier, later in zip(frames, frames[1:]):
        assert translation_m(invert(np.asarray(report["frames"][later]["pose"]))
                             @ np.asarray(report["frames"][earlier]["pose"])) >= 0.2


# ------------------------------------------------------------------ 12/13 map

def test_voxel_map_fusion_averages_and_counts_observations():
    voxel_map = VoxelMap(0.5, max_voxels=10, chunk_voxels=4)
    points = np.array([[0.1, 0.1, 0.1], [0.2, 0.2, 0.2], [3.0, 3.0, 3.0]])
    intensity = np.array([10.0, 20.0, 30.0])
    voxel_map.add(points, intensity, 1.0, 0, 0)
    voxel_map.add(points, intensity, 2.0, 1, 0)
    assert voxel_map.voxel_count() == 2
    assert voxel_map.fused_points == 6
    rows = voxel_map.chunked()
    assert sum(len(value) for value in rows.values()) == 2
    first = rows[(0, 0, 0)][0]
    assert first[4] == 4                                   # four observations
    assert first[3] == pytest.approx(15.0)                 # mean intensity
    assert first[5] == pytest.approx(2.0)                  # most recent time
    assert first[6] == 1                                   # most recent keyframe
    assert voxel_map.bounds()["maximum"] == [3.0, 3.0, 3.0]
    assert voxel_map.segment_summary()[0]["voxels"] == 2


def test_voxel_map_cap_drops_new_voxels_only():
    voxel_map = VoxelMap(0.5, max_voxels=1, chunk_voxels=4)
    voxel_map.add(np.array([[0.1, 0.1, 0.1]]), np.array([5.0]), 0.0, 0, 0)
    voxel_map.add(np.array([[9.0, 9.0, 9.0]]), np.array([5.0]), 0.0, 0, 0)
    assert voxel_map.voxel_count() == 1 and voxel_map.dropped_points == 1


def test_map_exports_have_parseable_headers(tmp_path):
    voxel_map = VoxelMap(0.4, chunk_voxels=8)
    rng = np.random.default_rng(16)
    voxel_map.add(rng.uniform(-4, 4, (5000, 3)), rng.uniform(0, 255, 5000), 3.5, 7, 0)
    ply = tmp_path / "map.ply"
    written = voxel_map.export_ply(ply)
    assert written == voxel_map.voxel_count()
    header = ply.read_bytes().split(b"end_header\n")[0].decode()
    assert f"element vertex {written}" in header and "binary_little_endian" in header
    body = len(ply.read_bytes()) - len(header.encode()) - len(b"end_header\n")
    assert body == written * (12 + 1 + 8)

    pcd = tmp_path / "map.pcd"
    assert voxel_map.export_pcd(pcd) == written
    text = pcd.read_bytes()
    head = text.split(b"DATA binary\n")[0].decode()
    assert f"POINTS {written}" in head and "observations" in head
    assert len(text) - len(head.encode()) - len(b"DATA binary\n") == written * 28


# ------------------------------------------------------------------ 18 closure

def test_closure_error_is_computed_for_a_return_to_start():
    cfg = synthetic_config(keyframe_translation=0.2, keyframe_time=0)
    rng = np.random.default_rng(17)
    world = room(rng)
    path = [[0.0, 0.0, 0.0], [0.2, 0.0, 0.0], [0.4, 0.1, 0.0], [0.2, 0.15, 0.0], [0.0, 0.0, 0.0]]
    poses = [pose_from(point) for point in path]
    report = run_odometry(load(drive(poses, world, cfg, rng)), list(range(len(poses))), cfg)
    closure = report["trajectory"]["segments"][0]["closure"]
    assert closure is not None and closure["translation_m"] < 0.1
    assert closure["first_frame_index"] == 0 and closure["last_frame_index"] == len(poses) - 1
    assert report["metrics"]["closure_translation_m"] == closure["translation_m"]


def test_single_frame_reports_no_closure():
    cfg = synthetic_config()
    rng = np.random.default_rng(18)
    clouds = drive([np.eye(4)], room(rng), cfg, rng)
    report = run_odometry(load(clouds), [0.0], cfg)
    assert report["metrics"]["frame_count"] == 1
    assert report["frames"][0]["state"] == "reference"
    assert report["metrics"]["closure_translation_m"] is None


# ------------------------------------------------------------------ config hygiene

def test_configuration_is_central_and_versioned(tmp_path):
    cfg = configuration()
    for key in ("voxel_size", "max_points", "max_range", "min_points", "correspondence_distance",
                "max_iterations", "min_overlap", "max_rmse", "max_translation_step",
                "max_rotation_step", "keyframe_translation", "keyframe_rotation",
                "submap_keyframes", "map_voxel_size"):
        assert key in cfg
    assert configuration_version(cfg) == configuration_version(dict(cfg))
    assert configuration_version(cfg) != configuration_version({**cfg, "max_rmse": 99})
    broken = tmp_path / "broken.yaml"
    broken.write_text(json.dumps({"voxel_size": 0.3}), "utf-8")
    with pytest.raises(ValueError):
        configuration(broken)
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(json.dumps({**cfg, "min_points": 2}), "utf-8")
    with pytest.raises(ValueError):
        configuration(invalid)


def test_frame_states_vocabulary_is_stable():
    assert set(FRAME_STATES) == {"reference", "accepted", "quality_failed", "insufficient_points",
                                 "degenerate_geometry", "timestamp_invalid", "tracking_lost",
                                 "reinitialized"}


def test_normals_point_along_the_surface_normal():
    rng = np.random.default_rng(19)
    plane = np.column_stack((rng.uniform(-1, 1, 300), rng.uniform(-1, 1, 300), np.zeros(300)))
    normals = estimate_normals(plane, 8)
    assert np.allclose(np.abs(normals[:, 2]), 1.0, atol=1e-6)

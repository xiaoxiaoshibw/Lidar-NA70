"""SYNTHETIC tests for the 4D spatio-temporal band detector.

Every case here uses generated points, never NA70 captures. Results are logic
regressions only and must not be quoted as perception performance evidence.
"""

import numpy as np

from backend.bands import BandConfig, CandidatePoints, detect_bands, weighted_dbscan


FPS = 10.0


def walk(duration=6.0, speed=1.2, start=(0.0, 0.0, 1.0), direction=(1.0, 0.0, 0.0),
         body_half=(0.25, 0.25, 0.85), points=60, seed=3, skip=None, jitter=0.03):
    """Return (positions, moments) for a person-sized box travelling in a line.

    ``body_half`` is a 0.5 x 0.5 x 1.7 m body, i.e. realistic pedestrian extent,
    so the cross-section stays a meaningful reference for "not the path length".
    """
    rng = np.random.default_rng(seed)
    frames = int(round(duration * FPS))
    heading = np.asarray(direction, dtype=np.float64)
    heading = heading / np.linalg.norm(heading)
    origin = np.asarray(start, dtype=np.float64)
    half = np.asarray(body_half, dtype=np.float64)
    positions, moments = [], []
    for index in range(frames):
        moment = index / FPS
        if skip is not None and skip[0] <= moment < skip[1]:
            continue
        centre = origin + heading * speed * moment
        cloud = centre + rng.uniform(-1.0, 1.0, (points, 3)) * half + rng.normal(0.0, jitter, (points, 3))
        positions.append(cloud)
        moments.append(np.full(points, moment))
    return np.vstack(positions), np.concatenate(moments)


def still(duration=6.0, centre=(3.0, 0.0, 1.0), points=60, seed=11, body_half=(0.25, 0.25, 0.85)):
    rng = np.random.default_rng(seed)
    frames = int(round(duration * FPS))
    total = frames * points
    cloud = np.asarray(centre) + rng.uniform(-1.0, 1.0, (total, 3)) * np.asarray(body_half)
    return cloud, np.repeat(np.arange(frames) / FPS, points)


def candidates(positions, moments, total_frames):
    frame = np.rint(np.asarray(moments) * FPS).astype(np.int32)
    frame = np.clip(frame, 0, total_frames - 1)
    return CandidatePoints(
        position=np.asarray(positions, dtype=np.float32),
        time=np.asarray(moments, dtype=np.float64),
        frame=frame,
        distance=np.linalg.norm(positions, axis=1).astype(np.float32),
        intensity=np.full(len(positions), 120, dtype=np.uint8),
        point_index=np.arange(len(positions), dtype=np.int32),
    )


def frame_times(total_frames):
    return np.arange(total_frames, dtype=np.float64) / FPS


def run(positions, moments, total_frames, config=None):
    points = candidates(positions, moments, total_frames)
    return detect_bands(points, frame_times(total_frames), config or BandConfig())


def recall(detection, total):
    """Fraction of the first ``total`` candidate points that landed in a band."""
    return float(np.count_nonzero(detection.point_band[:total] >= 0) / max(total, 1))


def test_static_background_produces_no_band():
    positions, moments = still(seed=5)
    detection = run(positions, moments, 60)
    assert detection.bands == []
    assert not (detection.point_band >= 0).any()


def test_single_moving_target_becomes_one_elongated_band():
    positions, moments = walk()
    detection = run(positions, moments, 60)
    assert len(detection.bands) == 1
    band = detection.bands[0]
    assert band["duration"] >= 5.0
    assert band["displacement"] >= 5.0
    assert 1.0 <= band["speed"] <= 1.4
    assert recall(detection, len(positions)) >= 0.95


def test_band_length_is_path_length_not_body_size():
    positions, moments = walk(duration=8.0, speed=1.5)
    detection = run(positions, moments, 80)
    assert len(detection.bands) == 1
    band = detection.bands[0]
    # The travelled path is ~12 m; the cross-section stays roughly person sized.
    assert band["spatial_span"] >= 9.0
    assert band["local_cross_norm"] <= 2.2
    assert band["local_cross_norm"] < band["trajectory_length"] / 4
    assert band["spatial_span"] > 4 * band["local_cross_norm"]


def test_sharp_turn_still_counts_as_one_moving_target():
    first = walk(duration=3.0, speed=1.2, start=(-3.0, 0.0, 1.0), direction=(1.0, 0.0, 0.0), seed=71)
    second = walk(duration=3.0, speed=1.2, start=(0.6, 0.0, 1.0), direction=(0.0, 1.0, 0.0), seed=72)
    positions = np.vstack([first[0], second[0]])
    moments = np.concatenate([first[1], second[1] + 3.0])
    detection = run(positions, moments, 60)
    assert len(detection.bands) == 1
    band = detection.bands[0]
    assert band["branches"] == 2
    assert band["duration"] >= 5.0
    assert recall(detection, len(positions)) >= 0.95


def test_stationary_foreground_object_is_not_a_band():
    positions, moments = still(centre=(2.0, 1.0, 1.0), seed=9)
    detection = run(positions, moments, 60)
    assert detection.bands == []
    assert detection.rejected >= 1


def test_scan_sweep_without_a_target_is_rejected():
    """Appearance swings and single-frame specks must not survive acceptance."""
    rng = np.random.default_rng(21)
    frames = 60
    positions, moments = [], []
    for index in range(frames):
        count = int(rng.integers(4, 12))
        cloud = rng.uniform(-9.0, 9.0, (count, 3))
        cloud[:, 2] = rng.uniform(0.2, 2.5, count)
        positions.append(cloud)
        moments.append(np.full(count, index / FPS))
    detection = run(np.vstack(positions), np.concatenate(moments), frames)
    assert detection.bands == []
    assert detection.point_cluster.max(initial=-1) <= 0 or detection.bands == []
    assert not (detection.point_band >= 0).any()


def test_two_crossing_targets_are_detected_without_exploding_into_fragments():
    """Two pedestrians passing on adjacent lanes must stay two bands."""
    first = walk(duration=6.0, speed=1.1, start=(-3.0, -0.9, 1.0), direction=(1.0, 0.0, 0.0), seed=31)
    second = walk(duration=6.0, speed=1.3, start=(3.0, 0.9, 1.0), direction=(-1.0, 0.0, 0.0), seed=32)
    positions = np.vstack([first[0], second[0]])
    moments = np.concatenate([first[1], second[1]])
    detection = run(positions, moments, 60)
    assert len(detection.bands) == 2
    assert recall(detection, len(first[0])) >= 0.9
    assert recall(detection, len(second[0])) >= 0.9


def test_short_dropout_is_merged_back_into_one_band():
    positions, moments = walk(duration=8.0, speed=1.2, skip=(3.0, 4.0), seed=41)
    detection = run(positions, moments, 80)
    assert len(detection.bands) == 1
    assert detection.stats["merged_fragments"] >= 1
    assert detection.bands[0]["duration"] >= 6.0


def test_isolated_noise_points_are_ignored():
    positions, moments = walk(seed=51)
    rng = np.random.default_rng(52)
    noise = rng.uniform(-15.0, 15.0, (40, 3))
    noise[:, 2] = rng.uniform(0.0, 3.0, 40)
    noise_moments = rng.uniform(0.0, 6.0, 40)
    detection = run(np.vstack([positions, noise]),
                    np.concatenate([moments, noise_moments]), 60)
    assert len(detection.bands) == 1
    band = detection.bands[0]
    assert band["point_count"] >= 0.9 * len(positions) - 60


def test_displacement_less_than_threshold_is_rejected():
    positions, moments = walk(duration=4.0, speed=0.1, seed=61)
    detection = run(positions, moments, 60)
    assert detection.bands == []
    assert detection.stats["rejected_bands"] >= 1
    assert "displacement" in detection.stats["rejected_reasons"]
    assert "speed" in detection.stats["rejected_reasons"]


def test_band_config_validation_rejects_bad_thresholds():
    for field in ("voxel_size", "time_voxel", "speed_scale", "eps", "slice_seconds"):
        config = BandConfig(**{field: 0.0})
        try:
            config.validate()
        except ValueError:
            continue
        raise AssertionError(f"{field} should be rejected")
    try:
        BandConfig(aabb_percentile=0.2).validate()
    except ValueError:
        return
    raise AssertionError("aabb_percentile out of range should be rejected")


def test_weighted_dbscan_clusters_voxels_by_raw_weight():
    points = np.array([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [9.0, 9.0, 9.0]])
    weights = np.array([10, 10, 1])
    labels = weighted_dbscan(points, weights, 0.5, 12)
    assert labels[0] == labels[1] == 0
    assert labels[2] == -1

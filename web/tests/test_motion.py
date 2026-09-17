import numpy as np

from backend.decoder import scan_pcap
from backend.motion import FixedLidarMotionAnalyzer, MotionConfig, analyze_capture
from test_decoder import make_pcap, payload


def background():
    x, y = np.meshgrid(np.linspace(-4, 4, 32), np.linspace(-4, 4, 32))
    xyz = np.column_stack([x.ravel(), y.ravel(), np.zeros(x.size)])
    return np.column_stack([xyz, np.full(len(xyz), 20)]).astype(np.float32)


def person(x, seed=7):
    rng = np.random.default_rng(seed)
    xyz = rng.normal((x, 0, 1.0), (0.16, 0.16, 0.45), (100, 3))
    return np.column_stack([xyz, np.full(len(xyz), 140)]).astype(np.float32)


def analyzer():
    return FixedLidarMotionAnalyzer(MotionConfig(
        warmup_frames=5, background_min_hits=3, confirmation_frames=3,
        cluster_min_points=8, min_moving_points=20,
    ))


def test_moving_person_is_clustered_tracked_and_mapped_to_original_points():
    detector = analyzer()
    base = background()
    for frame in range(5):
        result, mask = detector.process(base, frame * 0.1)
        assert not result["has_motion"] and not mask.any()

    outputs = []
    for frame, x in enumerate([5.0, 5.25, 5.5, 5.75, 6.0], start=5):
        cloud = np.vstack([base, person(x)])
        outputs.append(detector.process(cloud, frame * 0.1))

    result, mask = outputs[-1]
    assert result["has_motion"]
    assert result["moving_count"] >= 80
    assert not mask[:len(base)].any()
    assert mask[len(base):].sum() >= 80
    assert any(cluster["moving"] and cluster["track_status"] == "CONFIRMED" for cluster in result["clusters"])
    assert any(track["moving"] for track in detector.track_summaries())


def test_new_but_stationary_object_remains_foreground_not_motion():
    detector = analyzer()
    base = background()
    for frame in range(5):
        detector.process(base, frame * 0.1)
    for frame in range(5, 12):
        result, mask = detector.process(np.vstack([base, person(5.0)]), frame * 0.1)
    assert result["candidate_count"] >= 80
    assert result["clusters"]
    assert result["moving_count"] == 0
    assert not result["has_motion"]
    assert not mask.any()


def test_isolated_foreground_point_is_removed_as_noise():
    detector = analyzer()
    base = background()
    for frame in range(5):
        detector.process(base, frame * 0.1)
    isolated = np.array([[12.0, 12.0, 1.0, 200.0]], dtype=np.float32)
    result, mask = detector.process(np.vstack([base, isolated]), 0.6)
    assert result["candidate_count"] == 0
    assert not mask.any()


def test_full_capture_ray_history_finds_candidates_and_keeps_mask_stream(tmp_path):
    """A repeated nearer return is a ray-history *candidate*.

    The synthetic payload is a single fixed 3D cell, so it is a static closer
    return, not a walking person: the band stage must decline to call it motion.
    """
    packets = [payload(distance=1000)] * 2000 + [payload(distance=500)] * 1000
    capture = make_pcap(tmp_path / "ray-motion.pcap", packets)
    scan = scan_pcap(capture)
    summary = analyze_capture(capture, scan.frames, tmp_path, MotionConfig())
    assert summary["method"] == "ray_history_spatiotemporal_bands"
    assert summary["stable_ray_count"] == 1
    assert summary["candidate_points"] > 0
    assert summary["bands"] == []
    assert summary["moving_points"] == 0
    assert summary["events"] == []
    assert (tmp_path / "analysis_masks.bin").stat().st_size == sum(frame["point_count"] for frame in scan.frames)


def test_full_capture_ray_history_reports_a_walking_band(tmp_path):
    """A slowly receding return along one ray is a real spatio-temporal band."""
    packets = [payload(distance=1000)] * 2000
    for step in range(24):
        packets += [payload(distance=900 - 12 * step)] * 100
    capture = make_pcap(tmp_path / "ray-walk.pcap", packets)
    scan = scan_pcap(capture)
    summary = analyze_capture(capture, scan.frames, tmp_path, MotionConfig())
    assert summary["candidate_points"] > 0
    assert summary["band_count"] == 1
    assert summary["moving_points"] > 0
    assert summary["moving_frames"] > 0
    assert summary["bands"][0]["duration"] > 1.5

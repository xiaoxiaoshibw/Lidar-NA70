"""Contract checks for SYNTHETIC wire geometry and ray auditing."""
from pathlib import Path

import numpy as np

from backend import decoder
from backend.wire_scene import (
    load_scene, nominal_world_directions, ray_directions_from_codes,
    ray_wire_hits, sample_geometry, wire_curve,
)


ROOT = Path(__file__).resolve().parents[2]
SCENE = ROOT / "tools/scenarios/uav_single_wire_outdoor_v1.json"


def test_scene_geometry_truth_is_deterministic_and_absent_variant_is_empty():
    scene = load_scene(SCENE)
    curve = wire_curve(scene)
    np.testing.assert_allclose(curve[0], [35, -25, 19])
    np.testing.assert_allclose(curve[-1], [35, 25, 19])
    np.testing.assert_allclose(curve[len(curve) // 2], [35, 0, 17.5])
    points, labels = sample_geometry(scene, seed=123)
    points_again, labels_again = sample_geometry(scene, seed=123)
    np.testing.assert_array_equal(points, points_again)
    np.testing.assert_array_equal(labels, labels_again)
    assert np.count_nonzero(labels == "wire_truth") == 501
    scene["wire"]["enabled"] = False
    _, absent_labels = sample_geometry(scene, seed=123)
    assert not np.any(absent_labels == "wire_truth")


def test_ray_intersection_and_miss():
    scene = load_scene(SCENE)
    curve = wire_curve(scene)
    origin = np.array([0.0, 0.0, 12.0])
    aim = curve[len(curve) // 2] - origin
    miss = np.array([1.0, 0.0, 0.0])
    hit, distance = ray_wire_hits(np.stack((aim, miss, -aim)), origin, curve, .01)
    np.testing.assert_array_equal(hit, [True, False, False])
    assert distance[0] < 1e-9
    assert distance[1] > 5.0


def test_code_directions_match_formal_decoder_on_real_capture():
    pcap = ROOT / "radar_capture_data/1.pcap"
    if not pcap.is_file():
        return
    frame = decoder.scan_pcap(pcap).frames[0]
    codes = decoder.decode_frame_codes(pcap, frame)
    points = decoder.decode_frame(pcap, frame, install_direction=1)
    valid = codes[:, 0] > 0
    directions = ray_directions_from_codes(codes, frame, install_direction=1)
    normed = points[valid, :3] / np.linalg.norm(points[valid, :3], axis=1)[:, None]
    np.testing.assert_allclose(directions[valid], normed, atol=2e-6)
    front = nominal_world_directions(np.array([[0.0, -1.0, 0.0]]), 0.0)
    np.testing.assert_allclose(front, [[1.0, 0.0, 0.0]])

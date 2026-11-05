"""Tests for the packet-time LIO scan package (SYNTHETIC frames)."""
import numpy as np
import pytest

from na70_core.lio_scans import build_scans, load_npz, save_npz, time_quality


def frame(packets=2, points_per_packet=200, start=10.0):
    times = start + np.arange(packets) * 0.01
    points = np.ones((packets * points_per_packet, 4), dtype=np.float32)
    return points, times


def test_build_scans_preserves_source_references():
    scans = build_scans([frame(2, start=10.0), frame(2, start=10.02)])
    assert len(scans.points) == 800
    assert scans.frame_offsets.tolist() == [0, 400, 800]
    assert scans.frame_start_s.tolist() == pytest.approx([0.0, 0.02])
    assert scans.frame_end_s.tolist() == pytest.approx([0.01, 0.03])
    assert scans.point_time_s[:3].tolist() == pytest.approx([0.0, 0.0, 0.0])
    assert scans.point_time_s[400] == pytest.approx(0.02)
    assert scans.packet_index[:2].tolist() == [0, 0]
    assert scans.packet_index[200:202].tolist() == [1, 1]
    assert scans.packet_index[400:402].tolist() == [2, 2]
    assert scans.point_index[:5].tolist() == [0, 1, 2, 3, 4]
    assert scans.point_index[200] == 0


def test_build_scans_rejects_padding_or_truncation():
    points, times = frame(3)
    with pytest.raises(ValueError, match="no padding"):
        build_scans([(points[:500], times)])


def test_build_scans_rejects_cross_frame_rewind():
    with pytest.raises(ValueError, match="rewinds"):
        build_scans([frame(2, start=10.0), frame(2, start=10.005)])


def test_time_quality_reports_packet_gap():
    points, times = frame(2)
    times = np.array([10.0, 10.5])
    scans = build_scans([(points, times)])
    report = time_quality(scans, max_packet_gap_s=0.2)
    assert report["time_quality"] == "PACKET_APPROXIMATION"
    assert report["gaps_over_limit"] == 1
    assert report["max_packet_gap_s"] == pytest.approx(0.5)
    assert report["points_per_packet"] == 200


def test_time_quality_checks_frame_boundary_and_equal_packet_times():
    points, _ = frame(2)
    scans = build_scans([(points, [10.0, 10.0]), (points, [10.5, 10.51])])
    report = time_quality(scans, max_packet_gap_s=0.2)
    assert report["max_packet_gap_s"] == pytest.approx(0.5)
    assert report["gaps_over_limit"] == 1


def test_save_load_roundtrip_without_pickle(tmp_path):
    scans = build_scans([frame(2)])
    path = save_npz(tmp_path / "scans.npz", scans, note="SYNTHETIC")
    arrays = load_npz(path)
    assert arrays["points"].shape == (400, 4)
    assert arrays["frame_offsets"].tolist() == [0, 400]
    with np.load(path, allow_pickle=False) as archive:
        assert "note" in archive
    (tmp_path / "bad.npz").write_bytes(b"not an npz")
    with pytest.raises((ValueError, OSError)):
        load_npz(tmp_path / "bad.npz")

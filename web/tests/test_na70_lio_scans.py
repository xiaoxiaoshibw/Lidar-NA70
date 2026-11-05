"""Tests for the packet-time LIO scan package (SYNTHETIC frames)."""
import numpy as np
import pytest

from na70_core.lio_scans import (POINT_INTERPOLATED_UNIFORM_PACKET_GAP, build_scans,
                                 load_npz, save_npz, time_quality)


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


def interpolated_frame(packets, points_per_packet, times):
    points = np.ones((packets * points_per_packet, 4), dtype=np.float32)
    return points, np.asarray(times, dtype=np.float64)


def test_interpolated_times_equal_spec_formula_with_positive_gaps():
    # Session packets at 10.00/10.01/10.02/10.03 s (frame boundary crossed at k=1→2).
    times = [10.0, 10.01, 10.02, 10.03]
    scans = build_scans([interpolated_frame(2, 4, times[:2]),
                         interpolated_frame(2, 4, times[2:])],
                        points_per_packet=4, point_time_mode="interpolated")
    session = np.asarray(times, dtype=np.float64)
    gaps = np.diff(session)  # the implementation interpolates with these exact values
    expected = []
    for k in range(4):
        gap = float(gaps[k]) if k < len(gaps) else float(gaps[-1])
        expected.extend(session[k] + gap * (i / 4) for i in range(4))
    expected = np.asarray(expected) - 10.0
    assert np.array_equal(scans.point_time_s, expected)
    assert np.all(np.diff(scans.point_time_s) > 0)
    assert scans.packet_time_counters == (3, 0, 1)


def test_interpolated_zero_gap_packet_collapses_without_regression():
    # Packet 0 shares the host time of packet 1 (observed duplicate host times).
    scans = build_scans([interpolated_frame(3, 2, [10.0, 10.0, 10.5])],
                        points_per_packet=2, point_time_mode="interpolated")
    assert scans.point_time_s.tolist() == pytest.approx([0.0, 0.0, 0.0, 0.25, 0.5, 0.75])
    assert np.all(np.diff(scans.point_time_s) >= 0)
    assert scans.packet_time_counters == (1, 1, 1)


def test_interpolated_cross_frame_duplicate_time_collapses():
    scans = build_scans([interpolated_frame(1, 2, [10.0]),
                         interpolated_frame(1, 2, [10.0])],
                        points_per_packet=2, point_time_mode="interpolated")
    assert scans.point_time_s.tolist() == pytest.approx([0.0, 0.0, 0.0, 0.0])
    assert scans.packet_time_counters == (0, 1, 1)


def test_interpolated_last_packet_fallback_counts():
    # Previous gap is zero: the last packet shares its own time, fallback=1.
    scans = build_scans([interpolated_frame(2, 2, [10.0, 10.0])],
                        points_per_packet=2, point_time_mode="interpolated")
    assert scans.point_time_s.tolist() == pytest.approx([0.0, 0.0, 0.0, 0.0])
    assert scans.packet_time_counters == (0, 1, 1)
    # Single-packet session: no t_{k+1} and no previous gap.
    scans = build_scans([interpolated_frame(1, 2, [10.0])],
                        points_per_packet=2, point_time_mode="interpolated")
    assert scans.packet_time_counters == (0, 0, 1)
    assert scans.point_time_s.tolist() == pytest.approx([0.0, 0.0])


def test_default_mode_unchanged_and_rejects_unknown_mode():
    frames = [interpolated_frame(2, 200, [10.0, 10.01]),
              interpolated_frame(2, 200, [10.02, 10.03])]
    default = build_scans(frames)
    packet = build_scans(frames, point_time_mode="packet")
    assert np.array_equal(default.point_time_s, packet.point_time_s)
    assert default.time_mode == "packet"
    assert default.packet_time_counters == (0, 0, 0)
    # Packet mode: every point of a packet shares its time (regression red line).
    assert default.point_time_s[0] == pytest.approx(0.0)
    assert default.point_time_s[199] == pytest.approx(0.0)
    assert default.point_time_s[400] == pytest.approx(0.02)
    assert np.array_equal(default.points, packet.points)
    assert np.array_equal(default.frame_offsets, packet.frame_offsets)
    with pytest.raises(ValueError, match="point_time_mode"):
        build_scans(frames, point_time_mode="measured")


def test_time_quality_reports_interpolated_mode_fields(tmp_path):
    scans = build_scans([interpolated_frame(2, 200, [10.0, 10.5])],
                        point_time_mode="interpolated")
    report = time_quality(scans, max_packet_gap_s=0.2)
    assert report["time_quality"] == POINT_INTERPOLATED_UNIFORM_PACKET_GAP
    assert report["point_time_mode"] == "interpolated"
    assert report["packets_interpolated"] == 1
    assert report["packets_collapsed"] == 0
    assert report["packets_fallback"] == 1
    assert any("声明近似" in line and "实测逐点时间" in line for line in report["limitations"])
    path = save_npz(tmp_path / "scans.npz", scans)
    arrays = load_npz(path)  # non-decreasing validation must still pass
    assert len(arrays["point_time_s"]) == 400

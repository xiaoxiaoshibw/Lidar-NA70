"""Contract tests for na70_core.windows.PacketWindowRing.

Mirrors thin_wire's proven selection semantics so an incremental replay and
a one-shot offline audit agree on the same windows when fed the same packets.
"""
from __future__ import annotations

import numpy as np
import pytest

from na70_core import (PacketIdentity, PacketWindowRing, PushStatus,
                       ResourceLimitError, RingLimits, SelectedWindow)


def _limits(**overrides):
    base = dict(max_packets_in_ring=2000, max_window_packets=50_000,
                max_packet_gap_s=1.0, time_epsilon_s=1e-3)
    base.update(overrides)
    return RingLimits(**base)


def _frame_time(frame_index: int, local_index: int) -> float:
    # 100-packet frame at 10 Hz; used by both feeders so offline and ring
    # paths see identical per-packet timestamps.
    return 100.0 + (frame_index * 100 + local_index) * 0.001


def _feed(ring: PacketWindowRing, frame_index: int, packet_count: int,
          start_time: float | None = None, step: float = 0.001) -> None:
    for local in range(packet_count):
        t = start_time + local * step if start_time is not None else _frame_time(frame_index, local)
        ring.push(frame_index=frame_index, local_index=local,
                  device_time=t, capture_time=t)


def test_empty_ring_selection_rejected():
    ring = PacketWindowRing(_limits())
    with pytest.raises(ValueError):
        ring.select(0.5)


def test_resource_limits_rejected_at_construction():
    with pytest.raises(ValueError):
        RingLimits(max_packets_in_ring=0, max_window_packets=100)
    with pytest.raises(ValueError):
        RingLimits(max_packets_in_ring=10, max_window_packets=100,
                   time_epsilon_s=2.0, max_packet_gap_s=1.0)


def test_ring_matches_thin_wire_select_window():
    pytest.importorskip("backend.thin_wire")
    from backend import thin_wire

    frames = [{"index": f,
               "packet_device_times": [_frame_time(f, local) for local in range(100)],
               "packet_capture_times": [_frame_time(f, local) for local in range(100)]}
              for f in range(5)]

    class Scan:
        pass
    scan = Scan()
    scan.frames = frames

    cfg = thin_wire.load_config()
    offline_packets = thin_wire.packet_timeline(scan, cfg)
    for end_frame in (2, 3, 4):
        for duration in (None, 0.5, 1.0):
            selected_off, meta_off = thin_wire.select_window(offline_packets, end_frame, duration, cfg)

            ring = PacketWindowRing(_limits(max_packets_in_ring=5000,
                                            time_epsilon_s=cfg["time_epsilon_s"]))
            for f in range(5):
                _feed(ring, f, 100)
            online = ring.select(duration, end_frame_index=end_frame)

            assert online.packet_count == len(selected_off)
            assert online.time_quality == meta_off["time_quality"]
            assert online.partial_window == meta_off["partial_window"]
            online_ids = [(p.frame_index, p.index, p.local_index) for p in online.packets]
            offline_ids = [(p.frame_index, p.index, p.local_index) for p in selected_off]
            assert online_ids == offline_ids
            if meta_off["actual_start_device_time"] is not None:
                assert online.actual_start_device_time == pytest.approx(meta_off["actual_start_device_time"], abs=1e-6)
            if meta_off["actual_span_s"] is not None:
                assert online.actual_span_s == pytest.approx(meta_off["actual_span_s"], abs=1e-6)


def test_incremental_feed_equivalent_to_one_shot():
    frames = [(f, 100) for f in (0, 1, 2, 3)]
    starts = {0: 10.000, 1: 10.100, 2: 10.200, 3: 10.300}

    # one-shot ring after loading everything
    full = PacketWindowRing(_limits(max_packets_in_ring=10_000))
    two_step = PacketWindowRing(_limits(max_packets_in_ring=10_000))
    for f, cnt in frames:
        _feed(full, f, cnt, starts[f])
    for f, cnt in frames[:2]:
        _feed(two_step, f, cnt, starts[f])
    two_step.select(0.5, end_frame_index=1)
    for f, cnt in frames[2:]:
        _feed(two_step, f, cnt, starts[f])

    a = full.select(0.05, end_frame_index=3)
    b = two_step.select(0.05, end_frame_index=3)
    assert (a.packet_count, a.time_quality, a.actual_span_s) == (b.packet_count, b.time_quality, b.actual_span_s)
    assert [(p.index, p.frame_index) for p in a.packets] == [(p.index, p.frame_index) for p in b.packets]


def test_bad_device_time_terminates_segment_and_no_mixed_window():
    ring = PacketWindowRing(_limits(max_packets_in_ring=2000))
    _feed(ring, 0, 50, 50.000)
    status = ring.push(frame_index=0, local_index=50, device_time=None, capture_time=50.05)
    assert status == PushStatus.DEVICE_TIME_INVALID
    _feed(ring, 0, 49, 50.051)
    _feed(ring, 1, 100, 50.151)

    window = ring.select(None, end_frame_index=0)
    assert window.time_quality == "invalid_device_time"
    assert window.actual_span_s is None

    second = ring.select(0.1, end_frame_index=1)
    assert second.time_quality == "ok"
    assert all(p.device_time is not None for p in second.packets)


def test_device_backwards_starts_new_segment():
    ring = PacketWindowRing(_limits(max_packets_in_ring=2000))
    _feed(ring, 0, 100, 10.000)
    _feed(ring, 1, 100, 9.950)
    segments = [p.segment for p in ring.packets() if p.device_time is not None]
    assert segments == [0] * 100 + [1] * 100


def test_gap_starts_new_segment():
    ring = PacketWindowRing(_limits(max_packets_in_ring=2000, max_packet_gap_s=0.05))
    _feed(ring, 0, 100, 10.000)
    _feed(ring, 1, 100, 10.700)
    segments = [p.segment for p in ring.packets() if p.device_time is not None]
    assert segments == [0] * 100 + [1] * 100


def test_ring_eviction_preserves_recent_selection():
    ring = PacketWindowRing(_limits(max_packets_in_ring=120, max_window_packets=50_000))
    for frame in range(3):
        _feed(ring, frame, 100, 10.0 + frame * 0.1)
    assert ring.buffered_packet_count == 120
    window = ring.select(0.05, end_frame_index=2)
    assert window.packet_count <= 120


def test_window_packet_limit_rejected():
    ring = PacketWindowRing(_limits(max_packets_in_ring=10_000, max_window_packets=150))
    for frame in range(5):
        _feed(ring, frame, 100, 10.0 + frame * 0.1)
    with pytest.raises(ResourceLimitError):
        ring.select(0.4, end_frame_index=4)


def test_no_future_packets_in_window():
    ring = PacketWindowRing(_limits(max_packets_in_ring=10_000))
    for frame in range(5):
        _feed(ring, frame, 100, 10.0 + frame * 0.1)
    window = ring.select(0.05, end_frame_index=2)
    assert all(p.frame_index <= 2 for p in window.packets)

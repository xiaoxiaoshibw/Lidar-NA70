"""FrameBuffer ring behaviour: capacity, eviction, ordering, bad input."""
import numpy as np
import pytest

from backend.goal_detection.buffer import FrameBuffer, StampedCloud


def cloud(index, time=None, n=3):
    points = np.full((n, 4), float(index), dtype=np.float32)
    return StampedCloud(points, frame_index=index, device_time=time)


def test_push_and_window_order():
    buffer = FrameBuffer(3)
    for i in range(3):
        buffer.push(cloud(i))
    assert [f.frame_index for f in buffer.window()] == [0, 1, 2]
    assert buffer.is_full()


def test_evicts_oldest_and_keeps_capacity():
    buffer = FrameBuffer(3)
    for i in range(10):
        buffer.push(cloud(i))
    assert len(buffer) == 3
    assert [f.frame_index for f in buffer.window()] == [7, 8, 9]
    assert buffer.is_full()


def test_empty_and_last():
    buffer = FrameBuffer(2)
    assert len(buffer) == 0
    assert buffer.last() is None
    assert not buffer.is_full()
    buffer.push(cloud(5))
    assert buffer.last().frame_index == 5


def test_clear_resets_state():
    buffer = FrameBuffer(2)
    buffer.push(cloud(0, time=1.0))
    buffer.push(cloud(1, time=0.5))
    assert not buffer.monotonic
    buffer.clear()
    assert len(buffer) == 0 and buffer.monotonic


def test_non_monotonic_time_is_flagged_not_resorted():
    buffer = FrameBuffer(5)
    buffer.push(cloud(0, time=2.0))
    buffer.push(cloud(1, time=1.0))
    assert [f.frame_index for f in buffer.window()] == [0, 1]
    assert buffer.monotonic is False


def test_decreasing_frame_index_rejected():
    buffer = FrameBuffer(5)
    buffer.push(cloud(4))
    with pytest.raises(ValueError):
        buffer.push(cloud(3))
    with pytest.raises(ValueError):
        buffer.push(cloud(4))


def test_capacity_must_be_positive():
    with pytest.raises(ValueError):
        FrameBuffer(0)


def test_points_shape_validated():
    with pytest.raises(ValueError):
        StampedCloud(np.zeros((4, 3), dtype=np.float32), frame_index=0)
    with pytest.raises(ValueError):
        StampedCloud(np.zeros(4, dtype=np.float32), frame_index=0)


def test_points_coerced_to_float32_row_major():
    stamped = StampedCloud(np.zeros((2, 4), dtype=np.float64), frame_index=0)
    assert stamped.points.dtype == np.float32
    assert stamped.points.flags["C_CONTIGUOUS"]

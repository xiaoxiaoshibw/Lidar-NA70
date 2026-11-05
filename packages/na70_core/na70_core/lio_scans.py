"""Assemble NA70 frames into a source-preserving LIO scan package.

Each NA70 packet still has one packet time (the packet's place in the capture).
By default every point inside that packet inherits it: the package declares
``PACKET_APPROXIMATION`` and keeps the original frame/packet/point indices.

An explicitly requested ``point_time_mode="interpolated"`` instead spreads the
points of packet k uniformly across the gap to the next packet of the session
(``POINT_INTERPOLATED_UNIFORM_PACKET_GAP``).  This is still a declared
approximation -- the real per-point sampling order inside a packet is unknown,
so it is not a measured per-point time.

The consumer (FAST-LIO/GLIM adapter) is responsible for deskewing and for
deciding whether the declared time approximation satisfies its error budget.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


PACKET_APPROXIMATION = "PACKET_APPROXIMATION"
POINT_INTERPOLATED_UNIFORM_PACKET_GAP = "POINT_INTERPOLATED_UNIFORM_PACKET_GAP"
POINT_TIME_MODES = ("packet", "interpolated")

LIMITATIONS = (
    "逐点时间由包时间复制，属于 PACKET_APPROXIMATION，不是实测逐点时间",
    "包内采样时序仍未核实；不能用本包宣称逐点去畸变",
    "主机接收时间与设备采样时间必须由 manifest 的时间等级区分",
    "源引用只保证帧/包/点索引，不保证硬件时钟",
)

INTERPOLATED_LIMITATIONS = (
    "逐点时间由包内点序在包间隔上均匀插值，属于 POINT_INTERPOLATED_UNIFORM_PACKET_GAP，"
    "是声明近似，不是实测逐点时间",
    "插值假设包内 200 点采样均匀；包内真实采样时序仍未核实，不能据此宣称实测去畸变",
    "包间 gap<=0（含主机时间重复的观测事实）的包退化为共享包时间",
    "主机接收时间与设备采样时间必须由 manifest 的时间等级区分",
    "源引用只保证帧/包/点索引，不保证硬件时钟",
)


def limitations_for(point_time_mode):
    if point_time_mode == "packet":
        return list(LIMITATIONS)
    if point_time_mode == "interpolated":
        return list(INTERPOLATED_LIMITATIONS)
    raise ValueError(f"unknown point_time_mode {point_time_mode!r}")


@dataclass(frozen=True)
class ScanSet:
    points: np.ndarray
    point_time_s: np.ndarray
    frame_index: np.ndarray
    packet_index: np.ndarray
    point_index: np.ndarray
    frame_start_s: np.ndarray
    frame_end_s: np.ndarray
    frame_offsets: np.ndarray
    time_mode: str = "packet"
    # (packets_interpolated, packets_collapsed, packets_fallback); zero in packet mode.
    packet_time_counters: tuple = (0, 0, 0)

    def as_arrays(self):
        return {
            "points": self.points,
            "point_time_s": self.point_time_s,
            "frame_index": self.frame_index,
            "packet_index": self.packet_index,
            "point_index": self.point_index,
            "frame_start_s": self.frame_start_s,
            "frame_end_s": self.frame_end_s,
            "frame_offsets": self.frame_offsets,
        }


def _points(value, index):
    array = np.asarray(value)
    if array.ndim != 2 or array.shape[1] not in (3, 4):
        raise ValueError(f"frame {index}: points must be Nx3 or Nx4")
    if len(array) == 0:
        raise ValueError(f"frame {index}: no points")
    if not np.isfinite(array[:, :3]).all():
        raise ValueError(f"frame {index}: non-finite points")
    return array


def build_scans(frames, *, points_per_packet=200, point_time_mode="packet"):
    """Build one ScanSet from ``(points, packet_times)`` pairs.

    ``packet_times`` are absolute capture times in one declared clock.  The
    output ``point_time_s`` is relative to the first packet of the first frame.
    A frame whose point count does not equal ``points_per_packet * packets`` is
    rejected instead of being padded or truncated.

    ``point_time_mode="packet"`` (default) copies the packet time to every point.
    ``point_time_mode="interpolated"`` spreads packet k's points uniformly over
    ``gap_k = t_{k+1} - t_k`` (the next packet of the session, frame boundary
    allowed): ``t_k + gap_k * i / N``.  A packet with ``gap_k <= 0`` (duplicate
    host times were observed) falls back to sharing ``t_k`` and is counted in
    ``packets_collapsed``.  The last packet has no ``t_{k+1}``: it reuses the
    previous gap when that is positive, otherwise shares ``t_k``, and is counted
    in ``packets_fallback``.  The three counters partition all packets.
    """
    if not isinstance(points_per_packet, int) or points_per_packet <= 0:
        raise ValueError("points_per_packet must be a positive integer")
    if point_time_mode not in POINT_TIME_MODES:
        raise ValueError(f"point_time_mode must be one of {POINT_TIME_MODES}")
    frames = list(frames)
    if not frames:
        raise ValueError("no frames to assemble")

    origin = None
    previous_end = None
    frame_points = []
    frame_times = []
    for frame_number, (points, packet_times) in enumerate(frames):
        points = _points(points, frame_number)
        packet_times = np.asarray(packet_times, dtype=np.float64).reshape(-1)
        if len(packet_times) == 0:
            raise ValueError(f"frame {frame_number}: no packet times")
        if not np.isfinite(packet_times).all():
            raise ValueError(f"frame {frame_number}: non-finite packet time")
        if len(packet_times) > 1 and np.any(np.diff(packet_times) < 0):
            raise ValueError(f"frame {frame_number}: packet time moved backwards")
        expected = points_per_packet * len(packet_times)
        if len(points) != expected:
            raise ValueError(
                f"frame {frame_number}: {len(points)} points for {len(packet_times)} packets "
                f"(expected {expected}); no padding is performed")
        if origin is None:
            origin = float(packet_times[0])
        if packet_times[0] < origin:
            raise ValueError(f"frame {frame_number}: frame starts before session origin")
        if previous_end is not None and packet_times[0] < previous_end:
            raise ValueError(f"frame {frame_number}: frame time rewinds across frames")
        frame_points.append(points)
        frame_times.append(packet_times)
        previous_end = float(packet_times[-1])

    if point_time_mode == "packet":
        time_chunks = [np.repeat(times, points_per_packet) - origin
                       for times in frame_times]
        counters = (0, 0, 0)
    else:
        # Session packet times in capture order, frame boundary allowed.
        session_times = np.concatenate(frame_times)
        if len(session_times) > 1:
            gaps = np.diff(session_times)
        else:
            gaps = np.empty(0, dtype=np.float64)
        time_chunks = []
        interpolated = collapsed = 0
        for k in range(len(session_times)):
            if k < len(gaps):
                gap = float(gaps[k])
            else:
                # Last packet: reuse the previous gap when positive.
                gap = float(gaps[-1]) if len(gaps) and gaps[-1] > 0 else -1.0
            if gap > 0:
                time_chunks.append(
                    session_times[k] + gap * (np.arange(points_per_packet) / points_per_packet)
                    - origin)
                if k < len(gaps):
                    interpolated += 1
            else:
                time_chunks.append(np.full(points_per_packet, session_times[k] - origin))
                if k < len(gaps):
                    collapsed += 1
        # The final packet is counted separately: it had no t_{k+1}.
        counters = (interpolated, collapsed, 1)

    points_chunks = []
    frame_indices = []
    packet_indices = []
    point_indices = []
    frame_start = []
    frame_end = []
    offsets = [0]
    packet_offset = 0
    total = 0

    for frame_number, (points, packet_times) in enumerate(zip(frame_points, frame_times)):
        points_chunks.append(points)
        frame_indices.append(np.full(len(points), frame_number, dtype=np.int32))
        packet_indices.append(np.repeat(np.arange(len(packet_times), dtype=np.int32) + packet_offset,
                                        points_per_packet))
        point_indices.append(np.tile(np.arange(points_per_packet, dtype=np.int32), len(packet_times)))
        frame_start.append(float(packet_times[0] - origin))
        frame_end.append(float(packet_times[-1] - origin))
        packet_offset += len(packet_times)
        total += len(points)
        offsets.append(total)

    points = np.concatenate(points_chunks, axis=0)
    if points.shape[1] == 3:
        points = np.column_stack([points, np.zeros(len(points), dtype=points.dtype)])
    scans = ScanSet(
        points=np.ascontiguousarray(points, dtype=np.float32),
        point_time_s=np.concatenate(time_chunks).astype(np.float64),
        frame_index=np.concatenate(frame_indices),
        packet_index=np.concatenate(packet_indices),
        point_index=np.concatenate(point_indices),
        frame_start_s=np.asarray(frame_start, dtype=np.float64),
        frame_end_s=np.asarray(frame_end, dtype=np.float64),
        frame_offsets=np.asarray(offsets, dtype=np.int64),
        time_mode=point_time_mode,
        packet_time_counters=counters,
    )
    if len(scans.point_time_s) != len(scans.points):
        raise ValueError("internal length mismatch")
    return scans


def time_quality(scans, *, max_packet_gap_s=0.2):
    """Report packet-level timing facts; never promotes them to per-point time."""
    if not isinstance(scans, ScanSet):
        raise TypeError("scans must be a ScanSet")
    if not np.isfinite(float(max_packet_gap_s)) or max_packet_gap_s <= 0:
        raise ValueError("max_packet_gap_s must be finite and positive")
    # Packet identities, rather than unique timestamps, preserve equal-time
    # packets and include the gap across each frame boundary.
    packet_starts = np.r_[True, np.diff(scans.packet_index) != 0]
    gaps = np.diff(scans.point_time_s[packet_starts])
    interpolated, collapsed, fallback = scans.packet_time_counters
    return {
        "time_quality": (PACKET_APPROXIMATION if scans.time_mode == "packet"
                         else POINT_INTERPOLATED_UNIFORM_PACKET_GAP),
        "point_time_mode": scans.time_mode,
        "packets_interpolated": int(interpolated),
        "packets_collapsed": int(collapsed),
        "packets_fallback": int(fallback),
        "frames": int(len(scans.frame_start_s)),
        "points": int(len(scans.points)),
        "session_span_s": float(scans.frame_end_s[-1] - scans.frame_start_s[0]),
        "max_packet_gap_s": float(gaps.max()) if len(gaps) else 0.0,
        "gaps_over_limit": int((gaps > max_packet_gap_s).sum()) if len(gaps) else 0,
        "points_per_packet": int(len(np.unique(scans.point_index))) if len(scans.point_index) else 0,
        "limitations": limitations_for(scans.time_mode),
    }


def save_npz(path, scans, **extra):
    """Write the scan package with ``allow_pickle=False`` compatible arrays."""
    payload = scans.as_arrays()
    payload.update(extra)
    np.savez_compressed(path, **payload)
    return path


def load_npz(path):
    """Load and validate a saved package; pickle is always disabled."""
    keys = ("points", "point_time_s", "frame_index", "packet_index", "point_index",
            "frame_start_s", "frame_end_s", "frame_offsets")
    with np.load(path, allow_pickle=False) as archive:
        missing = [key for key in keys if key not in archive]
        if missing:
            raise ValueError(f"scan package missing {','.join(missing)}")
        arrays = {key: np.array(archive[key], copy=True) for key in keys}
    count = len(arrays["points"])
    if count == 0:
        raise ValueError("scan package is empty")
    for key in ("point_time_s", "frame_index", "packet_index", "point_index"):
        if len(arrays[key]) != count:
            raise ValueError(f"{key} length mismatch")
    offsets = arrays["frame_offsets"]
    if len(offsets) < 2 or offsets[0] != 0 or offsets[-1] != count:
        raise ValueError("frame_offsets must start at 0 and end at the point count")
    if np.any(np.diff(offsets) <= 0):
        raise ValueError("frame_offsets must strictly increase")
    if np.any(np.diff(arrays["point_time_s"]) < 0):
        raise ValueError("point times must be non-decreasing")
    return arrays

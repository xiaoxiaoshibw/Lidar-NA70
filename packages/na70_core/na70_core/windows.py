"""Bounded causal packet-level time windows over the packet timeline.

This mirrors the proven W1 selection semantics (thin_wire.packet_timeline +
select_window) but accepts packets incrementally, so a replay loop does not
have to re-scan decoded frames for every window end. No future packets are
ever included; the window is always anchored at the last packet device time.

All raw identities (frame, packet, point) come from the producer, this module
never renumbers them. Filtering outputs reference packet identifiers, not
decoded points; per-ray expansion stays with the window decoders that already
exist (web/backend/thin_wire.decode_window).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


SCHEMA_VERSION = "na70-windows-v1"

# Fixed quality labels kept byte-compatible with thin_wire.select_window.
QUALITY_OK = "ok"
QUALITY_PARTIAL = "partial_window"
QUALITY_INVALID_END_TIME = "invalid_end_time"
QUALITY_INVALID_DEVICE_TIME = "invalid_device_time"
QUALITY_MIXED_TIME_SEGMENTS = "mixed_time_segments"


class PushStatus(Enum):
    ACCEPTED = "accepted"
    DEVICE_TIME_INVALID = "device_time_invalid"
    RESOURCE_LIMIT = "resource_limit"


@dataclass(frozen=True)
class PacketIdentity:
    """Identifies one NA70 packet in a capture, as produced by scan_pcap.

    Fields mirror backend.thin_wire.Packet; the two wrappers must stay
    field-compatible so a selected window from this core agrees with the
    W1 audit CLI bit for bit.
    """

    index: int
    frame_index: int
    local_index: int
    device_time: float | None
    capture_time: float
    segment: int | None
    boundary_reason: str | None

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class SelectedWindow:
    """Packets and metadata for one causal window ending at end_index."""

    window_kind: str
    packets: tuple
    end_packet_index: int
    end_frame_index: int
    end_device_time: float | None
    actual_start_device_time: float | None
    actual_span_s: float | None
    time_quality: str
    partial_window: bool
    segment_index: int | None
    segment_start_reason: str | None
    requested_duration_s: float | None
    packet_count: int
    frame_count: int

    def to_dict(self):
        return {
            **{k: v for k, v in asdict(self).items() if k != "packets"},
            "packets": [identity.to_dict() for identity in self.packets],
        }


@dataclass(frozen=True)
class RingLimits:
    """Enforced before a packet enters the buffer. Exceed -> RESOURCE_LIMIT.

    time_epsilon_s default matches W1's thin_wire_config.yaml (1e-6). Callers
    comparing against thin_wire.select_window must pass the same value, or
    simply rely on this default being the production one.
    """

    max_packets_in_ring: int
    max_window_packets: int
    max_packet_gap_s: float = 1.0
    time_epsilon_s: float = 1e-6

    def __post_init__(self):
        if self.max_packets_in_ring <= 0:
            raise ValueError("max_packets_in_ring must be positive")
        if self.max_window_packets <= 0:
            raise ValueError("max_window_packets must be positive")
        if self.max_packet_gap_s <= 0:
            raise ValueError("max_packet_gap_s must be positive")
        if self.time_epsilon_s <= 0 or self.time_epsilon_s >= self.max_packet_gap_s:
            raise ValueError("time_epsilon_s must be within (0, max_packet_gap_s)")


class PacketWindowRing:
    """Incrementally fed, bounded causal window producer.

    Feed each decoded packet once via push(); the ring tracks segment breaks
    exactly like packet_timeline (capture_start / after_invalid_device_time /
    device_backwards / device_gap) so window selection is equivalent to an
    offline one-shot pass over the same packets.
    """

    def __init__(self, limits: RingLimits):
        self.limits = limits
        self._packets: list[PacketIdentity] = []
        self._previous_time = None
        self._segment = -1
        self._next_index = 0
        # Device time of the very first valid packet of each segment; used to
        # decide partial windows even after the first packet is evicted.
        self._segment_first_time: list[float] = []

    @property
    def segment_count(self) -> int:
        return self._segment + 1

    @property
    def buffered_packet_count(self) -> int:
        return len(self._packets)

    def push(self, *, frame_index: int, local_index: int, device_time,
             capture_time: float) -> PushStatus:
        """Feed one packet; returns a bounded-status enum, never raises."""
        import math
        valid = device_time is not None and math.isfinite(device_time)
        if valid:
            reason = None
            if self._previous_time is None:
                reason = "capture_start" if self._next_index == 0 else "after_invalid_device_time"
            elif device_time < self._previous_time:
                reason = "device_backwards"
            elif device_time - self._previous_time > self.limits.max_packet_gap_s:
                reason = "device_gap"
            if reason:
                self._segment += 1
                while len(self._segment_first_time) <= self._segment:
                    self._segment_first_time.append(float(device_time))
            previous = self._previous_time
            self._previous_time = device_time
        else:
            self._previous_time = None
        if len(self._packets) >= self.limits.max_packets_in_ring:
            self._drop_oldest()
        if valid:
            identity = PacketIdentity(self._next_index, frame_index, local_index,
                                      float(device_time), float(capture_time),
                                      self._segment, reason)
        else:
            identity = PacketIdentity(self._next_index, frame_index, local_index,
                                      None, float(capture_time), None,
                                      "invalid_device_time")
        self._packets.append(identity)
        self._next_index += 1
        if not valid:
            return PushStatus.DEVICE_TIME_INVALID
        return PushStatus.ACCEPTED

    def _drop_oldest(self) -> None:
        """Evict oldest packet, keeping segment bookkeeping consistent.

        Segment numbers and `segment_first_time` are monotonic; evicted
        packets never become selectable again, so partial-window semantics
        keep using the recorded segment start even after evictions.
        """
        self._packets.pop(0)

    def packets(self) -> tuple:
        return tuple(self._packets)

    def end_index(self) -> int | None:
        if not self._packets:
            return None
        return self._packets[-1].index

    def select(self, duration=None, *, end_frame_index=None) -> SelectedWindow:
        """Choose the causal window ending at the newest packet (or a frame).

        With end_frame_index=None the window ends at the last buffered packet.
        When a frame index is supplied, selection ends at the last packet of
        that frame; later buffered packets are ignored for this call only.
        duration=None returns a single-frame window; otherwise the window is
        (end - duration, end] by packet device time within the end segment.
        """
        buffered = self._packets
        if not buffered:
            raise ValueError("select() called before any packet was pushed")
        if end_frame_index is None:
            end = buffered[-1]
            history = buffered
        else:
            matching = [p for p in buffered if p.frame_index == end_frame_index]
            if not matching:
                raise ValueError(f"end_frame_index {end_frame_index} not in ring")
            end = matching[-1]
            history = [p for p in buffered if p.index <= end.index]

        if duration is None:
            # single 100-packet logical frame semantics
            end_packets = [p for p in history if p.frame_index == end.frame_index]
            quality = QUALITY_OK
            if any(p.device_time is None for p in end_packets):
                quality = QUALITY_INVALID_DEVICE_TIME
            elif len({p.segment for p in end_packets}) != 1:
                quality = QUALITY_MIXED_TIME_SEGMENTS
            window_kind = "single_frame"
            selected = list(end_packets)
            left = None
            partial = False
        else:
            if duration <= 0:
                raise ValueError("duration must be positive or None")
            if end.device_time is None:
                return SelectedWindow("device_time", tuple(), end.index, end.frame_index,
                                      None, None, None, QUALITY_INVALID_END_TIME,
                                      True, None, None, duration, 0, 0)
            left = end.device_time - duration
            segment_packets = [p for p in history if p.segment == end.segment and p.device_time is not None]
            selected = [p for p in segment_packets if p.device_time > left]
            # W1's semantics (thin_wire.select_window) - partial iff the
            # segment's first valid device_time is already later than the
            # requested left edge. Segment boundaries are not by themselves
            # evidence of an early capture start.
            if not segment_packets:
                partial = True
            elif end.segment is not None and end.segment < len(self._segment_first_time):
                first_of_segment = self._segment_first_time[end.segment]
                partial = first_of_segment > left + self.limits.time_epsilon_s
            else:
                first_of_segment = segment_packets[0]
                partial = first_of_segment.device_time > left + self.limits.time_epsilon_s
            quality = QUALITY_PARTIAL if partial else QUALITY_OK
            window_kind = "device_time"
        self._check_window_size(len(selected))
        valid_times = [p.device_time for p in selected if p.device_time is not None]
        span = None
        if valid_times and quality not in (QUALITY_INVALID_DEVICE_TIME, QUALITY_MIXED_TIME_SEGMENTS):
            span = valid_times[-1] - valid_times[0]
        segment_start_reason = None
        if end.segment is not None:
            first_of_segment = next((p for p in buffered if p.segment == end.segment), None)
            segment_start_reason = first_of_segment.boundary_reason if first_of_segment else None
        return SelectedWindow(window_kind, tuple(selected), end.index, end.frame_index,
                              end.device_time,
                              valid_times[0] if valid_times else None,
                              span, quality, partial, end.segment,
                              segment_start_reason, duration, len(selected),
                              len({p.frame_index for p in selected}))

    def _check_window_size(self, packet_count: int) -> None:
        rays = packet_count * 200
        if packet_count > self.limits.max_window_packets:
            raise ResourceLimitError("window packets above max_window_packets; refusing")
        if rays > self.limits.max_window_packets * 200:
            raise ResourceLimitError("window rays above resource budget; refusing")


class ResourceLimitError(RuntimeError):
    """Raised when a request would exceed RingLimits; caller reports REJECTED."""

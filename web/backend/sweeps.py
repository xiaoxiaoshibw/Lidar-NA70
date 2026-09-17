"""Coverage-driven NA70 packet-to-sweep assembly.

The legacy decoder deliberately keeps its 100-packet frame contract for WebUI
compatibility.  This module is the production scan model for odometry: both an
offline PCAP adapter and a future UDP/ROS2 adapter feed the same ``SweepBuilder``.
Only packet timestamps are represented; NA70 does not provide point-level time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np
from scipy.spatial import cKDTree

SWEEP_VERSION = "na70-coverage-sweep-v1"
POINTS_PER_PACKET = 200
INVALID_ANGLE_CODE = 0xFFFF


@dataclass(frozen=True)
class SweepConfig:
    min_packets: int = 200
    min_points: int = 10_000
    max_packets: int = 1_000
    max_duration: float = 0.75
    pitch_bin_size: int = 4
    yaw_bin_size: int = 8
    target_angular_bins: int = 20_000
    min_angular_coverage: float = 0.90

    def validate(self) -> "SweepConfig":
        if self.min_packets < 1 or self.max_packets < self.min_packets:
            raise ValueError("sweep 包数范围无效")
        if self.min_points < 0 or self.max_duration <= 0:
            raise ValueError("sweep 点数或最大时间窗无效")
        if self.pitch_bin_size < 1 or self.yaw_bin_size < 1:
            raise ValueError("角码分箱尺寸必须为正整数")
        if self.target_angular_bins < 1 or not 0 < self.min_angular_coverage <= 1:
            raise ValueError("角域覆盖目标无效")
        return self


def sweep_configuration(mapping_config: dict) -> SweepConfig:
    """Build the sweep contract from the reviewable production YAML fields."""
    return SweepConfig(
        min_packets=int(mapping_config["sweep_min_packets"]),
        min_points=int(mapping_config["sweep_min_points"]),
        max_packets=int(mapping_config["sweep_max_packets"]),
        max_duration=float(mapping_config["sweep_max_duration"]),
        pitch_bin_size=int(mapping_config["sweep_pitch_bin_size"]),
        yaw_bin_size=int(mapping_config["sweep_yaw_bin_size"]),
        target_angular_bins=int(mapping_config["sweep_target_angular_bins"]),
        min_angular_coverage=float(mapping_config["sweep_min_angular_coverage"]),
    ).validate()


@dataclass(frozen=True)
class SweepPacket:
    source_index: int
    capture_time: float
    device_time: float | None
    payload: bytes
    payload_offset: int | None = None
    sequence_id: int | None = None
    factory_code: int = 0
    pitch_offset: float = 0.0
    yaw_offset: float = 0.0
    input_anomalies: tuple[dict, ...] = ()

    def __post_init__(self) -> None:
        if len(self.payload) != 1410:
            raise ValueError("NA70 packet payload must contain 1410 bytes")

    @property
    def codes(self) -> np.ndarray:
        raw = np.frombuffer(self.payload[:1400], dtype=np.uint8).reshape(-1, 7)
        result = np.empty((POINTS_PER_PACKET, 4), dtype=np.uint16)
        result[:, 0] = (raw[:, 0].astype(np.uint16) << 8) | raw[:, 1]
        result[:, 1] = (raw[:, 2].astype(np.uint16) << 8) | raw[:, 3]
        result[:, 2] = (raw[:, 4].astype(np.uint16) << 8) | raw[:, 5]
        result[:, 3] = raw[:, 6]
        return result

    @property
    def fingerprint(self) -> str:
        return hashlib.blake2b(self.payload, digest_size=8).hexdigest()


@dataclass
class Sweep:
    index: int
    packets: tuple[SweepPacket, ...]
    completion_reason: str
    complete: bool
    anomalies: tuple[dict, ...] = ()
    _codes: np.ndarray | None = field(default=None, init=False, repr=False)

    @property
    def codes(self) -> np.ndarray:
        if self._codes is None:
            self._codes = np.concatenate([packet.codes for packet in self.packets])
        return self._codes

    @property
    def valid_mask(self) -> np.ndarray:
        codes = self.codes
        return ((codes[:, 0] > 0) & (codes[:, 1] != INVALID_ANGLE_CODE)
                & (codes[:, 2] != INVALID_ANGLE_CODE))

    def points(self, install_direction: int = 1, valid_only: bool = False) -> np.ndarray:
        if install_direction not in (1, 2):
            raise ValueError("安装方向必须为 1 或 2")
        codes = self.codes
        distance = codes[:, 0].astype(np.float32) * np.float32(16) / np.float32(1000)
        pitch = codes[:, 1].astype(np.float32) * np.float32(0.0258) * np.float32(np.pi / 180)
        yaw = codes[:, 2].astype(np.float32) * np.float32(0.0432) * np.float32(np.pi / 180)
        x = distance * np.cos(yaw) * np.cos(pitch)
        y = distance * np.sin(yaw) * np.cos(pitch)
        z = distance * np.sin(pitch)
        xyz = np.column_stack((z, -x, -y) if install_direction == 1 else (z, x, y))
        p = np.float32(self.packets[-1].pitch_offset) * np.float32(np.pi / 180)
        w = np.float32(self.packets[-1].yaw_offset) * np.float32(np.pi / 180)
        cp, sp, cw, sw = np.cos(p), np.sin(p), np.cos(w), np.sin(w)
        rotation = np.array([
            [cp * cw, -cp * sw, sp],
            [sw, cw, 0],
            [-sp * cw, sp * sw, cp],
        ], dtype=np.float32)
        if install_direction == 2:
            rotation = np.diag(np.array([1, -1, -1], dtype=np.float32)) @ rotation
        result = np.empty((len(codes), 4), dtype="<f4")
        result[:, :3] = xyz @ rotation.T
        result[:, 3] = codes[:, 3]
        return result[self.valid_mask] if valid_only else result

    @property
    def start_time(self) -> float:
        return self.packets[0].capture_time

    @property
    def end_time(self) -> float:
        return self.packets[-1].capture_time

    def angular_bins(self, config: SweepConfig) -> np.ndarray:
        codes = self.codes[self.valid_mask]
        if not len(codes):
            return np.empty((0, 2), dtype=np.int32)
        bins = np.column_stack((codes[:, 1] // config.pitch_bin_size,
                                codes[:, 2] // config.yaw_bin_size))
        return np.unique(bins, axis=0)

    def metadata(self, config: SweepConfig, include_packets: bool = True) -> dict:
        valid = self.codes[self.valid_mask]
        bins = self.angular_bins(config)
        payload = dict(
            schema_version=1,
            sweep_version=SWEEP_VERSION,
            index=self.index,
            start_time=self.start_time,
            end_time=self.end_time,
            duration=max(0.0, self.end_time - self.start_time),
            packet_count=len(self.packets),
            point_count=len(self.codes),
            valid_point_count=len(valid),
            angular_bin_count=len(bins),
            angular_coverage=min(1.0, len(bins) / config.target_angular_bins),
            completion_reason=self.completion_reason,
            complete=self.complete,
            anomalies=list(self.anomalies),
            point_time_semantics="packet_time_only",
        )
        if len(valid):
            payload["pitch_code_range"] = [int(valid[:, 1].min()), int(valid[:, 1].max())]
            payload["yaw_code_range"] = [int(valid[:, 2].min()), int(valid[:, 2].max())]
        else:
            payload["pitch_code_range"] = None
            payload["yaw_code_range"] = None
        if include_packets:
            point_start = 0
            packet_rows = []
            for packet in self.packets:
                packet_rows.append(dict(
                    source_index=packet.source_index,
                    sequence_id=packet.sequence_id,
                    capture_time=packet.capture_time,
                    device_time=packet.device_time,
                    payload_offset=packet.payload_offset,
                    point_start=point_start,
                    point_count=POINTS_PER_PACKET,
                    factory_code=packet.factory_code,
                    pitch_offset=packet.pitch_offset,
                    yaw_offset=packet.yaw_offset,
                    input_anomalies=list(packet.input_anomalies),
                ))
                point_start += POINTS_PER_PACKET
            payload["packets"] = packet_rows
        return payload


class SweepBuilder:
    """Stateful builder shared by batch PCAP and streaming packet inputs."""

    def __init__(self, config: SweepConfig | None = None):
        self.config = (config or SweepConfig()).validate()
        self._packets: list[SweepPacket] = []
        self._bins: set[tuple[int, int]] = set()
        self._valid_points = 0
        self._anomalies: list[dict] = []
        self._next_index = 0
        self._last_packet: SweepPacket | None = None

    def _record_packet(self, packet: SweepPacket) -> None:
        self._packets.append(packet)
        codes = packet.codes
        valid = ((codes[:, 0] > 0) & (codes[:, 1] != INVALID_ANGLE_CODE)
                 & (codes[:, 2] != INVALID_ANGLE_CODE))
        self._valid_points += int(valid.sum())
        if np.any(valid):
            quantized = np.column_stack((codes[valid, 1] // self.config.pitch_bin_size,
                                         codes[valid, 2] // self.config.yaw_bin_size))
            self._bins.update(map(tuple, quantized.tolist()))
        self._anomalies.extend(packet.input_anomalies)

    def _finish(self, reason: str, complete: bool) -> Sweep | None:
        if not self._packets:
            return None
        sweep = Sweep(self._next_index, tuple(self._packets), reason, complete,
                      tuple(self._anomalies))
        self._next_index += 1
        self._packets = []
        self._bins = set()
        self._valid_points = 0
        self._anomalies = []
        return sweep

    def add(self, packet: SweepPacket) -> list[Sweep]:
        emitted: list[Sweep] = []
        previous = self._last_packet
        if previous is not None:
            repeated_sequence = (packet.sequence_id is not None
                                 and packet.sequence_id == previous.sequence_id)
            repeated_payload = (packet.capture_time == previous.capture_time
                                and packet.device_time == previous.device_time
                                and packet.fingerprint == previous.fingerprint)
            if repeated_sequence or repeated_payload:
                self._anomalies.append(dict(
                    kind="duplicate_packet", source_index=packet.source_index,
                    sequence_id=packet.sequence_id,
                ))
                return emitted
            if (packet.sequence_id is not None and previous.sequence_id is not None
                    and packet.sequence_id > previous.sequence_id + 1):
                self._anomalies.append(dict(
                    kind="packet_gap", source_index=packet.source_index,
                    missing=packet.sequence_id - previous.sequence_id - 1,
                ))
            if packet.capture_time < previous.capture_time:
                self._anomalies.append(dict(
                    kind="capture_backwards", source_index=packet.source_index,
                    time=packet.capture_time, previous=previous.capture_time,
                ))
                sweep = self._finish("capture_time_backwards", False)
                if sweep is not None:
                    emitted.append(sweep)
            if (packet.device_time is None
                    or (previous.device_time is not None and packet.device_time < previous.device_time)):
                kind = "device_invalid" if packet.device_time is None else "device_backwards"
                if not any(item.get("kind") == kind for item in packet.input_anomalies):
                    self._anomalies.append(dict(
                        kind=kind, source_index=packet.source_index, time=packet.device_time,
                        previous=previous.device_time,
                    ))
        self._record_packet(packet)
        self._last_packet = packet

        duration = packet.capture_time - self._packets[0].capture_time
        enough = (len(self._packets) >= self.config.min_packets
                  and self._valid_points >= self.config.min_points)
        coverage = len(self._bins) / self.config.target_angular_bins
        if enough and coverage >= self.config.min_angular_coverage:
            sweep = self._finish("angular_coverage", True)
        elif duration >= self.config.max_duration:
            sweep = self._finish("max_time_window", False)
        elif len(self._packets) >= self.config.max_packets:
            sweep = self._finish("max_packet_count", False)
        else:
            sweep = None
        if sweep is not None:
            emitted.append(sweep)
        return emitted

    def finish(self) -> list[Sweep]:
        sweep = self._finish("end_of_stream", False)
        return [sweep] if sweep is not None else []

    def build(self, packets: Iterable[SweepPacket]) -> list[Sweep]:
        result = []
        for packet in packets:
            result.extend(self.add(packet))
        result.extend(self.finish())
        return result


def packets_from_scan(path: Path, frames: Iterable[dict]) -> Iterator[SweepPacket]:
    """Adapt a freshly indexed PCAP to packets without inventing timestamps."""
    path = Path(path)
    with path.open("rb") as source:
        for frame in frames:
            required = ("packet_capture_times", "packet_device_times", "packet_factory_codes",
                        "packet_pitch_offsets", "packet_yaw_offsets")
            if any(key not in frame for key in required):
                raise ValueError("帧索引缺少包级时间；请使用当前 decoder 重新索引 PCAP")
            anomalies_by_packet: dict[int, list[dict]] = {}
            for anomaly in frame.get("packet_time_anomalies", []):
                anomalies_by_packet.setdefault(int(anomaly["packet"]), []).append(anomaly)
            start = int(frame.get("packet_start_index", 0))
            for local_index, offset in enumerate(frame["offsets"]):
                source.seek(int(offset))
                payload = source.read(1410)
                if len(payload) != 1410:
                    raise ValueError("PCAP payload 已截断")
                yield SweepPacket(
                    source_index=start + local_index,
                    # NA70 exposes no on-wire sequence field in this payload.
                    # Source order is retained separately and must not be
                    # misrepresented as a loss-detecting device sequence.
                    sequence_id=None,
                    capture_time=float(frame["packet_capture_times"][local_index]),
                    device_time=frame["packet_device_times"][local_index],
                    payload=payload,
                    payload_offset=int(offset),
                    factory_code=int(frame["packet_factory_codes"][local_index]),
                    pitch_offset=float(frame["packet_pitch_offsets"][local_index]),
                    yaw_offset=float(frame["packet_yaw_offsets"][local_index]),
                    input_anomalies=tuple(anomalies_by_packet.get(local_index, ())),
                )


def build_sweeps(path: Path, frames: Iterable[dict], config: SweepConfig | None = None) -> list[Sweep]:
    return SweepBuilder(config).build(packets_from_scan(path, frames))


def correspondence_coverage(reference: np.ndarray, query: np.ndarray,
                            distances=(0.3, 0.6, 1.0), max_points: int = 12_000) -> dict:
    """Deterministic symmetric nearest-neighbour coverage for diagnostics."""
    if not len(reference) or not len(query):
        return {str(value): 0.0 for value in distances}

    def sample(points):
        step = max(1, len(points) // max_points)
        return points[::step][:max_points]

    reference = sample(reference)
    query = sample(query)
    forward = cKDTree(reference).query(query, k=1, workers=1)[0]
    backward = cKDTree(query).query(reference, k=1, workers=1)[0]
    return {
        str(value): round(float(((forward <= value).mean() + (backward <= value).mean()) / 2), 6)
        for value in distances
    }

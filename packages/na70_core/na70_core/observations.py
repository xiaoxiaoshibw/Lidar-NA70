"""NA70 decoded-ray contract. Protocol decoding remains in backend.decoder.

There is no per-ray device timestamp in classic PCAP: each of the 200 rays in
a packet carries the same measured packet time. An absent direction is UNKNOWN,
not a confirmed miss of a wire or any other target.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

import numpy as np


SCHEMA_VERSION = "na70-ray-batch-v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def direction_known_from_codes(codes: np.ndarray) -> np.ndarray:
    """Return a mask of rays with a recoverable direction.

    A valid nonzero distance fixes the direction even when both angle codes are
    zero. A zero-distance record with both angle codes zero has no direction.
    """
    data = np.asarray(codes)
    if data.ndim != 2 or data.shape[1] != 4 or data.dtype.kind not in "iu":
        raise ValueError("codes must be an integer Nx4 array")
    return (data[:, 0] != 0) | (data[:, 1] != 0) | (data[:, 2] != 0)


def _readonly(value, dtype, shape, name):
    array = np.array(value, dtype=dtype, copy=True)
    if array.ndim != len(shape) or any(size is not None and array.shape[i] != size
                                       for i, size in enumerate(shape)):
        raise ValueError(f"{name} has invalid shape")
    array.setflags(write=False)
    return array


@dataclass(frozen=True)
class RayBatch:
    """Immutable raw-ray-aligned observation with explicit capture identity.

    Each array has one entry per source ray, including zero-distance records.
    The three source indices are never renumbered after filtering. Array copies
    are intentional so a producer cannot mutate a published observation.
    """

    capture_sha256: str
    coordinate_frame: str
    install_direction: int
    codes: np.ndarray
    points: np.ndarray
    frame_indices: np.ndarray
    packet_indices: np.ndarray
    point_indices: np.ndarray
    device_times: np.ndarray
    capture_times: np.ndarray
    valid_return: np.ndarray
    direction_known: np.ndarray
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_decoded(cls, *, capture_sha256: str, coordinate_frame: str,
                     install_direction: int, codes, points, frame_indices,
                     packet_indices, point_indices, device_times, capture_times):
        if not isinstance(capture_sha256, str) or not _SHA256.fullmatch(capture_sha256):
            raise ValueError("capture_sha256 must be lowercase SHA-256")
        if not coordinate_frame or not isinstance(coordinate_frame, str):
            raise ValueError("coordinate_frame is required")
        if type(install_direction) is not int or install_direction not in (1, 2):
            raise ValueError("install_direction must be 1 or 2")
        raw_codes = np.asarray(codes)
        if raw_codes.ndim != 2 or raw_codes.shape[1] != 4 or raw_codes.dtype.kind not in "iu":
            raise ValueError("codes must be an integer Nx4 array")
        if np.any(raw_codes < 0) or np.any(raw_codes[:, :3] > 65535) or np.any(raw_codes[:, 3] > 255):
            raise ValueError("raw codes are outside NA70 bounds")
        n = len(raw_codes)
        raw_points = np.asarray(points)
        if raw_points.shape != (n, 4) or raw_points.dtype.kind != "f":
            raise ValueError("points must be a floating Nx4 array")
        ids = {}
        for name, source in (("frame_indices", frame_indices),
                             ("packet_indices", packet_indices),
                             ("point_indices", point_indices)):
            data = np.asarray(source)
            if data.shape != (n,) or data.dtype.kind not in "iu" or np.any(data < 0):
                raise ValueError(f"{name} must be nonnegative integer ray-aligned IDs")
            ids[name] = _readonly(data, np.int64, (n,), name)
        if np.any(ids["point_indices"] >= 20_000):
            raise ValueError("point_indices must be frame-local NA70 ray indices")
        device = np.asarray(device_times)
        captured = np.asarray(capture_times)
        if device.shape != (n,) or device.dtype.kind != "f" or np.isinf(device).any():
            raise ValueError("device_times must be ray-aligned floats or NaN")
        if captured.shape != (n,) or captured.dtype.kind != "f" or not np.isfinite(captured).all():
            raise ValueError("capture_times must be finite ray-aligned floats")
        direction = direction_known_from_codes(raw_codes)
        valid = (raw_codes[:, 0] != 0) & np.isfinite(raw_points).all(axis=1)
        return cls(
            capture_sha256=capture_sha256, coordinate_frame=coordinate_frame,
            install_direction=install_direction,
            codes=_readonly(raw_codes, np.uint16, (n, 4), "codes"),
            points=_readonly(raw_points, np.float32, (n, 4), "points"),
            frame_indices=ids["frame_indices"], packet_indices=ids["packet_indices"],
            point_indices=ids["point_indices"],
            device_times=_readonly(device, np.float64, (n,), "device_times"),
            capture_times=_readonly(captured, np.float64, (n,), "capture_times"),
            valid_return=_readonly(valid, np.bool_, (n,), "valid_return"),
            direction_known=_readonly(direction, np.bool_, (n,), "direction_known"),
        )

    @property
    def ray_count(self) -> int:
        return len(self.codes)

    def source_refs(self, mask: np.ndarray) -> np.ndarray:
        """Return frame, packet and frame-local point ID for a selected subset."""
        selected = np.asarray(mask)
        if selected.shape != (self.ray_count,) or selected.dtype != np.bool_:
            raise ValueError("mask must be a ray-aligned boolean array")
        return np.column_stack((self.frame_indices[selected],
                                self.packet_indices[selected],
                                self.point_indices[selected]))

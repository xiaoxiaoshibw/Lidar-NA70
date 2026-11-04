"""Shared NA70 angular transform used by the formal decoder and S0 audit.

The ROS2 driver remains the protocol authority. Raw code extraction and PCAP
framing stay in backend.decoder; this module owns only the coordinate math.
"""
from __future__ import annotations

import numpy as np


def _xyz(codes: np.ndarray, distance: np.ndarray, install_direction: int,
         pitch_offset_deg: float, yaw_offset_deg: float) -> np.ndarray:
    if install_direction not in (1, 2):
        raise ValueError("install_direction must be 1 or 2")
    codes = np.asarray(codes)
    if codes.ndim != 2 or codes.shape[1] != 4 or codes.dtype.kind not in "iu":
        raise ValueError("codes must be an integer Nx4 array")
    pitch = codes[:, 1].astype(np.float32) * np.float32(0.0258) * np.float32(np.pi) / np.float32(180)
    yaw = codes[:, 2].astype(np.float32) * np.float32(0.0432) * np.float32(np.pi) / np.float32(180)
    return xyz_from_angles(distance, pitch, yaw, install_direction,
                           pitch_offset_deg, yaw_offset_deg)


def xyz_from_angles(distance: np.ndarray, pitch: np.ndarray, yaw: np.ndarray,
                    install_direction: int, pitch_offset_deg: float,
                    yaw_offset_deg: float) -> np.ndarray:
    """Apply the driver's coordinate and factory-offset transform to radians."""
    if install_direction not in (1, 2):
        raise ValueError("install_direction must be 1 or 2")
    if np.shape(distance) != np.shape(pitch) or np.shape(distance) != np.shape(yaw):
        raise ValueError("distance, pitch and yaw must have matching shapes")
    x = distance * np.cos(yaw) * np.cos(pitch)
    y = distance * np.sin(yaw) * np.cos(pitch)
    z = distance * np.sin(pitch)
    xyz = np.column_stack((z, -x, -y) if install_direction == 1 else (z, x, y))
    p = np.float32(pitch_offset_deg) * np.float32(np.pi) / np.float32(180)
    w = np.float32(yaw_offset_deg) * np.float32(np.pi) / np.float32(180)
    cp, sp, cw, sw = np.cos(p), np.sin(p), np.cos(w), np.sin(w)
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]], dtype=np.float32)
    rz = np.array([[cw, -sw, 0], [sw, cw, 0], [0, 0, 1]], dtype=np.float32)
    rotation = ry @ rz
    if install_direction == 2:
        a = np.float32(np.pi)
        rx = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)],
                       [0, np.sin(a), np.cos(a)]], dtype=np.float32)
        rotation = rx @ rotation
    return xyz @ rotation.T


def points_from_codes(codes: np.ndarray, pitch_offset_deg: float,
                      yaw_offset_deg: float, install_direction: int = 1) -> np.ndarray:
    """Return formal XYZI points from the decoder's raw Nx4 code matrix."""
    codes = np.asarray(codes)
    if codes.ndim != 2 or codes.shape[1] != 4 or codes.dtype.kind not in "iu":
        raise ValueError("codes must be an integer Nx4 array")
    distance = codes[:, 0].astype(np.float32) * np.float32(16) / np.float32(1000)
    points = np.empty((len(codes), 4), dtype="<f4")
    points[:, :3] = _xyz(codes, distance, install_direction,
                         pitch_offset_deg, yaw_offset_deg)
    points[:, 3] = codes[:, 3]
    return points


def directions_from_codes(codes: np.ndarray, pitch_offset_deg: float,
                          yaw_offset_deg: float, install_direction: int = 1) -> np.ndarray:
    """Return unit directions only where raw angle codes are recoverable.

    Callers must separately apply direction_known_from_codes: a zero-distance,
    zero-angle record cannot be claimed as a measured outgoing direction.
    """
    data = np.asarray(codes)
    if data.ndim != 2 or data.shape[1] != 4 or data.dtype.kind not in "iu":
        raise ValueError("codes must be an integer Nx4 array")
    return _xyz(data, np.ones(len(data), dtype=np.float32), install_direction,
                pitch_offset_deg, yaw_offset_deg)

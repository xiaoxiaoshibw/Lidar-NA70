"""Stage: forward-projection Hough line proposals (W4-A contender).

Valid returns are projected into a normalised forward-angle image
(u = atan2(y,|x|), v = atan2(z,|x|) degrees). HoughLinesP runs on the
occupancy image; every 2-D segment's pixel range is mapped back to the
rays that produced it, and those rays become one Proposal.

Determinism: HoughLinesP is run on a single fixed-seed occupancy image with
no random augmentation; OpenCV's implementation is deterministic for a
given image + parameters. OpenCV is imported lazily so headless/test
environments without it can still use the rest of the package.

Per NA70_WIRE_ALGORITHMS §5, this baseline must be validated against
propose_full_fov on the frozen SYNTHETIC + reviewed sets before replacing
any incumbent behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..observations import RayBatch
from .proposals import Proposal


@dataclass(frozen=True)
class HoughConfig:
    bin_deg: float = 0.5
    u_range_deg: tuple = (-45.0, 45.0)
    v_range_deg: tuple = (-30.0, 30.0)
    canny_threshold1: int = 50
    canny_threshold2: int = 150
    hough_threshold: int = 10
    min_line_length_px: int = 6
    max_line_gap_px: int = 3
    dilate_kernel: int = 0  # 0 = no dilation; >0 -> single (k,k) dilation pass
    max_proposals: int = 32


def validate_hough_config(cfg: HoughConfig) -> HoughConfig:
    if cfg.bin_deg <= 0:
        raise ValueError("bin_deg must be positive")
    if len(cfg.u_range_deg) != 2 or cfg.u_range_deg[0] >= cfg.u_range_deg[1]:
        raise ValueError("u_range_deg must be (min,max) with min < max")
    if len(cfg.v_range_deg) != 2 or cfg.v_range_deg[0] >= cfg.v_range_deg[1]:
        raise ValueError("v_range_deg must be (min,max) with min < max")
    for key in ("canny_threshold1", "canny_threshold2", "hough_threshold",
                "min_line_length_px", "max_line_gap_px", "max_proposals"):
        value = getattr(cfg, key)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{key} must be a positive integer")
    if isinstance(cfg.dilate_kernel, bool) or not isinstance(cfg.dilate_kernel, int) \
            or cfg.dilate_kernel < 0:
        raise ValueError("dilate_kernel must be a non-negative integer")
    return cfg


def propose_hough(batch: RayBatch, cfg: HoughConfig) -> tuple:
    cfg = validate_hough_config(cfg)
    valid = batch.valid_return & batch.direction_known
    if not valid.any():
        return tuple()

    import cv2  # lazy, optional dependency

    xyz = batch.points[valid, :3]
    x_abs = np.maximum(np.abs(xyz[:, 0]), 1e-6)
    u_deg = np.degrees(np.arctan2(xyz[:, 1], x_abs))
    v_deg = np.degrees(np.arctan2(xyz[:, 2], x_abs))

    u_min, u_max = cfg.u_range_deg
    v_min, v_max = cfg.v_range_deg
    width = max(1, int(np.ceil((u_max - u_min) / cfg.bin_deg)))
    height = max(1, int(np.ceil((v_max - v_min) / cfg.bin_deg)))
    px_u = np.clip(((u_deg - u_min) / cfg.bin_deg).astype(np.int64), 0, width - 1)
    px_v = np.clip(((v_deg - v_min) / cfg.bin_deg).astype(np.int64), 0, height - 1)

    keep = (u_deg >= u_min) & (u_deg < u_max) & (v_deg >= v_min) & (v_deg < v_max)
    if not keep.any():
        return tuple()
    image = np.zeros((height, width), dtype=np.uint8)
    image[px_v[keep], px_u[keep]] = 255
    if cfg.dilate_kernel > 0:
        kernel = np.ones((cfg.dilate_kernel, cfg.dilate_kernel), dtype=np.uint8)
        image = cv2.dilate(image, kernel, iterations=1)
    edged = cv2.Canny(image, cfg.canny_threshold1, cfg.canny_threshold2)
    segments = cv2.HoughLinesP(edged, rho=1, theta=np.pi / 180.0,
                               threshold=cfg.hough_threshold,
                               minLineLength=cfg.min_line_length_px,
                               maxLineGap=cfg.max_line_gap_px)
    if segments is None:
        return tuple()

    batch_positions = np.flatnonzero(valid)[keep]
    u_coords = px_u[keep]
    v_coords = px_v[keep]
    proposals = []
    for line in segments[: cfg.max_proposals]:
        x1, y1, x2, y2 = (int(v) for v in line[0])
        du, dv = x2 - x1, y2 - y1
        line_length = float(np.hypot(du, dv))
        if line_length == 0:
            continue
        tu = (u_coords - x1) * du + (v_coords - y1) * dv
        along = tu / line_length
        within = (along >= 0.0) & (along <= line_length)
        if not within.any():
            continue
        close_x = x1 + along * du
        close_y = y1 + along * dv
        distance = np.hypot(u_coords - close_x, v_coords - close_y)
        sources = within & (distance <= 1.0)
        if not sources.any():
            continue
        member = batch_positions[sources]
        pts = xyz[keep][sources]
        member_mask = np.zeros(batch.ray_count, dtype=bool)
        member_mask[member] = True
        amin = pts.min(axis=0)
        amax = pts.max(axis=0)
        proposals.append(Proposal(kind="hough_line",
                                  aabb_m=np.array([amin[0], amax[0],
                                                   amin[1], amax[1],
                                                   amin[2], amax[2]]),
                                  mask=member_mask))
    return tuple(proposals)

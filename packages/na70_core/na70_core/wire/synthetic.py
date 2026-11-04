"""Deterministic SYNTHETIC wire-scene fixtures for the wire pipeline.

Everything produced here is SYNTHETIC: scenes may test code behaviour only
and never enter real TP / range / detection-rate denominators (mirrors the
goal_detection.synthetic fixture precedent). Scenes are built directly as a
RayBatch - no PCAP roundtrip - with NA70-shaped identities: zero-distance
filler rays (direction unknown), packet-shared device times, and global
(frame, packet, point) ids.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

import numpy as np

from ..observations import RayBatch


SCHEMA_VERSION = "na70-wire-synthetic-v1"


@dataclass(frozen=True)
class SyntheticScene:
    name: str
    batch: RayBatch
    roi_aabb_m: tuple
    truth: dict
    expect: dict  # behavioural hints verified by the runner / tests


def wire_segment(p0, p1, spacing_m, rng, sag_m=0.0, drop_interval=None,
                 noise_m=0.02) -> np.ndarray:
    """Sample a straight wire; optional parabolic sag and dropped middle span."""
    p0 = np.asarray(p0, dtype=float)
    p1 = np.asarray(p1, dtype=float)
    length = float(np.linalg.norm(p1 - p0))
    n = max(2, int(length / spacing_m) + 1)
    t = np.linspace(0.0, 1.0, n)
    pts = p0[None, :] + t[:, None] * (p1 - p0)[None, :]
    if sag_m:
        pts[:, 2] -= sag_m * 4.0 * t * (1.0 - t)
    if drop_interval is not None:
        keep = (t < drop_interval[0]) | (t > drop_interval[1])
        pts = pts[keep]
    if noise_m:
        pts = pts + rng.normal(0.0, noise_m, pts.shape)
    return pts


def tree_blobs(centers, per_blob, sigma_m, rng) -> np.ndarray:
    chunks = [rng.normal(c, sigma_m, (per_blob, 3)) for c in centers]
    return np.vstack(chunks) if chunks else np.empty((0, 3))


def ground_grid(x_range, y_range, step_m, rng, noise_m=0.03) -> np.ndarray:
    xs = np.arange(x_range[0], x_range[1] + 1e-6, step_m)
    ys = np.arange(y_range[0], y_range[1] + 1e-6, step_m)
    gx, gy = np.meshgrid(xs, ys)
    pts = np.column_stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)])
    return pts + rng.normal(0.0, noise_m, pts.shape)


def _assemble(name, elements, roi, truth, expect, *, seed: int,
              frames: int = 16, packets_per_frame: int = 25,
              time_jump_after_packet: int | None = None,
              time_jump_seconds: float = 1.5) -> SyntheticScene:
    """Distribute labelled scene points deterministically across ray slots.

    Element points are shuffled with the scenario seed and written into
    consecutive packets, one ray per slot; remaining slots stay zero-distance
    (invalid, direction unknown), mirroring the real PCAP return structure.
    """
    rng = np.random.default_rng(seed)
    labelled = []
    for label, pts in elements:
        pts = np.asarray(pts, dtype=float).reshape(-1, 3)
        for row in pts:
            labelled.append((label, row))
    order = rng.permutation(len(labelled)) if labelled else np.array([], dtype=int)
    labelled = [labelled[i] for i in order]

    packets_total = frames * packets_per_frame
    rays_total = packets_total * 200
    if len(labelled) > rays_total:
        raise ValueError(f"scene {name} overflows ray budget")
    codes = np.zeros((rays_total, 4), dtype=np.uint16)
    points = np.zeros((rays_total, 4), dtype=np.float32)
    labels = np.array(["invalid"] * rays_total, dtype=object)
    assignments = rng.choice(rays_total, size=len(labelled), replace=False) \
        if labelled else np.array([], dtype=int)
    for assignment, (label, xyz) in zip(assignments, labelled):
        r = float(np.linalg.norm(xyz))
        distance_code = int(min(65535, max(1, round(r / 0.016))))
        codes[assignment] = (distance_code, 512 + (assignment % 7), assignment % 3,
                             int(rng.integers(20, 90)))
        points[assignment, :3] = xyz.astype(np.float32)
        points[assignment, 3] = codes[assignment, 3]
        labels[assignment] = label

    frame_indices = np.repeat(np.arange(frames), packets_per_frame * 200).astype(np.int64)
    packet_indices = np.repeat(np.arange(packets_total), 200).astype(np.int64)
    point_indices = np.tile(np.arange(packets_per_frame * 200), frames).astype(np.int64)

    device_per_packet = 0.001 * np.arange(packets_total, dtype=float)
    if time_jump_after_packet is not None:
        device_per_packet[time_jump_after_packet:] += time_jump_seconds
    device_times = np.repeat(device_per_packet, 200)
    capture_times = device_times.copy()

    sha = hashlib.sha256(f"{SCHEMA_VERSION}:{name}:{seed}".encode()).hexdigest()
    batch = RayBatch.from_decoded(
        capture_sha256=sha, coordinate_frame="lidar_link", install_direction=1,
        codes=codes, points=points, frame_indices=frame_indices,
        packet_indices=packet_indices, point_indices=point_indices,
        device_times=device_times, capture_times=capture_times)
    return SyntheticScene(name=name, batch=batch, roi_aabb_m=roi,
                          truth={**truth, "labels_per_ray": None, "schema": SCHEMA_VERSION},
                          expect=expect)


def single_wire_50m(seed: int = 11) -> SyntheticScene:
    rng = np.random.default_rng(seed)
    wire = wire_segment((50, -15, 8), (50, 15, 8), 0.3, rng)
    elements = [("wire", wire), ("ground", ground_grid((40, 60), (-20, 20), 4.0, rng))]
    return _assemble("single_wire_50m", elements,
                     (45, 55, -20, 20, 5, 12),
                     {"wire": {"endpoints": [[50, -15, 8], [50, 15, 8]], "identity": "wire"}},
                     {"status_in": ("MEASURED_CURRENT",), "nearest_m_approx": 50.0},
                     seed=seed)


def sag_wire_100m(seed: int = 12) -> SyntheticScene:
    rng = np.random.default_rng(seed)
    wire = wire_segment((100, -20, 10), (100, 20, 10), 0.4, rng, sag_m=0.8)
    return _assemble("sag_wire_100m", [("wire", wire)],
                     (95, 105, -25, 25, 6, 14),
                     {"wire": {"endpoints": [[100, -20, 10], [100, 20, 10]], "sag_m": 0.8}},
                     {"status_in": ("MEASURED_CURRENT",)},
                     seed=seed)


def sparse_wire_200m(seed: int = 13) -> SyntheticScene:
    rng = np.random.default_rng(seed)
    wire = wire_segment((200, -4, 18), (200, 4, 18), 0.7, rng)
    sub = wire[rng.choice(len(wire), size=12, replace=False)]
    return _assemble("sparse_wire_200m", [("wire", sub)],
                     (195, 205, -10, 10, 14, 22),
                     {"wire": {"support_points": int(len(sub)), "distance_m": 200}},
                     {"status_in": ("MEASURED_CURRENT",)},
                     seed=seed)


def ultra_sparse_wire_200m(seed: int = 14) -> SyntheticScene:
    rng = np.random.default_rng(seed)
    wire = wire_segment((200, -4, 18), (200, 4, 18), 0.7, rng)
    sub = wire[rng.choice(len(wire), size=3, replace=False)]
    return _assemble("ultra_sparse_wire_200m", [("wire", sub)],
                     (195, 205, -10, 10, 14, 22),
                     {"wire": {"support_points": int(len(sub)), "distance_m": 200}},
                     {"status_in": ("MEASURED_CURRENT", "UNKNOWN"),
                      "reject_if_unknown": "INSUFFICIENT_SUPPORT"},
                     seed=seed)


def trees_only_negative(seed: int = 15) -> SyntheticScene:
    rng = np.random.default_rng(seed)
    centers = [(60, y, 8 + 2 * (i % 3)) for i, y in enumerate((-12, -4, 0, 6, 14, 20))]
    trees = tree_blobs(centers, 30, 2.5, rng)
    return _assemble("trees_only_negative", [("tree", trees)],
                     (50, 70, -20, 25, 5, 15),
                     {"tree": {"blobs": len(centers)}, "wire": None},
                     # Weakness #1 (phantom line, locked 2026-10-03). The
                     # RANSAC inliers here are ~12 canopy-top points that
                     # span 34 m -> density < 1/m -> SPARSE_SUPPORT gate
                     # (replaces the insufficient per-bin lateral gate).
                     {"status_not_in": ("MEASURED_CURRENT",),
                      "reject_if_unknown": "SPARSE_SUPPORT"},
                     seed=seed)


def fence_rails_line_negative(seed: int = 16) -> SyntheticScene:
    rng = np.random.default_rng(seed)
    rail1 = wire_segment((30, -30, 1.0), (30, 0, 1.0), 0.25, rng)
    rail2 = wire_segment((30, 4, 1.0), (30, 30, 1.0), 0.25, rng)
    return _assemble("fence_rails_line_negative",
                     [("fence", rail1), ("fence", rail2)],
                     (25, 35, -35, 35, 0.2, 2.5),
                     {"fence": {"identity": "fence_rail", "note": "geometry is a true line but not a wire"}},
                     {"status_in": ("MEASURED_CURRENT",),
                      "truth_identity": "fence_rail"},
                     seed=seed)


def building_edge_180m(seed: int = 17) -> SyntheticScene:
    rng = np.random.default_rng(seed)
    edge = wire_segment((180, -30, 12), (180, 30, 12.6), 0.5, rng)
    facade = rng.uniform((175, -30, 3), (185, 30, 11), (120, 3))
    return _assemble("building_edge_180m",
                     [("building", edge), ("building", facade)],
                     (170, 190, -35, 35, 8, 16),
                     {"building": {"identity": "roof_eave"}},
                     {"status_in": ("MEASURED_CURRENT",), "truth_identity": "roof_eave"},
                     seed=seed)


def crossing_wires(seed: int = 18) -> SyntheticScene:
    rng = np.random.default_rng(seed)
    wire_a = wire_segment((80, -20, 8), (80, 20, 8), 0.3, rng)
    wire_b = wire_segment((78, -14, 11), (82, 14, 5), 0.6, rng)
    return _assemble("crossing_wires",
                     [("wire_a", wire_a), ("wire_b", wire_b)],
                     (72, 88, -25, 25, 3, 14),
                     {"wire_a": {"endpoints": [[80, -20, 8], [80, 20, 8]]},
                      "wire_b": {"endpoints": [[78, -14, 11], [82, 14, 5]]}},
                     {"status_in": ("MEASURED_CURRENT",)},
                     seed=seed)


def occluded_wire_gap60(seed: int = 19) -> SyntheticScene:
    rng = np.random.default_rng(seed)
    wire = wire_segment((120, -100, 9), (120, 100, 9), 0.4, rng,
                        drop_interval=(0.45, 0.75))
    return _assemble("occluded_wire_gap60", [("wire", wire)],
                     (115, 125, -105, 105, 6, 14),
                     {"wire": {"gap_m": 60.0}},
                     {"status_not_in": ("MEASURED_CURRENT",),
                      "reject_if_unknown": "LARGE_GAP"},
                     seed=seed)


def catenary_wire_150m(seed: int = 20) -> SyntheticScene:
    rng = np.random.default_rng(seed)
    wire = wire_segment((150, -75, 12), (150, 75, 12), 0.5, rng, sag_m=2.0)
    return _assemble("catenary_wire_150m", [("wire", wire)],
                     (145, 155, -80, 80, 8, 16),
                     {"wire": {"sag_m": 2.0, "span_m": 150.0}},
                     # Weakness #2 (sag accepted by straight model, locked
                     # 2026-10-03) is now gated by max_sag_m: a 2 m sag must
                     # be rejected as EXCESSIVE_SAG so a curve model (P4) is
                     # required rather than silently accepting the line.
                     {"status_not_in": ("MEASURED_CURRENT",),
                      "reject_if_unknown": "EXCESSIVE_SAG"},
                     seed=seed)


def partial_occlusion_150m(seed: int = 21) -> SyntheticScene:
    rng = np.random.default_rng(seed)
    wire = wire_segment((150, -5, 10), (150, 5, 10), 0.4, rng)
    wall = rng.uniform((145, -30, 2), (155, -10, 20), (200, 3))
    return _assemble("partial_occlusion_150m", [("wire", wire), ("building", wall)],
                     (140, 160, -8, 8, 7, 13),
                     {"wire": {"visible_span_m": 10.0, "distance_m": 150}},
                     {"status_in": ("MEASURED_CURRENT",)},
                     seed=seed)


def time_jump_window(seed: int = 22) -> SyntheticScene:
    rng = np.random.default_rng(seed)
    wire = wire_segment((60, -10, 7), (60, 10, 7), 0.3, rng)
    return _assemble("time_jump_window", [("wire", wire)],
                     (55, 65, -15, 15, 4, 12),
                     {"wire": {"endpoints": [[60, -10, 7], [60, 10, 7]]},
                      "time_jump_seconds": 1.5},
                     {"status_in": ("MEASURED_CURRENT",),
                      "window_quality_note": "PARTIAL_WINDOW"},
                     seed=seed, time_jump_after_packet=200, time_jump_seconds=1.5)


def two_wires_close_50m(seed: int = 23) -> SyntheticScene:
    """Two parallel wires 4 m apart; proposal layer must separate them."""
    rng = np.random.default_rng(seed)
    wire_a = wire_segment((50, -20, 8), (50, 20, 8), 0.25, rng)
    wire_b = wire_segment((50, -20, 12), (50, 20, 12), 0.25, rng)
    return _assemble("two_wires_close_50m",
                     [("wire_a", wire_a), ("wire_b", wire_b)],
                     (45, 55, -25, 25, 5, 15),
                     {"wire_a": {"endpoints": [[50, -20, 8], [50, 20, 8]]},
                      "wire_b": {"endpoints": [[50, -20, 12], [50, 20, 12]]}},
                     {"note": "proposal_separation_case"},
                     seed=seed)


def wire_in_canopy_100m(seed: int = 24) -> SyntheticScene:
    """A clear wire running through a canopy deck. Proposals that task the
    wire must not drown it in canopy blobs."""
    rng = np.random.default_rng(seed)
    wire = wire_segment((100, -25, 6), (100, 25, 6), 0.3, rng)
    canopy = rng.uniform((95, -30, 8), (105, 30, 14), (240, 3))
    return _assemble("wire_in_canopy_100m",
                     [("wire", wire), ("tree_canopy", canopy)],
                     (90, 110, -30, 30, 4, 16),
                     {"wire": {"endpoints": [[100, -25, 6], [100, 25, 6]]},
                      "tree_canopy": {"blobs": "uniform"}},
                     {"note": "wire_vs_canopy_separation"},
                     seed=seed)


SCENARIO_BUILDERS = {
    builder.__name__: builder for builder in (
        single_wire_50m, sag_wire_100m, sparse_wire_200m,
        ultra_sparse_wire_200m, trees_only_negative, fence_rails_line_negative,
        building_edge_180m, crossing_wires, occluded_wire_gap60,
        catenary_wire_150m, partial_occlusion_150m, time_jump_window,
        two_wires_close_50m, wire_in_canopy_100m)
}


def build_scenario(name: str, seed: int | None = None) -> SyntheticScene:
    if name not in SCENARIO_BUILDERS:
        raise ValueError(f"unknown synthetic scenario {name!r}")
    builder = SCENARIO_BUILDERS[name]
    return builder() if seed is None else builder(seed)

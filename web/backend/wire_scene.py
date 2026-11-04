"""SYNTHETIC outdoor wire geometry and NA70 ray-coverage audit.

Ray/curve intersection is a geometric opportunity, never an observed return.
The sensor-to-world transform is a declared synthetic mounting assumption.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from na70_core import directions_from_codes


def load_scene(path: Path) -> dict:
    scene = json.loads(Path(path).read_text(encoding="utf-8"))
    if scene.get("label") != "SYNTHETIC_GEOMETRY_ONLY":
        raise ValueError("Scene must be labelled SYNTHETIC_GEOMETRY_ONLY")
    wire = scene["wire"]
    endpoints = np.asarray(wire["endpoints"], dtype=float)
    if endpoints.shape != (2, 3) or not np.isfinite(endpoints).all():
        raise ValueError("Wire endpoints must be two finite XYZ coordinates")
    if wire["diameter_m"] <= 0 or wire["midpoint_sag"] < 0:
        raise ValueError("Wire diameter and sag are invalid")
    path_xyz = np.asarray(scene["uav"]["path_world"], dtype=float)
    if path_xyz.shape != (2, 3) or not np.isfinite(path_xyz).all():
        raise ValueError("UAV path must have two finite XYZ coordinates")
    return scene


def wire_curve(scene: dict, segments: int = 32) -> np.ndarray:
    if segments < 1:
        raise ValueError("segments must be positive")
    wire = scene["wire"]
    ends = np.asarray(wire["endpoints"], dtype=float)
    u = np.linspace(0.0, 1.0, segments + 1)
    curve = ends[0][None, :] + u[:, None] * (ends[1] - ends[0])[None, :]
    curve[:, 2] -= 4.0 * float(wire["midpoint_sag"]) * u * (1.0 - u)
    return curve


def sample_geometry(scene: dict, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Return display-only world XYZ and object labels, not sensor measurements."""
    rng = np.random.default_rng(seed)
    pieces = []
    labels = []

    def add(points, name):
        points = np.asarray(points, dtype=np.float32)
        pieces.append(points)
        labels.append(np.full(len(points), name, dtype="U20"))

    ground = scene["ground"]
    count = 1500
    xy = np.column_stack((rng.uniform(*ground["x_range"], count),
                          rng.uniform(*ground["y_range"], count)))
    add(np.column_stack((xy, np.full(count, scene["world_frame"]["ground_z"]))), "ground")
    for pole in scene["supports"]:
        base = np.asarray(pole["base"], dtype=float)
        z = np.linspace(0.0, pole["height"], 90)
        add(base + np.column_stack((np.zeros(len(z)), np.zeros(len(z)), z)), "support")
    if scene["wire"].get("enabled", True):
        add(wire_curve(scene, 500), "wire_truth")
    for obj in scene["distractors"]:
        if obj["kind"] == "tree":
            base = np.asarray(obj["base"], dtype=float)
            h = float(obj["height"])
            r = float(obj["canopy_radius"])
            trunk = base + np.column_stack((np.zeros(50), np.zeros(50), np.linspace(0, h * .7, 50)))
            add(trunk, "tree")
            phi = rng.uniform(0, 2 * np.pi, 450)
            costheta = rng.uniform(-1, 1, 450)
            rad = r * np.cbrt(rng.uniform(0, 1, 450))
            canopy = base + np.column_stack((rad * np.sqrt(1 - costheta**2) * np.cos(phi),
                                              rad * np.sqrt(1 - costheta**2) * np.sin(phi),
                                              h * .75 + rad * costheta))
            add(canopy, "tree")
        elif obj["kind"] == "fence":
            start, end = np.asarray(obj["start"], float), np.asarray(obj["end"], float)
            u = np.linspace(0, 1, 80)
            for height in (0.0, obj["height"]):
                line = start[None, :] + u[:, None] * (end - start)[None, :]
                line[:, 2] += height
                add(line, "fence")
        elif obj["kind"] == "branch":
            start, end = np.asarray(obj["start"], float), np.asarray(obj["end"], float)
            u = np.linspace(0, 1, 60)
            add(start[None, :] + u[:, None] * (end - start)[None, :], "branch")
    return np.concatenate(pieces), np.concatenate(labels)


def ray_directions_from_codes(codes: np.ndarray, frame: dict,
                              install_direction: int = 1) -> np.ndarray:
    """Reproduce decoder orientation for unit rays, including zero-distance codes.

    NA70 code steps and bundle factory offsets follow backend.decoder.decode_frame.
    """
    return directions_from_codes(codes, frame["pitch_offset"],
                                 frame["yaw_offset"], install_direction)


def nominal_world_directions(sensor_directions: np.ndarray, pitch_down_deg: float = 0.0) -> np.ndarray:
    """Synthetic mounting: decoder -Y aims +X, then pitch optical axis down.

    This rotation is not a measured NA70/drone extrinsic.
    """
    d = np.asarray(sensor_directions, dtype=float)
    world = np.column_stack((-d[:, 1], -d[:, 2], d[:, 0]))
    a = np.deg2rad(pitch_down_deg)
    ca, sa = np.cos(a), np.sin(a)
    return np.column_stack((ca * world[:, 0] + sa * world[:, 2], world[:, 1],
                            -sa * world[:, 0] + ca * world[:, 2]))


def ray_wire_hits(directions_world: np.ndarray, origin_world: np.ndarray,
                  curve_world: np.ndarray, radius_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Closest ray/segment geometry, with ray t >= 0 and segment u in [0, 1]."""
    d = np.asarray(directions_world, dtype=float)
    if d.ndim != 2 or d.shape[1] != 3 or not np.isfinite(d).all():
        raise ValueError("directions must be finite Nx3")
    norm = np.linalg.norm(d, axis=1)
    if np.any(norm <= 0) or radius_m <= 0:
        raise ValueError("directions and radius must be positive")
    d = d / norm[:, None]
    origin = np.asarray(origin_world, dtype=float)
    curve = np.asarray(curve_world, dtype=float)
    hits = np.zeros(len(d), dtype=bool)
    nearest = np.full(len(d), np.inf)
    for a_world, b_world in zip(curve[:-1], curve[1:]):
        a = a_world - origin
        v = b_world - a_world
        c = float(v @ v)
        if c <= 0:
            continue
        b = d @ v
        dot_a = d @ a
        e = float(v @ a)
        denom = c - b * b
        u = np.divide(b * dot_a - e, denom, out=np.zeros_like(b), where=denom > 1e-12)
        u = np.clip(u, 0.0, 1.0)
        q = a[None, :] + u[:, None] * v[None, :]
        t = np.einsum("ij,ij->i", d, q)
        t = np.maximum(t, 0.0)
        delta = t[:, None] * d - q
        distance2 = np.einsum("ij,ij->i", delta, delta)
        match = distance2 <= radius_m * radius_m
        hits |= match
        nearest = np.minimum(nearest, distance2)
    return hits, np.sqrt(nearest)


def coverage_intervals(hit_indices: np.ndarray, ray_world: np.ndarray,
                       origin_world: np.ndarray, curve_world: np.ndarray,
                       bins: int = 50) -> dict:
    """Approximate along-wire coverage of hit rays for display and comparison."""
    occupied = np.zeros(bins, dtype=bool)
    if len(hit_indices):
        # At x=wire x, infer lateral coordinate; this scene has a near-constant x.
        d = ray_world[hit_indices]
        forward = d[:, 0]
        keep = forward > 1e-9
        crossing = origin_world[0] + np.zeros(len(d))
        crossing[keep] = (curve_world[0, 0] - origin_world[0]) / forward[keep]
        lateral = origin_world[1] + crossing * d[:, 1]
        lo, hi = curve_world[:, 1].min(), curve_world[:, 1].max()
        index = np.floor((lateral[keep] - lo) / (hi - lo) * bins).astype(int)
        index = index[(index >= 0) & (index < bins)]
        occupied[index] = True
    longest = 0
    run = 0
    for used in occupied:
        run = 0 if used else run + 1
        longest = max(longest, run)
    span = float(curve_world[:, 1].max() - curve_world[:, 1].min())
    return {"occupied_bins": int(occupied.sum()), "total_bins": bins,
            "coverage_fraction": float(occupied.mean()),
            "largest_uncovered_m": longest * span / bins,
            "occupied_bin_indices": np.flatnonzero(occupied).tolist()}

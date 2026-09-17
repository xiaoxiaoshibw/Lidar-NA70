"""SUPERSEDED experimental module - kept only for its original synthetic tests.

The live pipeline is :mod:`backend.mapping` (algorithm) plus
:mod:`backend.mapping_jobs` (persistence and API).  The legacy
``/api/captures/{id}/registration`` endpoints no longer call anything here; they
are shims over a mapping job.

What is wrong with this module and why it was replaced:

* it registers every frame against the *whole* accumulated map, so the target
  cloud grows without bound and wrong correspondences accumulate;
* a rejected frame sets a permanent ``lost`` flag with no re-initialisation
  path, so one bad frame ends the run;
* the result lives only in memory and the browser transforms points per frame;
* ``registration.yaml`` is the historical threshold file; ``mapping.yaml`` is
  the live one.

Do not add features here.  New work belongs in ``mapping.py``.
"""
import json
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from .decoder import decode_frame


def configuration():
    # JSON is a YAML subset; keep thresholds in a reviewable YAML file.
    return json.loads(Path(__file__).with_name("registration.yaml").read_text("utf-8"))


def sample(points, cfg, limit):
    xyz = np.asarray(points[:, :3], dtype=np.float64)
    distance = np.linalg.norm(xyz, axis=1)
    xyz = xyz[np.isfinite(xyz).all(axis=1) & (distance > 0) & (distance < cfg["max_distance_m"])]
    _, indices = np.unique(np.floor(xyz / cfg["voxel_m"]), axis=0, return_index=True)
    xyz = xyz[np.sort(indices)]
    if len(xyz) > limit:
        xyz = xyz[np.linspace(0, len(xyz) - 1, limit, dtype=int)]
    return xyz


def register(source, target, initial, cfg):
    pose = initial.copy()
    if min(len(source), len(target)) < cfg["min_points"]:
        return None, dict(reason="insufficient_points", overlap=0, rmse_m=None)
    spectrum = np.linalg.eigvalsh(np.cov(source.T))
    if spectrum[0] < cfg["min_geometry_ratio"] * spectrum[-1]:
        return None, dict(reason="degenerate_geometry", overlap=0, rmse_m=None)
    tree = cKDTree(target)
    for _ in range(cfg["iterations"]):
        moved = source @ pose[:3, :3].T + pose[:3, 3]
        distances, indices = tree.query(moved)
        valid = distances < cfg["correspondence_m"]
        if valid.sum() < cfg["min_points"]:
            break
        cutoff = np.quantile(distances[valid], 0.85)
        valid &= distances <= cutoff
        a, b = moved[valid], target[indices[valid]]
        ac, bc = a.mean(axis=0), b.mean(axis=0)
        u, _, vt = np.linalg.svd((a - ac).T @ (b - bc))
        correction = np.eye(3)
        correction[2, 2] = np.linalg.det(vt.T @ u.T)
        rotation = vt.T @ correction @ u.T
        delta = np.eye(4)
        delta[:3, :3], delta[:3, 3] = rotation, bc - rotation @ ac
        pose = delta @ pose
        if np.linalg.norm(delta - np.eye(4)) < cfg["convergence_m"]:
            break
    distances, _ = tree.query(source @ pose[:3, :3].T + pose[:3, 3])
    valid = distances < cfg["correspondence_m"]
    overlap = float(valid.mean())
    rmse = float(np.sqrt(np.mean(distances[valid] ** 2))) if valid.any() else None
    step = np.linalg.inv(initial) @ pose
    angle = float(np.degrees(np.arccos(np.clip((np.trace(step[:3, :3]) - 1) / 2, -1, 1))))
    accepted = (overlap >= cfg["min_overlap"] and rmse is not None and rmse <= cfg["max_rmse_m"]
                and np.linalg.norm(step[:3, 3]) <= cfg["max_step_m"] and angle <= cfg["max_rotation_deg"])
    return pose if accepted else None, dict(reason="accepted" if accepted else "quality_gate_failed", overlap=overlap, rmse_m=rmse)


def analyze_registration(path, frames, cancelled, progress):
    cfg = configuration()
    pose, local_map = np.eye(4), np.empty((0, 3))
    records = []
    lost = False
    previous_time = None
    for index, frame in enumerate(frames):
        if cancelled():
            raise InterruptedError()
        source = sample(decode_frame(path, frame), cfg, cfg["max_points"])
        timestamp = frame["time"]
        if previous_time is not None and timestamp <= previous_time:
            lost = True
        previous_time = timestamp
        if lost:
            candidate, quality = None, dict(reason="tracking_lost_restart_required", overlap=0, rmse_m=None)
        elif index == 0:
            spectrum = np.linalg.eigvalsh(np.cov(source.T)) if len(source) >= cfg["min_points"] else np.zeros(3)
            valid = spectrum[-1] > 0 and spectrum[0] >= cfg["min_geometry_ratio"] * spectrum[-1]
            candidate = pose if valid else None
            quality = dict(reason="reference" if valid else "invalid_reference_geometry", overlap=1 if valid else 0, rmse_m=0 if valid else None)
        else:
            candidate, quality = register(source, local_map, pose, cfg)
        if candidate is not None:
            pose = candidate
            transformed = source @ pose[:3, :3].T + pose[:3, 3]
            local_map = sample(np.concatenate((local_map, transformed)), {**cfg, "max_distance_m": float("inf")}, cfg["map_points"])
        else:
            lost = True
        records.append(dict(index=index, time=timestamp, accepted=candidate is not None,
                            pose=pose.tolist() if candidate is not None else None, **quality))
        progress((index + 1) / len(frames))
    return dict(version="lidar-odometry-v1", imu=False, loop_closure=False, config=cfg,
                frames=records, accepted_frames=sum(r["accepted"] for r in records), frame_count=len(frames))

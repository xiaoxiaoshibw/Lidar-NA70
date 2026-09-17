"""Offline fixed-LiDAR foreground extraction, clustering, and tracking.

The pipeline adapts ideas from permissively licensed roadside LiDAR projects:

* TeRyZh/Roadside-LiDAR-Vehicle-Detection-and-Tracking-Background-Subtraction
  (Apache-2.0): fixed-sensor background modelling and foreground filtering.
* koide3/hdl_people_tracking (BSD-2-Clause): spatial cluster constraints and
  constant-velocity people tracks.
* mzahana/multi_target_kf (MIT): Kalman prediction, Hungarian association, and
  tentative/confirmed/coasting track life cycles.

The implementation below is native to the NA70 decoder and keeps the algorithm
independent of ROS so the Windows Web application can analyze PCAP files.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from itertools import product
from pathlib import Path
import time
from typing import Callable

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.spatial import cKDTree

from .bands import BandConfig, CandidatePoints, detect_bands, summarise_bands
from .decoder import decode_frame, decode_frame_codes
from .spatial import weighted_dbscan as _weighted_dbscan


ANALYSIS_VERSION = "na70-ray-history-v3"


@dataclass(frozen=True)
class MotionConfig:
    warmup_frames: int = 100
    voxel_size: float = 0.15
    background_min_hits: int = 3
    background_margin_voxels: int = 1
    min_range: float = 0.5
    max_range: float = 100.0
    noise_radius: float = 0.35
    noise_min_voxels: int = 2
    cluster_eps: float = 0.65
    cluster_min_points: int = 8
    association_distance: float = 1.5
    confirmation_frames: int = 3
    max_missed_frames: int = 5
    min_motion_displacement: float = 0.20
    min_motion_speed: float = 0.15
    min_moving_points: int = 20
    ray_range_bin_codes: int = 8
    ray_min_observations: int = 3
    ray_mode_min_hits: int = 2
    ray_foreground_margin_m: float = 0.5
    temporal_window_frames: int = 10
    event_smoothing_frames: int = 10
    event_min_frames: int = 10
    event_gap_frames: int = 20
    band: BandConfig = field(default_factory=BandConfig)

    def validate(self) -> None:
        if self.warmup_frames < 1:
            raise ValueError("warmup_frames must be positive")
        if self.voxel_size <= 0 or self.cluster_eps <= 0 or self.noise_radius <= 0:
            raise ValueError("spatial thresholds must be positive")
        if self.background_min_hits < 1 or self.background_margin_voxels < 0:
            raise ValueError("background parameters are invalid")
        if self.min_range < 0 or self.max_range <= self.min_range:
            raise ValueError("range limits are invalid")
        if self.noise_min_voxels < 1 or self.cluster_min_points < 1:
            raise ValueError("point thresholds must be positive")
        if self.association_distance <= 0 or self.confirmation_frames < 1:
            raise ValueError("tracking parameters are invalid")
        if self.max_missed_frames < 0 or self.min_motion_displacement < 0 or self.min_motion_speed < 0:
            raise ValueError("motion thresholds are invalid")
        if self.ray_range_bin_codes < 1 or self.ray_min_observations < 1 or self.ray_mode_min_hits < 1:
            raise ValueError("ray background parameters are invalid")
        if self.ray_foreground_margin_m <= 0 or self.temporal_window_frames < 1:
            raise ValueError("ray motion parameters are invalid")
        if self.event_smoothing_frames < 1 or self.event_min_frames < 1 or self.event_gap_frames < 0:
            raise ValueError("event parameters are invalid")
        self.band.validate()


def _row_view(values: np.ndarray) -> np.ndarray:
    contiguous = np.ascontiguousarray(values, dtype=np.int32)
    return contiguous.view(np.dtype((np.void, contiguous.dtype.itemsize * 3))).ravel()


@dataclass
class _Track:
    identifier: int
    state: np.ndarray
    covariance: np.ndarray
    first_position: np.ndarray
    first_time: float
    last_time: float
    hits: int = 1
    missed: int = 0
    age: int = 1
    status: str = "TENTATIVE"
    max_speed: float = 0.0
    max_displacement: float = 0.0

    def predict(self, timestamp: float) -> None:
        dt = max(0.001, min(timestamp - self.last_time, 1.0))
        transition = np.eye(6, dtype=np.float64)
        transition[0, 3] = transition[1, 4] = transition[2, 5] = dt
        acceleration_noise = 1.5
        q = np.diag([dt**4 / 4] * 3 + [dt**2] * 3) * acceleration_noise**2
        self.state = transition @ self.state
        self.covariance = transition @ self.covariance @ transition.T + q
        self.last_time = timestamp
        self.age += 1

    def update(self, measurement: np.ndarray, timestamp: float, confirmation_frames: int) -> None:
        observation = np.zeros((3, 6), dtype=np.float64)
        observation[:3, :3] = np.eye(3)
        residual = measurement - observation @ self.state
        noise = np.eye(3, dtype=np.float64) * 0.12**2
        innovation = observation @ self.covariance @ observation.T + noise
        gain = np.linalg.solve(innovation, observation @ self.covariance).T
        self.state = self.state + gain @ residual
        self.covariance = (np.eye(6) - gain @ observation) @ self.covariance
        self.last_time = timestamp
        self.hits += 1
        self.missed = 0
        if self.hits >= confirmation_frames:
            self.status = "CONFIRMED"
        displacement = float(np.linalg.norm(self.state[:3] - self.first_position))
        elapsed = max(timestamp - self.first_time, 1e-3)
        speed = max(float(np.linalg.norm(self.state[3:])), displacement / elapsed)
        self.max_displacement = max(self.max_displacement, displacement)
        self.max_speed = max(self.max_speed, speed)

    @property
    def speed(self) -> float:
        return float(np.linalg.norm(self.state[3:]))


class FixedLidarMotionAnalyzer:
    """Learns an empty background, clusters foreground, and tracks motion."""

    def __init__(self, config: MotionConfig):
        config.validate()
        self.config = config
        self.frame_index = 0
        self._warmup_voxels: list[np.ndarray] = []
        self._background: np.ndarray | None = None
        self._tracks: list[_Track] = []
        self._finished_tracks: list[_Track] = []
        self._next_track_id = 1

    @property
    def ready(self) -> bool:
        return self._background is not None

    def _valid_mask(self, points: np.ndarray) -> np.ndarray:
        xyz = points[:, :3]
        ranges = np.linalg.norm(xyz, axis=1)
        return np.isfinite(points).all(axis=1) & (ranges >= self.config.min_range) & (ranges <= self.config.max_range)

    def _voxels(self, xyz: np.ndarray) -> np.ndarray:
        return np.floor(xyz / self.config.voxel_size).astype(np.int32)

    def _finish_background(self) -> None:
        stacked = np.vstack(self._warmup_voxels) if self._warmup_voxels else np.empty((0, 3), np.int32)
        voxels, hits = np.unique(stacked, axis=0, return_counts=True)
        required = min(self.config.background_min_hits, self.config.warmup_frames)
        background = voxels[hits >= required]
        margin = self.config.background_margin_voxels
        if margin and len(background):
            offsets = np.asarray(list(product(range(-margin, margin + 1), repeat=3)), dtype=np.int32)
            background = np.unique((background[:, None, :] + offsets).reshape(-1, 3), axis=0)
        self._background = np.unique(_row_view(background))
        self._warmup_voxels.clear()

    def _foreground(self, points: np.ndarray, valid_mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        valid_indices = np.flatnonzero(valid_mask)
        valid_points = points[valid_mask]
        if len(valid_points) == 0:
            return valid_indices, valid_points, np.empty((0, 3), np.int32), np.empty(0, np.int32)
        voxels = self._voxels(valid_points[:, :3])
        outside = ~np.isin(_row_view(voxels), self._background)
        candidate_indices = valid_indices[outside]
        candidates = valid_points[outside]
        if len(candidates) == 0:
            return candidate_indices, candidates, np.empty((0, 3), np.int32), np.empty(0, np.int32)

        occupied, inverse, counts = np.unique(self._voxels(candidates[:, :3]), axis=0, return_inverse=True, return_counts=True)
        radius_voxels = max(1.0, self.config.noise_radius / self.config.voxel_size)
        support = cKDTree(occupied).query_ball_point(occupied, radius_voxels, p=np.inf, return_length=True)
        keep_voxel = support >= self.config.noise_min_voxels
        keep_points = keep_voxel[inverse]
        candidate_indices = candidate_indices[keep_points]
        candidates = candidates[keep_points]
        if len(candidates) == 0:
            return candidate_indices, candidates, np.empty((0, 3), np.int32), np.empty(0, np.int32)
        occupied, inverse, counts = np.unique(self._voxels(candidates[:, :3]), axis=0, return_inverse=True, return_counts=True)
        centres = (occupied.astype(np.float64) + 0.5) * self.config.voxel_size
        labels = _weighted_dbscan(centres, counts, self.config.cluster_eps, self.config.cluster_min_points)
        return candidate_indices, candidates, occupied, labels[inverse]

    def _associate(self, measurements: list[np.ndarray], timestamp: float) -> dict[int, _Track]:
        for track in self._tracks:
            track.predict(timestamp)
        assignments: dict[int, _Track] = {}
        matched_tracks: set[int] = set()
        matched_measurements: set[int] = set()
        if self._tracks and measurements:
            predicted = np.asarray([track.state[:3] for track in self._tracks])
            observed = np.asarray(measurements)
            costs = np.linalg.norm(predicted[:, None, :] - observed[None, :, :], axis=2)
            rows, columns = linear_sum_assignment(costs)
            for row, column in zip(rows, columns):
                if costs[row, column] > self.config.association_distance:
                    continue
                track = self._tracks[row]
                track.update(observed[column], timestamp, self.config.confirmation_frames)
                assignments[int(column)] = track
                matched_tracks.add(row)
                matched_measurements.add(int(column))

        for index, track in enumerate(self._tracks):
            if index not in matched_tracks:
                track.missed += 1
                if track.status == "CONFIRMED":
                    track.status = "COAST"

        for index, measurement in enumerate(measurements):
            if index in matched_measurements:
                continue
            state = np.zeros(6, dtype=np.float64)
            state[:3] = measurement
            covariance = np.diag([0.25, 0.25, 0.25, 4.0, 4.0, 4.0])
            track = _Track(self._next_track_id, state, covariance, measurement.copy(), timestamp, timestamp)
            self._next_track_id += 1
            self._tracks.append(track)
            assignments[index] = track

        survivors = []
        for track in self._tracks:
            if track.missed > self.config.max_missed_frames:
                self._finished_tracks.append(track)
            else:
                survivors.append(track)
        self._tracks = survivors
        return assignments

    def process(self, points: np.ndarray, timestamp: float) -> tuple[dict, np.ndarray]:
        points = np.asarray(points, dtype=np.float32)
        if points.ndim != 2 or points.shape[1] < 3:
            raise ValueError("points must have shape [N, >=3]")
        valid_mask = self._valid_mask(points)
        considered = int(valid_mask.sum())
        mask = np.zeros(len(points), dtype=np.uint8)
        current_index = self.frame_index
        self.frame_index += 1

        if not self.ready:
            valid_points = points[valid_mask]
            self._warmup_voxels.append(np.unique(self._voxels(valid_points[:, :3]), axis=0) if len(valid_points) else np.empty((0, 3), np.int32))
            if self.frame_index >= self.config.warmup_frames:
                self._finish_background()
            return dict(index=current_index, time=float(timestamp), ready=self.ready, considered_count=considered,
                        candidate_count=0, moving_count=0, has_motion=False, clusters=[], tracks=[]), mask

        candidate_indices, candidates, _occupied, point_labels = self._foreground(points, valid_mask)
        clusters = []
        measurements = []
        unique_labels = sorted(int(label) for label in np.unique(point_labels) if label >= 0)
        for label in unique_labels:
            selected = candidates[point_labels == label]
            if len(selected) < self.config.cluster_min_points:
                continue
            minimum = selected[:, :3].min(axis=0)
            maximum = selected[:, :3].max(axis=0)
            centroid = np.median(selected[:, :3], axis=0).astype(np.float64)
            measurements.append(centroid)
            clusters.append(dict(label=label, point_count=int(len(selected)), centroid=centroid.tolist(),
                                 minimum=minimum.astype(float).tolist(), maximum=maximum.astype(float).tolist()))

        assignments = self._associate(measurements, float(timestamp))
        moving_count = 0
        for cluster_index, cluster in enumerate(clusters):
            track = assignments[cluster_index]
            elapsed = max(float(timestamp) - track.first_time, 1e-3)
            displacement = float(np.linalg.norm(track.state[:3] - track.first_position))
            observed_speed = displacement / elapsed
            speed = max(track.speed, observed_speed)
            is_moving = (track.status == "CONFIRMED" and displacement >= self.config.min_motion_displacement
                         and speed >= self.config.min_motion_speed)
            cluster.update(track_id=track.identifier, track_status=track.status, speed=speed,
                           displacement=displacement, moving=is_moving)
            if is_moving:
                label = unique_labels[cluster_index]
                full_indices = candidate_indices[point_labels == label]
                mask[full_indices] = 1
                moving_count += len(full_indices)

        visible_tracks = [dict(id=track.identifier, status=track.status, position=track.state[:3].tolist(),
                               velocity=track.state[3:].tolist(), speed=track.speed, hits=track.hits,
                               missed=track.missed, age=track.age) for track in self._tracks]
        has_motion = moving_count >= self.config.min_moving_points
        return dict(index=current_index, time=float(timestamp), ready=True, considered_count=considered,
                    candidate_count=int(len(candidates)), moving_count=int(moving_count),
                    has_motion=has_motion, clusters=clusters, tracks=visible_tracks), mask

    def track_summaries(self) -> list[dict]:
        tracks = self._finished_tracks + self._tracks
        return [dict(id=track.identifier, status=track.status, hits=track.hits, age=track.age,
                     max_speed=track.max_speed, max_displacement=track.max_displacement,
                     moving=track.max_speed >= self.config.min_motion_speed
                     and track.max_displacement >= self.config.min_motion_displacement)
                for track in tracks if track.hits >= self.config.confirmation_frames]


def _events(records: list[dict]) -> list[dict]:
    events = []
    start = None
    peak = 0
    for record in records + [dict(has_motion=False, time=records[-1]["time"] if records else 0, index=len(records))]:
        if record["has_motion"]:
            if start is None:
                start = record
                peak = 0
            peak = max(peak, record["moving_count"])
        elif start is not None:
            previous = records[max(0, record["index"] - 1)]
            events.append(dict(start_frame=start["index"], end_frame=previous["index"],
                               start_time=start["time"], end_time=previous["time"], peak_moving_points=peak))
            start = None
    return events


def _analyze_voxel_capture(capture_path: Path, frames: list[dict], output_directory: Path, config: MotionConfig,
                           progress: Callable[[float, dict], None] = lambda *_: None,
                           cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Legacy first-window voxel analysis, retained for controlled comparisons."""
    analyzer = FixedLidarMotionAnalyzer(config)
    records = []
    mask_offset = 0
    mask_temp = output_directory / "analysis_masks.tmp"
    summary_temp = output_directory / "analysis.tmp"
    started = time.perf_counter()
    try:
        with mask_temp.open("wb") as masks:
            for index, frame in enumerate(frames):
                if cancelled():
                    raise InterruptedError()
                points = decode_frame(capture_path, frame)
                record, mask = analyzer.process(points, float(frame["time"]))
                record["mask_offset"] = mask_offset
                record["point_count"] = len(mask)
                mask_offset += len(mask)
                masks.write(mask.tobytes())
                records.append(record)
                progress((index + 1) / len(frames), record)
        track_summaries = analyzer.track_summaries()
        summary = dict(version=ANALYSIS_VERSION, config=asdict(config), frame_count=len(records),
                       warmup_frames=config.warmup_frames, moving_frames=sum(r["has_motion"] for r in records),
                       candidate_points=sum(r["candidate_count"] for r in records),
                       moving_points=sum(r["moving_count"] for r in records), events=_events(records),
                       tracks=track_summaries, moving_tracks=sum(t["moving"] for t in track_summaries),
                       elapsed_seconds=round(time.perf_counter() - started, 3), frames=records)
        summary_temp.write_text(json.dumps(summary, ensure_ascii=False), "utf-8")
        mask_temp.replace(output_directory / "analysis_masks.bin")
        summary_temp.replace(output_directory / "analysis.json")
        return summary
    except Exception:
        mask_temp.unlink(missing_ok=True)
        summary_temp.unlink(missing_ok=True)
        raise


def _event_flags(values: np.ndarray, config: MotionConfig) -> tuple[np.ndarray, float, float]:
    width = min(config.event_smoothing_frames, len(values))
    if width <= 1:
        smooth = values.astype(np.float64)
    else:
        left = width // 2
        right = width - 1 - left
        padded = np.pad(values.astype(np.float64), (left, right), mode="edge")
        smooth = np.convolve(padded, np.ones(width) / width, mode="valid")
    lower = smooth[smooth <= np.quantile(smooth, 0.35)] if len(smooth) else smooth
    baseline = float(np.median(lower)) if len(lower) else 0.0
    mad = float(np.median(np.abs(lower - baseline))) if len(lower) else 0.0
    threshold = max(float(config.min_moving_points), baseline + 4 * max(mad, 1.0))
    active = smooth >= threshold

    start = None
    for index, value in enumerate(active):
        if not value and start is None:
            start = index
        elif value and start is not None:
            if index - start <= config.event_gap_frames:
                active[start:index] = True
            start = None
    cleaned = np.zeros_like(active)
    start = None
    for index, value in enumerate(np.r_[active, False]):
        if value and start is None:
            start = index
        elif not value and start is not None:
            if index - start >= config.event_min_frames:
                cleaned[start:index] = True
            start = None
    return cleaned, baseline, threshold


def analyze_capture(capture_path: Path, frames: list[dict], output_directory: Path, config: MotionConfig,
                    progress: Callable[[float, dict], None] = lambda *_: None,
                    cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Analyze non-repeating NA70 scans with a full-capture ray background."""
    config.validate()
    started = time.perf_counter()
    frame_keys: list[np.ndarray] = []
    frame_distance: list[np.ndarray] = []
    frame_indices: list[np.ndarray] = []
    all_keys = []
    all_ranges = []
    bin_codes = config.ray_range_bin_codes
    with capture_path.open("rb") as source:
        for index, frame in enumerate(frames):
            if cancelled():
                raise InterruptedError()
            codes = decode_frame_codes(capture_path, frame, source)
            distance = codes[:, 0].astype(np.int64)
            valid = ((distance * 0.016 >= config.min_range) & (distance * 0.016 <= config.max_range)
                     & (codes[:, 3] > 0))
            indices = np.flatnonzero(valid)
            keys = (codes[valid, 1].astype(np.int64) << 16) | codes[valid, 2].astype(np.int64)
            # Keep the raw uint16 distance code; the binned version is derived when
            # needed so no second decode pass or large int64 buffer is required.
            raw_distance = codes[valid, 0].copy()
            ranges = raw_distance.astype(np.int64) // bin_codes
            frame_indices.append(indices)
            frame_keys.append(keys)
            frame_distance.append(raw_distance)
            all_keys.append(keys)
            all_ranges.append(ranges)
            progress((index + 1) / len(frames) * 0.30, {})

    keys = np.concatenate(all_keys) if all_keys else np.empty(0, np.int64)
    ranges = np.concatenate(all_ranges) if all_ranges else np.empty(0, np.int64)
    if len(keys) == 0:
        raise ValueError("没有可用于运动分析的有效点")
    packed = (keys << 10) | ranges
    combinations, combination_counts = np.unique(packed, return_counts=True)
    combination_keys = combinations >> 10
    combination_ranges = combinations & 1023
    order = np.lexsort((-combination_counts, combination_keys))
    sorted_keys = combination_keys[order]
    first = np.r_[True, sorted_keys[1:] != sorted_keys[:-1]]
    background_keys = sorted_keys[first]
    background_ranges = combination_ranges[order][first]
    mode_counts = combination_counts[order][first]
    directions, observation_counts = np.unique(keys, return_counts=True)
    stable = ((observation_counts[np.searchsorted(directions, background_keys)] >= config.ray_min_observations)
              & (mode_counts >= config.ray_mode_min_hits))
    background_keys = background_keys[stable]
    background_ranges = background_ranges[stable]
    del packed, combinations, combination_counts, combination_keys, combination_ranges, order, sorted_keys

    margin_bins = max(1, int(np.ceil(config.ray_foreground_margin_m / (0.016 * bin_codes))))
    candidate_position: list[np.ndarray] = []
    candidate_distance: list[np.ndarray] = []
    candidate_intensity: list[np.ndarray] = []
    candidate_index: list[np.ndarray] = []
    candidate_frame: list[np.ndarray] = []
    candidate_time: list[np.ndarray] = []
    considered_counts = []
    with capture_path.open("rb") as source:
        for index, (frame, ray_keys, raw_distance, valid_indices) in enumerate(
                zip(frames, frame_keys, frame_distance, frame_indices)):
            if cancelled():
                raise InterruptedError()
            ray_ranges = raw_distance.astype(np.int64) // bin_codes
            positions = np.searchsorted(background_keys, ray_keys)
            known = positions < len(background_keys)
            known[known] &= background_keys[positions[known]] == ray_keys[known]
            foreground = np.zeros(len(ray_keys), dtype=bool)
            foreground[known] = ray_ranges[known] < background_ranges[positions[known]] - margin_bins
            points = decode_frame(capture_path, frame, source=source)
            selected = valid_indices[foreground]
            # Provenance kept per candidate point: XYZ, raw range, reflectance,
            # frame index, capture time and the original point index in the frame.
            candidate_position.append(points[selected, :3].astype(np.float32))
            candidate_intensity.append(points[selected, 3].astype(np.uint8))
            candidate_distance.append(raw_distance[foreground].astype(np.float32) * np.float32(0.016))
            candidate_index.append(selected.astype(np.int32))
            candidate_frame.append(np.full(len(selected), index, dtype=np.int32))
            candidate_time.append(np.full(len(selected), float(frame["time"]), dtype=np.float64))
            considered_counts.append(len(valid_indices))
            progress(0.30 + (index + 1) / len(frames) * 0.30, {})

    def stack(parts: list[np.ndarray], trailing: tuple, dtype) -> np.ndarray:
        usable = [part for part in parts if len(part)]
        return np.concatenate(usable).astype(dtype, copy=False) if usable else np.empty(trailing, dtype)

    candidates = CandidatePoints(
        position=stack(candidate_position, (0, 3), np.float32),
        time=stack(candidate_time, (0,), np.float64),
        frame=stack(candidate_frame, (0,), np.int32),
        distance=stack(candidate_distance, (0,), np.float32),
        intensity=stack(candidate_intensity, (0,), np.uint8),
        point_index=stack(candidate_index, (0,), np.int32),
    )

    frame_times = np.asarray([float(frame["time"]) for frame in frames], dtype=np.float64)
    progress(0.62, {})
    detection = detect_bands(candidates, frame_times, config.band)
    progress(0.80, {})

    masks = [np.zeros(frame["point_count"], dtype=np.uint8) for frame in frames]
    shape = len(frames)
    candidate_counts = np.bincount(candidates.frame, minlength=shape) if len(candidates) else np.zeros(shape, np.int64)
    signal_counts = np.zeros(shape, dtype=np.int64)
    if len(candidates):
        in_cluster = detection.point_cluster >= 0
        if in_cluster.any():
            signal_counts = np.bincount(candidates.frame[in_cluster], minlength=shape)
        accepted = detection.point_band >= 0
        if accepted.any():
            band_frames = candidates.frame[accepted]
            band_indices = candidates.point_index[accepted]
            for frame_index in np.unique(band_frames):
                masks[int(frame_index)][band_indices[band_frames == frame_index]] = 1

    records = []
    for index, frame in enumerate(frames):
        targets = detection.targets_by_frame.get(index, [])
        # Backwards-compatible views of the same per-slice targets. The boxes come
        # from the local time slice, never from the whole travelled path.
        clusters = [dict(label=target["band_id"], track_id=target["band_id"],
                         track_status=target["status"], point_count=target["point_count"],
                         centroid=target["position"], minimum=target["minimum"],
                         maximum=target["maximum"], speed=target["speed"],
                         displacement=target["displacement"], moving=True) for target in targets]
        tracks = [dict(id=target["band_id"], status=target["status"], position=target["position"],
                       velocity=target["velocity"], speed=target["speed"],
                       hits=target["point_count"], missed=0 if target["status"] == "CONFIRMED" else 1,
                       age=target["slice_index"] + 1) for target in targets]
        records.append(dict(index=index, time=float(frame["time"]), ready=True,
                            considered_count=considered_counts[index],
                            candidate_count=int(candidate_counts[index]),
                            signal_count=int(signal_counts[index]),
                            moving_count=int(masks[index].sum()),
                            has_motion=False, clusters=clusters, tracks=tracks, targets=targets,
                            moving_bands=len({target["band_id"] for target in targets})))
        progress(0.80 + (index + 1) / len(frames) * 0.18, records[-1])

    moving_counts = np.asarray([record["moving_count"] for record in records], dtype=np.float64)
    if len(records):
        active, baseline, event_threshold = _event_flags(moving_counts, config)
    else:
        active, baseline, event_threshold = np.zeros(0, dtype=bool), 0.0, 0.0
    for index, record in enumerate(records):
        # A frame is "in motion" while an accepted band is active.  Bands are
        # instantiated for every slice of their span, including short gaps, so a
        # target briefly missed between two good slices does not split the event.
        # Frames bridged by the event window keep has_motion for the same reason.
        record["has_motion"] = bool(record["targets"] or active[index])

    bands = detection.bands
    band_section = summarise_bands(detection)
    mask_temp = output_directory / "analysis_masks.tmp"
    summary_temp = output_directory / "analysis.tmp"
    mask_offset = 0
    try:
        with mask_temp.open("wb") as output:
            for record, mask in zip(records, masks):
                record["mask_offset"] = mask_offset
                record["point_count"] = len(mask)
                mask_offset += len(mask)
                output.write(mask.tobytes())
        summary = dict(
            version=ANALYSIS_VERSION, method="ray_history_spatiotemporal_bands",
            config=asdict(config), frame_count=len(records), warmup_frames=0,
            stable_ray_count=len(background_keys),
            event_baseline=baseline, event_threshold=event_threshold,
            moving_frames=sum(bool(record["has_motion"]) for record in records),
            candidate_points=int(len(candidates)),
            moving_points=sum(record["moving_count"] for record in records),
            events=_events(records),
            bands=band_section["bands"], band_count=band_section["band_count"],
            band_version=band_section["version"], band_config=band_section["config"],
            band_diagnostics=band_section["diagnostics"], band_note=band_section["note"],
            tracks=[dict(id=band["band_id"], status="CONFIRMED", hits=band["slice_count"],
                         age=band["slice_count"], max_speed=band["speed"],
                         max_displacement=band["displacement"], moving=True) for band in bands],
            moving_tracks=len(bands),
            elapsed_seconds=round(time.perf_counter() - started, 3), frames=records)
        summary_temp.write_text(json.dumps(summary, ensure_ascii=False), "utf-8")
        mask_temp.replace(output_directory / "analysis_masks.bin")
        summary_temp.replace(output_directory / "analysis.json")
        progress(1.0, records[-1] if records else {})
        return summary
    except Exception:
        mask_temp.unlink(missing_ok=True)
        summary_temp.unlink(missing_ok=True)
        raise

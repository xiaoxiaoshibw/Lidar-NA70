"""Four-dimensional spatio-temporal point-band detection for the fixed NA70 LiDAR.

Why this module exists
----------------------
The NA70 dual-wedge scanner does not repeat identical scan directions from one
100-packet frame to the next, so consecutive-frame voxel differencing turns the
normal sweep into false foreground and cannot be the primary method.  The
per-capture ray-history background in :mod:`backend.motion` already compares
each raw (pitch, yaw) direction only against *its own* history, which removes
the sweep artefact.  What it still left open is the second question:

    given a set of candidate change points, which of them are a *moving object*?

A person walking through the scene paints a connected band in the joint
``(x, y, z, t)`` volume.  Its length is the travelled path, its cross-section is
roughly body sized, and its centroid advances smoothly along one dominant
direction.  Static structure that survives foreground gating stays compact in
space *and* time; isolated noise never connects across time slices.

So the detector clusters candidates in four dimensions, scores each cluster for
"elongated, persistent, directional motion", then re-instantiates a per-time-slice
local target from the accepted bands.  The band length is a *path* measurement and
is deliberately never reported as body size - body size comes from the per-slice
cross-section instead.

CPU-only NumPy/SciPy.  No ROS, no CUDA, no WSL2.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from .spatial import weighted_dbscan

BAND_VERSION = "na70-spatiotemporal-bands-v1"

# Feature vector order used by the 4D clustering: three metric axes plus a
# time axis that has been converted into metres by ``speed_scale``.
_SPACE_AXES = 3

# A two-branch interpretation is only reported when it beats the single-line
# residual by this factor.  Without a margin, noise would label straight bands
# as turning ones.
_BRANCH_GAIN = 0.6


@dataclass(frozen=True)
class BandConfig:
    """Thresholds for 4D band clustering, acceptance, slicing and merging."""

    # --- 4D voxelisation and clustering -----------------------------------
    voxel_size: float = 0.20          # m   spatial voxel edge
    time_voxel: float = 0.30          # s   temporal voxel edge
    speed_scale: float = 1.4          # m/s normalises time into the spatial metric
    eps: float = 0.80                 # m   DBSCAN radius in the normalised 4D metric
    min_points: int = 24              # raw points required to seed a band
    max_voxels: int = 250_000         # hard guard; voxel size coarsens if exceeded

    # --- band acceptance ---------------------------------------------------
    min_duration: float = 1.2         # s
    min_slices: int = 5               # occupied time slices
    min_displacement: float = 1.00    # m   start-to-end centroid displacement
    min_speed: float = 0.35           # m/s below a purposeful walk
    max_speed: float = 4.0            # m/s reject implausible / aliased bands
    min_coverage: float = 0.45        # occupied slices / expected slices
    min_linearity: float = 3.0        # PCA lambda1 / lambda2
    min_straightness: float = 0.45    # displacement / trajectory length
    max_regression_rms: float = 1.0   # m   centroid-vs-time fit residual
    min_local_points: int = 30        # points in the densest time slice
    min_point_count: int = 60         # total raw points in the band

    # --- per-slice instantiation ------------------------------------------
    slice_seconds: float = 0.30       # s   time-slice width
    slice_min_points: int = 6         # points for a slice to define a position
    smoothing_slices: int = 3         # moving-average width over slice centres
    max_gap_slices: int = 3           # tolerated consecutive empty slices
    aabb_percentile: float = 0.98     # local box percentile coverage
    min_box_extent: float = 0.10      # m   floor so boxes are never degenerate

    # --- fragment merging --------------------------------------------------
    merge_max_gap: float = 1.5        # s   gap between fragment end and start
    merge_max_distance: float = 2.0   # m   end-to-start separation
    merge_max_angle_deg: float = 60.0 # deg direction agreement

    def validate(self) -> None:
        positive = {
            "voxel_size": self.voxel_size, "time_voxel": self.time_voxel,
            "speed_scale": self.speed_scale, "eps": self.eps,
            "slice_seconds": self.slice_seconds, "min_box_extent": self.min_box_extent,
            "min_duration": self.min_duration, "min_local_points": self.min_local_points,
        }
        for name, value in positive.items():
            if not np.isfinite(value) or value <= 0:
                raise ValueError(f"band parameter {name} must be positive and finite")
        if self.min_points < 1 or self.slice_min_points < 1 or self.min_slices < 1:
            raise ValueError("band point and slice thresholds must be positive")
        if not 0.5 <= self.aabb_percentile < 1.0:
            raise ValueError("aabb_percentile must be in [0.5, 1)")
        if self.smoothing_slices < 1 or self.max_gap_slices < 0:
            raise ValueError("smoothing and gap parameters are invalid")
        if self.min_speed < 0 or self.max_speed <= self.min_speed:
            raise ValueError("band speed limits are invalid")
        if not 0.0 <= self.min_coverage <= 1.0:
            raise ValueError("min_coverage must be in [0, 1]")
        if self.min_linearity < 0 or self.max_regression_rms <= 0:
            raise ValueError("band shape limits are invalid")
        if not 0.0 < self.min_straightness <= 1.0:
            raise ValueError("min_straightness must be in (0, 1]")
        if self.min_point_count < 1:
            raise ValueError("min_point_count must be positive")
        if self.merge_max_gap < 0 or self.merge_max_distance < 0 or not 0 < self.merge_max_angle_deg < 180:
            raise ValueError("band merge limits are invalid")


@dataclass
class CandidatePoints:
    """Change points produced by the ray-history background, with provenance."""

    position: np.ndarray          # (N, 3) float32  x, y, z
    time: np.ndarray              # (N,)   float64  play time in seconds
    frame: np.ndarray             # (N,)   int32    frame index
    distance: np.ndarray          # (N,)   float32  raw range in metres
    intensity: np.ndarray         # (N,)   uint8    raw reflectance
    point_index: np.ndarray       # (N,)   int32    index inside its frame

    def __len__(self) -> int:
        return int(len(self.position))

    @classmethod
    def empty(cls) -> "CandidatePoints":
        return cls(position=np.empty((0, 3), np.float32), time=np.empty(0, np.float64),
                   frame=np.empty(0, np.int32), distance=np.empty(0, np.float32),
                   intensity=np.empty(0, np.uint8), point_index=np.empty(0, np.int32))

    def subset(self, mask: np.ndarray) -> "CandidatePoints":
        return CandidatePoints(self.position[mask], self.time[mask], self.frame[mask],
                               self.distance[mask], self.intensity[mask], self.point_index[mask])


@dataclass
class BandDetection:
    """Result of :func:`detect_bands`."""

    config: BandConfig
    bands: list                        # accepted band summaries, merged and ranked
    rejected: int                      # clusters that failed acceptance
    point_band: np.ndarray             # (N,) int32 accepted band index, -1 if none
    point_cluster: np.ndarray          # (N,) int32 raw 4D cluster index before acceptance, -1 if none
    point_slice: np.ndarray            # (N,) int32 slice index per candidate, -1 if none
    targets_by_frame: dict             # frame index -> list of per-slice target states
    stats: dict                        # counters, timings and clustering diagnostics


def _scales(config: BandConfig, factor: float = 1.0) -> np.ndarray:
    """Voxel edges in the normalised 4D space (metres on every axis)."""
    spatial = config.voxel_size * factor
    return np.array([spatial, spatial, spatial, config.time_voxel * config.speed_scale], dtype=np.float64)


def _four_dimensional_coordinates(points: CandidatePoints, config: BandConfig) -> np.ndarray:
    return np.column_stack([points.position.astype(np.float64), points.time * config.speed_scale])


def cluster_candidates(points: CandidatePoints, config: BandConfig) -> tuple[np.ndarray, np.ndarray, float]:
    """Voxelise candidates in 4D, then cluster the unique occupied voxels.

    Returns ``(per_point_label, occupied_centre_count, voxel_factor)``.  Voxel
    edges are coarsened until the occupied-voxel count fits ``max_voxels`` so a
    pathological capture cannot blow up the neighbourhood search.
    """
    if len(points) == 0:
        return np.empty(0, np.int32), 0, 1.0
    coordinates = _four_dimensional_coordinates(points, config)
    factor = 1.0
    for _ in range(8):
        scales = _scales(config, factor)
        voxels = np.floor(coordinates / scales).astype(np.int64)
        occupied, inverse, counts = np.unique(voxels, axis=0, return_inverse=True, return_counts=True)
        if len(occupied) <= config.max_voxels or factor >= 8.0:
            break
        factor *= 2.0
    centres = (occupied.astype(np.float64) + 0.5) * scales
    labels = weighted_dbscan(centres, counts, config.eps, config.min_points)
    return labels[inverse], len(occupied), factor


def _principal_axis(xyz: np.ndarray) -> tuple[float, float, np.ndarray]:
    """Return ``(lambda1/lambda2, lambda2/lambda3, unit main axis)`` for a point set."""
    if len(xyz) < 3:
        return 0.0, 0.0, np.zeros(3)
    centred = xyz - xyz.mean(axis=0)
    covariance = centred.T @ centred / max(len(xyz) - 1, 1)
    values, vectors = np.linalg.eigh(covariance)
    order = np.argsort(values)[::-1]
    values = np.maximum(values[order], 0.0)
    axis = vectors[:, order[0]]
    norm = float(np.linalg.norm(axis))
    axis = axis / norm if norm > 1e-9 else np.zeros(3)
    linearity = float(values[0] / max(values[1], 1e-9))
    planarity = float(values[1] / max(values[2], 1e-9))
    return linearity, planarity, axis


def _slices(times: np.ndarray, origin: float, width: float) -> np.ndarray:
    return np.floor((times - origin) / width).astype(np.int64)


def _plane_basis(direction: np.ndarray) -> np.ndarray:
    """Two orthonormal axes spanning the plane perpendicular to ``direction``."""
    axis = np.asarray(direction, dtype=np.float64)
    norm = float(np.linalg.norm(axis))
    if norm < 1e-9:
        return np.eye(3)[:2]
    axis = axis / norm
    helper = np.array([0.0, 0.0, 1.0]) if abs(axis[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    first = np.cross(axis, helper)
    first = first / max(float(np.linalg.norm(first)), 1e-9)
    return np.stack([first, np.cross(axis, first)])


def _quantile_sorted(sorted_values: np.ndarray, q: float) -> np.ndarray:
    """Linear-interpolated quantile of an already-sorted array.

    Reproduces ``np.quantile(..., method="linear")`` bit-for-bit, including
    numpy's two-sided lerp (``b - diff * (1 - t)`` for ``t >= 0.5``), while
    skipping the per-call dispatch overhead that dominates on the small
    per-slice arrays used here.
    """
    count = sorted_values.shape[0]
    if count == 1:
        return sorted_values[0]
    virtual = q * (count - 1)
    previous = int(np.floor(virtual))
    if previous >= count - 1:
        return sorted_values[count - 1]
    gamma = virtual - previous
    lower = sorted_values[previous]
    upper = sorted_values[previous + 1]
    diff = upper - lower
    if gamma >= 0.5:
        return upper - diff * (1.0 - gamma)
    return lower + diff * gamma


def _cross_section(xyz: np.ndarray, basis: np.ndarray, config: BandConfig) -> np.ndarray:
    """Robust extent measured across the direction of travel.

    A time slice is smeared along the path by ``speed x slice width``, so the
    world-aligned box overstates a walking person.  Projecting onto the plane
    perpendicular to the motion removes that smear and leaves the actual body
    cross-section.
    """
    tail = (1.0 - config.aabb_percentile) / 2.0
    ordered = np.sort(xyz @ basis.T, axis=0)
    low = _quantile_sorted(ordered, tail)
    high = _quantile_sorted(ordered, 1.0 - tail)
    return np.maximum(high - low, config.min_box_extent)


def _box(xyz: np.ndarray, config: BandConfig) -> tuple[np.ndarray, np.ndarray]:
    """Robust local axis-aligned box, floored so it is never degenerate."""
    tail = (1.0 - config.aabb_percentile) / 2.0
    ordered = np.sort(xyz, axis=0)
    low = _quantile_sorted(ordered, tail)
    high = _quantile_sorted(ordered, 1.0 - tail)
    centre = (low + high) / 2.0
    half = np.maximum((high - low) / 2.0, config.min_box_extent / 2.0)
    return centre - half, centre + half


def _line_fit(times: np.ndarray, centres: np.ndarray) -> tuple[float, float, np.ndarray]:
    """Least-squares ``centre = c0 + v * t``; returns ``(rms, r2, velocity)``."""
    if len(times) < 2:
        return 0.0, 1.0, np.zeros(3)
    design = np.column_stack([np.ones_like(times), times])
    coefficients, *_ = np.linalg.lstsq(design, centres, rcond=None)
    residual = centres - design @ coefficients
    rms = float(np.sqrt(np.mean(np.sum(residual ** 2, axis=1))))
    total = float(np.sum((centres - centres.mean(axis=0)) ** 2))
    r2 = float(1.0 - float(np.sum(residual ** 2)) / total) if total > 1e-12 else 1.0
    return rms, r2, np.asarray(coefficients[1], dtype=np.float64)


def _regression(times: np.ndarray, centres: np.ndarray) -> tuple[float, float, np.ndarray, float, int, int | None]:
    """Single-line fit plus a best two-branch fit over one split point.

    A person who walks around a corner traces a bent centroid path.  Judging it
    against a single straight line alone would reject genuine motion, so the
    residual is also minimised over one split of the slice sequence and the
    better interpretation is used for acceptance.  ``branches`` reports which won
    and ``cut`` is the slice index where the two branches meet.
    """
    rms, r2, velocity = _line_fit(times, centres)
    split_rms, split_velocity, cut = rms, velocity, None
    if len(times) >= 4:
        best = rms
        for index in range(2, len(times) - 1):
            left, _, left_velocity = _line_fit(times[:index], centres[:index])
            right, _, _ = _line_fit(times[index:], centres[index:])
            worst = max(left, right)
            if worst < best:
                best, split_velocity, cut = worst, left_velocity, index
        # Only credit the split when it is a real improvement, otherwise a nearly
        # straight band would be reported as turning on floating-point noise.
        if cut is not None and best > _BRANCH_GAIN * rms:
            cut = None
        split_rms = best
    return rms, r2, velocity, split_rms, (2 if cut is not None else 1), cut


def _band_statistics(points: CandidatePoints, indices: np.ndarray, config: BandConfig,
                     origin: float) -> dict | None:
    """Full descriptor for one candidate cluster; ``None`` when too small."""
    xyz = points.position[indices].astype(np.float64)
    times = points.time[indices].astype(np.float64)
    if len(xyz) < config.slice_min_points:
        return None

    t_min, t_max = float(times.min()), float(times.max())
    duration = t_max - t_min
    extent = xyz.max(axis=0) - xyz.min(axis=0)
    spatial_span = float(np.linalg.norm(extent))
    linearity, planarity, axis = _principal_axis(xyz)

    buckets = _slices(times, origin, config.slice_seconds)
    # One stable grouping replaces the repeated ``buckets == b`` scans below.  A
    # stable sort keeps each bucket's points in their original order, so every
    # reduction (mean, median, quantile) is bit-identical to the mask result.
    order = np.argsort(buckets, kind="stable")
    sorted_buckets = buckets[order]
    starts = np.r_[0, np.flatnonzero(np.diff(sorted_buckets)) + 1]
    unique_buckets = sorted_buckets[starts]
    counts = np.diff(np.r_[starts, len(sorted_buckets)])
    groups = np.split(order, starts[1:])
    slice_times = np.array([times[group].mean() for group in groups], dtype=np.float64)
    slice_centres = np.array([np.median(xyz[group], axis=0) for group in groups], dtype=np.float64)
    slice_counts = counts.astype(np.int64)

    displacement = float(np.linalg.norm(slice_centres[-1] - slice_centres[0])) if len(slice_centres) > 1 else 0.0
    steps = np.linalg.norm(np.diff(slice_centres, axis=0), axis=1) if len(slice_centres) > 1 else np.empty(0)
    trajectory_length = float(steps.sum())
    speed = displacement / duration if duration > 1e-6 else 0.0
    path_speed = trajectory_length / duration if duration > 1e-6 else 0.0
    # A person walks a roughly monotone path, so net displacement is a large
    # fraction of the travelled length.  A wandering centroid (sparse noise
    # chained across slices) has a long path but almost no net progress.
    straightness = displacement / trajectory_length if trajectory_length > 1e-6 else 0.0
    rms, r2, velocity, split_rms, branches, cut = _regression(slice_times, slice_centres)
    axis_direction = velocity / max(float(np.linalg.norm(velocity)), 1e-9)
    # A band is "directional" if it is straight, or if it splits into two straight
    # parts around the turn.  Evaluating only the whole cloud would reject anyone
    # who changes heading.
    linearity_branches = linearity
    if cut is not None:
        boundary = unique_buckets[cut]
        head = buckets < boundary
        tail = ~head
        if head.sum() >= 3 and tail.sum() >= 3:
            head_linearity, _, _ = _principal_axis(xyz[head])
            tail_linearity, _, _ = _principal_axis(xyz[tail])
            linearity_branches = (head_linearity + tail_linearity) / 2.0

    expected = int(math.floor(duration / config.slice_seconds)) + 1
    coverage = float(len(unique_buckets) / expected) if expected > 0 else 1.0

    basis = _plane_basis(axis_direction)
    boxes = [_box(xyz[group], config) for group in groups]
    sizes = np.array([high - low for low, high in boxes], dtype=np.float64)
    local_size = np.median(sizes, axis=0)
    local_size_norm = float(np.linalg.norm(local_size))
    # Body size must come from the cross-section, never from the travelled path.
    cross = np.array([_cross_section(xyz[group], basis, config) for group in groups])
    local_cross = np.median(cross, axis=0)
    local_cross_norm = float(np.linalg.norm(local_cross))

    return dict(
        point_count=int(len(indices)),
        duration=round(duration, 4),
        time_start=round(t_min, 4),
        time_end=round(t_max, 4),
        spatial_span=round(spatial_span, 4),
        extent=extent.round(4).tolist(),
        start=slice_centres[0].round(4).tolist() if len(slice_centres) else [0.0, 0.0, 0.0],
        end=slice_centres[-1].round(4).tolist() if len(slice_centres) else [0.0, 0.0, 0.0],
        centroid=xyz.mean(axis=0).round(4).tolist(),
        displacement=round(displacement, 4),
        trajectory_length=round(trajectory_length, 4),
        speed=round(speed, 4),
        path_speed=round(path_speed, 4),
        straightness=round(straightness, 4),
        linearity=round(linearity, 4),
        linearity_branches=round(linearity_branches, 4),
        planarity=round(planarity, 4),
        main_direction=axis_direction.round(5).tolist(),
        regression_rms=round(rms, 4),
        regression_r2=round(r2, 4),
        regression_rms_split=round(split_rms, 4),
        branches=branches,
        slice_count=int(len(unique_buckets)),
        expected_slices=expected,
        coverage=round(coverage, 4),
        local_size=local_size.round(4).tolist(),
        local_size_norm=round(local_size_norm, 4),
        local_cross_size=local_cross.round(4).tolist(),
        local_cross_norm=round(local_cross_norm, 4),
        slices=[dict(bucket=int(bucket), time=round(float(slice_times[i]), 4),
                     count=int(slice_counts[i]), centre=slice_centres[i].round(4).tolist())
                for i, bucket in enumerate(unique_buckets)],
    )


def _accept(statistics: dict, config: BandConfig) -> tuple[bool, list[str]]:
    """Configurable acceptance test: every criterion must hold."""
    reasons = []
    if statistics["duration"] < config.min_duration:
        reasons.append("duration")
    if statistics["slice_count"] < config.min_slices:
        reasons.append("slices")
    if statistics["displacement"] < config.min_displacement:
        reasons.append("displacement")
    if statistics["speed"] < config.min_speed:
        reasons.append("speed")
    if statistics["speed"] > config.max_speed or statistics["path_speed"] > config.max_speed:
        reasons.append("speed_high")
    if statistics["coverage"] < config.min_coverage:
        reasons.append("coverage")
    if statistics["straightness"] < config.min_straightness:
        reasons.append("straightness")
    if statistics["point_count"] < config.min_point_count:
        reasons.append("point_count")
    if max(statistics["linearity"], statistics["linearity_branches"]) < config.min_linearity:
        reasons.append("linearity")
    if min(statistics["regression_rms"], statistics["regression_rms_split"]) > config.max_regression_rms:
        reasons.append("regression")
    if max((slice_["count"] for slice_ in statistics["slices"]), default=0) < config.min_local_points:
        reasons.append("local_points")
    return (not reasons), reasons


def _band_score(statistics: dict, config: BandConfig) -> float:
    """Rank accepted bands; higher means a more convincing moving target."""
    def ratio(value, reference):
        return float(min(1.0, value / reference)) if reference > 0 else 0.0

    return round(
        0.25 * ratio(statistics["duration"], 4 * config.min_duration)
        + 0.25 * ratio(statistics["displacement"], 4 * config.min_displacement)
        + 0.20 * ratio(statistics["coverage"], 0.8)
        + 0.15 * ratio(max(statistics["linearity"], statistics["linearity_branches"]),
                        4 * config.min_linearity)
        + 0.15 * ratio(statistics["regression_r2"], 0.95), 4)


def detect_bands(points: CandidatePoints, frame_times: np.ndarray, config: BandConfig) -> BandDetection:
    """Cluster candidates in 4D, score bands, and instantiate per-slice targets."""
    config.validate()
    origin = float(frame_times[0]) if len(frame_times) else 0.0
    empty = BandDetection(config=config, bands=[], rejected=0,
                          point_band=np.full(len(points), -1, np.int32),
                          point_cluster=np.full(len(points), -1, np.int32),
                          point_slice=np.full(len(points), -1, np.int32),
                          targets_by_frame={},
                          stats=dict(version=BAND_VERSION, candidate_points=len(points),
                                     occupied_voxels=0, voxel_factor=1.0, raw_clusters=0,
                                     accepted_bands=0, merged_fragments=0, rejected_bands=0,
                                     rejected_reasons={}))
    if len(points) == 0:
        return empty

    labels, occupied_voxels, voxel_factor = cluster_candidates(points, config)
    point_slice = _slices(points.time, origin, config.slice_seconds).astype(np.int32)
    point_cluster = labels.astype(np.int32)

    raw_groups = [np.flatnonzero(labels == label) for label in np.unique(labels) if label >= 0]
    raw_groups = [group for group in raw_groups if len(group) >= config.slice_min_points]

    static: list[dict] = []
    for group in raw_groups:
        statistics = _band_statistics(points, group, config, origin)
        if statistics is not None:
            static.append(statistics)

    accepted: list[tuple[np.ndarray, dict]] = []
    rejected = 0
    reasons: dict[str, int] = {}
    for group, statistics in zip(raw_groups, static):
        ok, failed = _accept(statistics, config)
        if ok:
            accepted.append((group, statistics))
        else:
            rejected += 1
            statistics["rejected_because"] = failed
            for name in failed:
                reasons[name] = reasons.get(name, 0) + 1

    merged_fragments = _merge_bands(accepted, points, config, origin)

    bands = []
    point_band = np.full(len(points), -1, np.int32)
    for identifier, (group, statistics) in enumerate(accepted):
        point_band[group] = identifier
        statistics.update(band_id=identifier, label=f"B{identifier:03d}",
                          score=_band_score(statistics, config))
        bands.append(statistics)
    bands.sort(key=lambda item: (-item["score"], item["time_start"]))

    targets_by_frame = _instantiate_targets(points, frame_times, accepted, point_band, config, origin)

    return BandDetection(
        config=config, bands=bands, rejected=rejected, point_band=point_band,
        point_cluster=point_cluster, point_slice=point_slice,
        targets_by_frame=targets_by_frame,
        stats=dict(version=BAND_VERSION, candidate_points=len(points), occupied_voxels=occupied_voxels,
                   voxel_factor=voxel_factor,                    raw_clusters=len(raw_groups),
                   accepted_bands=len(bands), merged_fragments=merged_fragments,
                   rejected_bands=rejected, rejected_reasons=reasons))


def _merge_bands(accepted: list[tuple[np.ndarray, dict]], points: CandidatePoints,
                 config: BandConfig, origin: float) -> int:
    """Join accepted fragments that continue the same path; returns the join count.

    One person intermittently observed - or briefly occluded - otherwise becomes
    several bands.  Two fragments are joined when the later one starts shortly
    after the earlier one ends, close to where it ended, and both travel roughly
    the same way.
    """
    limit = math.cos(math.radians(config.merge_max_angle_deg))
    joins = 0
    for _ in range(4):
        combined = False
        for i in range(len(accepted)):
            if accepted[i] is None:
                continue
            for j in range(i + 1, len(accepted)):
                if accepted[j] is None:
                    continue
                left, right = accepted[i][1], accepted[j][1]
                if right["time_start"] < left["time_start"]:
                    left, right = right, left
                gap = right["time_start"] - left["time_end"]
                if gap < 0 or gap > config.merge_max_gap:
                    continue
                offset = np.asarray(right["start"], dtype=np.float64) - np.asarray(left["end"], dtype=np.float64)
                distance = float(np.linalg.norm(offset))
                if distance > config.merge_max_distance:
                    continue
                direction = np.asarray(left["main_direction"], dtype=np.float64)
                heading = np.asarray(right["main_direction"], dtype=np.float64)
                if float(np.dot(direction, heading)) < limit:
                    continue
                if distance > 1e-6 and float(np.dot(direction, offset / distance)) < limit:
                    continue
                group = np.concatenate([accepted[i][0], accepted[j][0]])
                statistics = _band_statistics(points, group, config, origin)
                if statistics is None:
                    continue
                accepted[i] = (group, statistics)
                accepted[j] = None
                joins += 1
                combined = True
        if not combined:
            break
    survivors = [item for item in accepted if item is not None]
    accepted.clear()
    accepted.extend(survivors)
    return joins


def _smooth(values: np.ndarray, width: int) -> np.ndarray:
    """Edge-padded centred moving average; keeps the array length."""
    width = max(1, min(width, len(values)))
    if width <= 1:
        return values.astype(np.float64)
    left = width // 2
    right = width - 1 - left
    padded = np.pad(values.astype(np.float64), ((left, right), (0, 0)), mode="edge")
    kernel = np.ones(width) / width
    return np.column_stack([np.convolve(padded[:, axis], kernel, mode="valid") for axis in range(values.shape[1])])


def _instantiate_targets(points: CandidatePoints, frame_times: np.ndarray,
                         accepted: list[tuple[np.ndarray, dict]], point_band: np.ndarray,
                         config: BandConfig, origin: float) -> dict:
    """Recover one instantaneous target per time slice for every accepted band."""
    targets_by_frame: dict[int, list[dict]] = {}
    frame_buckets = _slices(frame_times.astype(np.float64), origin, config.slice_seconds) if len(frame_times) \
        else np.empty(0, np.int64)

    for identifier, (group, statistics) in enumerate(accepted):
        xyz = points.position[group].astype(np.float64)
        times = points.time[group].astype(np.float64)
        buckets = _slices(times, origin, config.slice_seconds)
        order = np.argsort(buckets, kind="stable")
        sorted_buckets = buckets[order]
        starts = np.r_[0, np.flatnonzero(np.diff(sorted_buckets)) + 1]
        observed = sorted_buckets[starts]
        groups = np.split(order, starts[1:])
        counts = np.diff(np.r_[starts, len(sorted_buckets)]).astype(np.int64)

        centres = np.array([np.median(xyz[group], axis=0) for group in groups], dtype=np.float64)
        boxes = [_box(xyz[group], config) for group in groups]
        means = np.array([times[group].mean() for group in groups], dtype=np.float64)

        span = np.arange(int(observed[0]), int(observed[-1]) + 1)
        if len(observed) > 1:
            filled = np.column_stack([np.interp(span, observed, centres[:, axis]) for axis in range(3)])
            filled_times = np.interp(span, observed, means)
        else:
            filled = np.repeat(centres, len(span), axis=0)
            filled_times = np.repeat(means, len(span))
        smoothed = _smooth(filled, config.smoothing_slices)

        gradient = np.zeros_like(smoothed)
        if len(span) > 1:
            edge = np.gradient(filled_times)
            edge[edge == 0] = config.slice_seconds
            for axis in range(3):
                gradient[:, axis] = np.gradient(smoothed[:, axis]) / edge
        speed = np.linalg.norm(gradient, axis=1)
        # Reject single-step spikes from residual noise without hiding real turns.
        ceiling = max(config.max_speed, 1e-6)
        over = speed > ceiling
        if over.any():
            scale = np.where(over, ceiling / np.maximum(speed, 1e-9), 1.0)
            gradient = gradient * scale[:, None]
            speed = np.linalg.norm(gradient, axis=1)

        observed_lookup = {int(b): index for index, b in enumerate(observed)}
        for position, bucket in enumerate(span):
            index = observed_lookup.get(int(bucket))
            if index is None:
                if counts.size == 0:
                    continue
                gaps = np.abs(observed - bucket)
                if int(gaps.min()) > config.max_gap_slices:
                    continue
            low, high = (boxes[index] if index is not None else (smoothed[position] - config.min_box_extent / 2,
                                                                 smoothed[position] + config.min_box_extent / 2))
            state = dict(
                band_id=identifier, id=identifier, label=f"B{identifier:03d}",
                status="CONFIRMED" if index is not None else "MISSING",
                position=smoothed[position].round(4).tolist(),
                velocity=gradient[position].round(4).tolist(),
                speed=round(float(speed[position]), 4),
                minimum=np.asarray(low).round(4).tolist(),
                maximum=np.asarray(high).round(4).tolist(),
                size=(np.asarray(high) - np.asarray(low)).round(4).tolist(),
                point_count=int(counts[index]) if index is not None else 0,
                slice_index=int(bucket),
                time=round(float(filled_times[position]), 4),
                duration=statistics["duration"], displacement=statistics["displacement"],
                trajectory_length=statistics["trajectory_length"], linearity=statistics["linearity"],
                score=statistics["score"], time_start=statistics["time_start"],
                time_end=statistics["time_end"],
            )
            for frame_index in np.flatnonzero(frame_buckets == bucket):
                targets_by_frame.setdefault(int(frame_index), []).append(state)
    return targets_by_frame


def summarise_bands(detection: BandDetection) -> dict:
    """Serialisable band section for the analysis summary.

    ``slices`` is kept: it is the per-time-slice centroid track, which the WebUI
    draws as the band trail and the CSV export writes as one row per slice.
    """
    return dict(
        version=BAND_VERSION,
        config={key: value for key, value in detection.config.__dict__.items()},
        band_count=len(detection.bands),
        rejected_bands=detection.rejected,
        diagnostics=detection.stats,
        bands=detection.bands,
        note=("band count counts spatio-temporal fragments, not people; band length is the "
              "travelled path during observation, not body size"),
    )

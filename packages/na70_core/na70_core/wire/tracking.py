"""Stage: multi-window line association and freshness bucketing.

Associates candidates across detection windows by spatial gate (position,
direction) without ever upgrading a prediction to a measurement. The output
separates three buckets per the wire architecture:

  * MEASURED_CURRENT  - candidate has current-window raw support;
  * PREDICTED_RECENT  - no current support, but a track entry has a prior
    measured geometry and its age stays within max_prediction_age_s;
  * EXPIRED_HISTORY   - older track entries kept for review only.

A prediction carries the *last measured* geometry verbatim plus its age; it
never produces a new nearest distance or a new inlier set.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math

import numpy as np


SCHEMA_VERSION = "na70-wire-tracking-v1"


@dataclass(frozen=True)
class TrackGeometry:
    direction: tuple
    point_on_line: tuple
    segment_endpoints: tuple | None
    nearest_distance_m: float | None

    @classmethod
    def from_candidate(cls, candidate: dict) -> "TrackGeometry":
        seg = candidate.get("segment_endpoints_sensor_m")
        return cls(
            direction=tuple(float(v) for v in candidate["direction"]),
            point_on_line=tuple(float(v) for v in candidate["point_on_line"]),
            segment_endpoints=tuple(tuple(float(v) for v in p) for p in seg) if seg else None,
            nearest_distance_m=candidate.get("nearest_distance_m"),
        )


@dataclass
class TrackEntry:
    track_id: str
    geometry: TrackGeometry
    last_measured_time_s: float
    observation_count: int = 1
    source_window_ids: list = field(default_factory=list)

    def signature(self) -> str:
        payload = {
            "direction": self.geometry.direction,
            "point_on_line": self.geometry.point_on_line,
            "segment": self.geometry.segment_endpoints,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class AssociationGates:
    max_point_distance_m: float = 5.0
    max_direction_angle_deg: float = 10.0
    max_prediction_age_s: float = 5.0


def associate_candidate(entries: list[TrackEntry], candidate: dict,
                        measured_time_s: float, window_id: str,
                        gates: AssociationGates) -> list[TrackEntry]:
    """Match candidate to an existing track or create a new one."""
    geometry = TrackGeometry.from_candidate(candidate)
    best = None
    best_gap = None
    for entry in entries:
        if measured_time_s - entry.last_measured_time_s > gates.max_prediction_age_s * 4:
            continue  # keep long-dead tracks out of association entirely
        point_gap = math.dist(geometry.point_on_line, entry.geometry.point_on_line)
        d = np.asarray(entry.geometry.direction)
        d2 = np.asarray(geometry.direction)
        angle = float(np.degrees(np.arccos(min(1.0, abs(float(d @ d2)))))) \
            if float(d @ d) > 0 and float(d2 @ d2) > 0 else 90.0
        if point_gap <= gates.max_point_distance_m and angle <= gates.max_direction_angle_deg:
            if best_gap is None or point_gap < best_gap:
                best = entry
                best_gap = point_gap
    if best is not None:
        best.geometry = geometry
        best.last_measured_time_s = measured_time_s
        best.observation_count += 1
        if window_id not in best.source_window_ids:
            best.source_window_ids.append(window_id)
        return entries
    new_id = f"trk-{len(entries):03d}-{geometry.direction[0]:+.3f}"
    entries.append(TrackEntry(new_id, geometry, measured_time_s, 1, [window_id]))
    return entries


def bucket_outputs(entries: list[TrackEntry], current_window_id: str,
                   now_s: float, gates: AssociationGates) -> dict:
    """Split track entries into evidence buckets relative to the latest window."""
    measured, predicted, expired = [], [], []
    for entry in entries:
        age = now_s - entry.last_measured_time_s
        if age < 0:
            raise ValueError("track last-measured time is in the future")
        if current_window_id in entry.source_window_ids and age <= 1e-6:
            measured.append(entry)
        elif age <= gates.max_prediction_age_s:
            predicted.append(entry)
        else:
            expired.append(entry)
    measured.sort(key=lambda e: e.track_id)
    predicted.sort(key=lambda e: e.track_id)
    expired.sort(key=lambda e: e.track_id)
    return {"MEASURED_CURRENT": measured,
            "PREDICTED_RECENT": predicted,
            "EXPIRED_HISTORY": expired}

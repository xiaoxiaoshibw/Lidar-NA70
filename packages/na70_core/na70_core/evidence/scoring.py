"""Reviewed-only scorer (W4-B contract).

The scorer consumes:
  * a set of `wire-truth-v1` entries (JSON); only entries whose
    review_status == "reviewed" are counted. Unreviewed entries are listed
    but never enter TP/FN/FP denominators;
  * one or more detection-output rows (the `candidate.json` produced by the
    W3/W4 offline runners, or equivalent dictionaries).

Matching rules (frozen at W4-B entry; hit radius and class rules must not
change after the first reviewed run):
  * a detection row matches a truth segment when |direction dot| >= 0.995
    AND the nearest truth endpoint-to-line distance <= hit_radius_m;
  * an unmatched detection on a target_absent row is an FP;
  * truth with no matching detection is an FN;
  * rows whose truth has review_status != "reviewed" produce NOT_EVALUATED
    rows in the output and are excluded from denominators.

When there are zero reviewed rows the scorer reports status NOT_EVALUATED
with null metrics - never zeros.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np


SCHEMA_VERSION = "na70-wire-score-v1"


@dataclass(frozen=True)
class ScoreRow:
    label: str
    truth_id: str | None
    status: str  # TP|FP|FN|NOT_EVALUATED
    direction_angle_deg: float | None
    span_overlap_m: float | None

    def to_dict(self):
        return {"label": self.label, "truth_id": self.truth_id, "status": self.status,
                "direction_angle_deg": self.direction_angle_deg,
                "span_overlap_m": self.span_overlap_m}


def _point_segment_distance(point, seg_start, seg_end) -> float:
    p = np.asarray(point, dtype=float)
    a = np.asarray(seg_start, dtype=float)
    b = np.asarray(seg_end, dtype=float)
    ab = b - a
    denom = float(ab @ ab)
    if denom == 0:
        return float(np.linalg.norm(p - a))
    t = float(np.clip(((p - a) @ ab) / denom, 0.0, 1.0))
    return float(np.linalg.norm(p - (a + t * ab)))


def _match(detections, truth_entry, hit_radius_m: float):
    target = truth_entry.get("target") or {}
    endpoints = target.get("endpoints_m")
    if not endpoints:
        return []
    p0, p1 = np.asarray(endpoints[0], dtype=float), np.asarray(endpoints[1], dtype=float)
    truth_dir = p1 - p0
    norm = float(np.linalg.norm(truth_dir))
    if norm == 0:
        return []
    truth_dir = truth_dir / norm
    hits = []
    for row_index, row in enumerate(detections):
        candidate = (row.get("candidate_line") or row.get("candidate") or {})
        direction = candidate.get("direction")
        point_on_line = candidate.get("point_on_line")
        if not direction or not point_on_line:
            continue
        direction = np.asarray(direction, dtype=float)
        point_on_line = np.asarray(point_on_line, dtype=float)
        dot = float(abs(direction @ truth_dir))
        distance = _point_segment_distance(point_on_line, p0, p1)
        if dot >= 0.995 and distance <= hit_radius_m:
            hits.append((row_index, row, dot, distance))
    return hits


def score_rows(detections: list[dict], truth_entries: list[dict],
               hit_radius_m: float = 5.0) -> dict:
    reviewed = [t for t in truth_entries if t.get("review_status") == "reviewed"]
    rows: list[ScoreRow] = []
    used_detection: set[int] = set()
    tp = fp = fn = 0
    for truth in reviewed:
        truth_id = truth.get("truth_set_id")
        label = f"truth:{truth_id}"
        if truth.get("capture_role") == "target_absent":
            for idx, row in enumerate(detections):
                if idx in used_detection:
                    continue
                rows.append(ScoreRow(f"{label}:detection-{idx}", truth_id, "FP", None, None))
                used_detection.add(idx)
                fp += 1
            continue
        # one-to-one matching: an unused detection may satisfy at most one
        # present-truth, and a present-truth accepts at most one detection
        # (the best dot/distance). Every other hit on the same truth is
        # still reported but as an FP of the same window - the contract
        # counts shared-support detections once, per DEBUG_ACCEPTANCE §5.
        remaining_idx = [i for i in range(len(detections)) if i not in used_detection]
        remaining_rows = [detections[i] for i in remaining_idx]
        hits = _match(remaining_rows, truth, hit_radius_m)
        if not hits:
            rows.append(ScoreRow(label, truth_id, "FN", None, None))
            fn += 1
            continue
        best_local = max(range(len(hits)),
                         key=lambda i: (hits[i][2], -hits[i][3]))
        # 'hits' rows are (local_index_in_remaining, row, dot, distance).
        local_idx, row, dot, distance = hits[best_local]
        global_idx = remaining_idx[local_idx]
        used_detection.add(global_idx)
        direction_error = float(np.degrees(np.arccos(min(1.0, dot))))
        rows.append(ScoreRow(f"{label}:detection-{global_idx}", truth_id, "TP",
                             direction_error, None))
        tp += 1
        for other_local, _row, _dot, _distance in hits:
            if other_local == local_idx:
                continue
            other_global = remaining_idx[other_local]
            if other_global in used_detection:
                continue
            used_detection.add(other_global)
            rows.append(ScoreRow(
                f"{label}:detection-{other_global}", truth_id, "FP",
                None, None))
            fp += 1
    for idx, row in enumerate(detections):
        if idx not in used_detection and reviewed:
            rows.append(ScoreRow(f"detection-{idx}", None, "FP", None, None))
            fp += 1
        elif idx not in used_detection and not reviewed:
            rows.append(ScoreRow(f"detection-{idx}", None, "NOT_EVALUATED", None, None))

    total_pos = tp + fn
    total_pred = tp + fp
    metrics = {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": (tp / total_pred) if total_pred else None,
        "recall": (tp / total_pos) if total_pos else None,
        "fp_per_minute": None,  # needs reviewed background duration; never zero-fill
    }
    status = "EVALUATED" if reviewed else "NOT_EVALUATED"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "hit_radius_m": hit_radius_m,
        "reviewed_truth_count": len(reviewed),
        "detection_row_count": len(detections),
        "metrics": metrics,
        "rows": [r.to_dict() for r in rows],
        "unreviewed_truth_count": len(truth_entries) - len(reviewed),
    }


def score_run_directory(run_dir: Path, truth_path: Path,
                        hit_radius_m: float = 5.0) -> dict:
    run_dir = Path(run_dir)
    detections = []
    candidate_file = run_dir / "candidate.json"
    if candidate_file.is_file():
        detections.append(json.loads(candidate_file.read_text("utf-8")))
    compare_file = run_dir / "compare.jsonl"
    if compare_file.is_file():
        detections.extend(json.loads(line) for line in
                          compare_file.read_text("utf-8").splitlines() if line.strip())
    truth_entries = json.loads(Path(truth_path).read_text("utf-8"))
    if isinstance(truth_entries, dict) and "truths" in truth_entries:
        truth_entries = truth_entries["truths"]
    if not isinstance(truth_entries, list):
        raise ValueError("truth file must contain a list (or {\"truths\": [...]})")
    return score_rows(detections, truth_entries, hit_radius_m)

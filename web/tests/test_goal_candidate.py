"""GoalDetector on synthetic frame geometry (SYNTHETIC)."""
import numpy as np
import pytest

from backend.goal_detection.config import load_configuration
from backend.goal_detection.detector import GoalDetector


def pole(cx, cy, z0=0.0, z1=2.44, step=0.15, jitter=0.0, rng=None):
    z = np.arange(z0, z1, step)
    xy = np.column_stack((np.full(len(z), cx), np.full(len(z), cy)))
    if jitter and rng is not None:
        xy = xy + rng.normal(0.0, jitter, xy.shape)
    return np.column_stack((xy, z))


def goal_frame(cx=100.0, cy=0.0, width=5.4, crossbar=True, jitter=0.0, rng=None,
               intensity=100.0):
    parts = [pole(cx, cy - width / 2, jitter=jitter, rng=rng),
             pole(cx, cy + width / 2, jitter=jitter, rng=rng)]
    if crossbar:
        samples = np.linspace(0.0, 1.0, 41)
        bar_y = cy - width / 2 + samples * width
        parts.append(np.column_stack((np.full(len(bar_y), cx), bar_y,
                                      np.full(len(bar_y), 2.44))))
    points = np.vstack(parts)
    return np.column_stack((points, np.full(len(points), intensity)))


def detector(min_confidence=0.5):
    cfg = load_configuration()
    cfg["goal_detection"]["scoring"]["min_confidence"] = min_confidence
    det = GoalDetector(cfg["goal_detection"])
    det.set_range(50.0, 130.0)
    return det


def test_full_frame_scores_high():
    best = detector().detect(goal_frame())[0]
    assert best.confidence >= 0.7
    assert best.crossbar > 0.8
    assert abs(best.size[1] - 5.4) < 1.0
    assert abs(best.center[2] - 1.22) < 0.8
    debug = best.as_dict()
    assert debug["candidate_id"] == "candidate-1"
    assert debug["point_count"] == len(debug["supporting_point_indices"])
    assert debug["point_count"] > 20
    assert set(debug["score_breakdown"]) >= {"geometry_score", "crossbar_score"}
    assert debug["detection_score"] == debug["confidence"]


def test_missing_crossbar_loses_crossbar_score():
    bare = detector().detect(goal_frame(crossbar=False))[0]
    full = detector().detect(goal_frame())[0]
    assert bare.crossbar == 0.0
    assert bare.confidence < full.confidence


def test_wrong_width_pairs_rejected():
    det = detector()
    assert det.detect(goal_frame(width=2.0)) == []
    assert det.last_rejected
    assert det.last_rejected[0].as_dict()["reject_reason"] == "width_too_small"
    assert det.last_rejected[0].as_dict()["accepted"] is False
    assert det.last_rejected_summary["total"] >= det.last_rejected_summary["returned"]


def test_ground_band_points_excluded():
    rng = np.random.default_rng(3)
    ground = np.column_stack((rng.uniform(95, 105, (500, 2)),
                              rng.uniform(-2.0, 0.2, 500),
                              np.full(500, 100.0)))
    best = detector().detect(np.vstack((goal_frame(), ground)))[0]
    assert best.confidence >= 0.7


def test_empty_and_nan_input():
    det = detector()
    assert det.detect(np.empty((0, 4))) == []
    assert det.detect(np.full((10, 4), np.nan)) == []
    assert det.detect(np.zeros((5, 3))) == []


def test_range_gate_applies_inside_detector():
    det = detector()
    assert det.detect(goal_frame(cx=10.0)) == []
    assert det.detect(goal_frame(cx=100.0))


def test_candidates_sorted_by_confidence():
    det = detector(min_confidence=0.0)
    candidates = det.detect(np.vstack((goal_frame(), goal_frame(crossbar=False))))
    confidences = [c.confidence for c in candidates]
    assert confidences == sorted(confidences, reverse=True)


def test_pole_jitter_still_detected():
    rng = np.random.default_rng(5)
    best = detector().detect(goal_frame(jitter=0.05, rng=rng))[0]
    assert best.confidence >= 0.6


def test_disabling_diagnostics_does_not_change_detection():
    points = goal_frame()
    cfg = load_configuration()
    enabled = GoalDetector(cfg["goal_detection"])
    enabled.set_range(50.0, 130.0)
    expected = [candidate.as_dict() for candidate in enabled.detect(points)]
    cfg["goal_detection"]["diagnostics"]["enabled"] = False
    disabled = GoalDetector(cfg["goal_detection"])
    disabled.set_range(50.0, 130.0)
    actual = [candidate.as_dict() for candidate in disabled.detect(points)]
    assert actual == expected
    diagnostic = next(stage for stage in disabled.last_trace
                      if stage["stage"] == "rejected_candidate_diagnostics")
    assert diagnostic["stage"] == "rejected_candidate_diagnostics"
    assert diagnostic["status"] == "skipped" and diagnostic["latency_ms"] is None


def test_detector_layered_fallback_keeps_two_posts_and_provenance():
    points = goal_frame(cx=100.0, cy=0.0, jitter=0.03,
                        rng=np.random.default_rng(17))
    cfg = load_configuration()
    # Force the regular grid path to fail without weakening the pole geometry
    # gates; the layered fallback must recover both posts independently.
    cfg["goal_detection"]["cell_size"] = 0.05
    det = GoalDetector(cfg["goal_detection"])
    det.set_range(50.0, 130.0)
    candidates = det.detect(points)
    fallback = next(stage for stage in det.last_trace
                    if stage["stage"] == "local_3d_fallback")
    assert fallback["output_entities"] == 2
    assert fallback["crossbar_layer_points"] > 0
    assert candidates
    assert all(0 <= index < len(points)
               for index in candidates[0].supporting_point_indices)

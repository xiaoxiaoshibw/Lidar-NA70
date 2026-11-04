"""Contract tests for tracking buckets, structured reject_counts, and the
reviewed-only scorer."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

import numpy as np
import pytest

from na70_core.wire import (AssociationGates, TrackEntry, TrackGeometry,
                            associate_candidate, bucket_outputs, build_scenario)
from na70_core.wire.fitting import fit_line_ransac
from na70_core.wire.proposals import make_manual_roi_proposal
from na70_core.wire.validation import evaluate_finite_support
from na70_core.wire import load_wire_config
from na70_core.evidence.scoring import score_rows, score_run_directory


def _cand(direction=(0.0, 1.0, 0.0), point=(50.0, 0.0, 8.0),
          seg=((50.0, -10.0, 8.0), (50.0, 10.0, 8.0)), nearest=50.0):
    return {"direction": list(direction), "point_on_line": list(point),
            "segment_endpoints_sensor_m": [list(seg[0]), list(seg[1])],
            "nearest_distance_m": nearest}


def test_tracking_buckets_separate_current_predicted_expired():
    gates = AssociationGates(max_prediction_age_s=2.0)
    entries = []
    entries = associate_candidate(entries, _cand(), 10.0, "w1", gates)
    entries = associate_candidate(entries, _cand(point=(50.0, 0.5, 8.0)), 11.0, "w2", gates)
    assert len(entries) == 1
    assert entries[0].observation_count == 2

    buckets = bucket_outputs(entries, "w2", 11.0, gates)
    assert [e.track_id for e in buckets["MEASURED_CURRENT"]] == [entries[0].track_id]
    assert not buckets["PREDICTED_RECENT"]
    assert not buckets["EXPIRED_HISTORY"]

    buckets = bucket_outputs(entries, "w3", 12.5, gates)
    assert not buckets["MEASURED_CURRENT"]
    assert [e.track_id for e in buckets["PREDICTED_RECENT"]] == [entries[0].track_id]
    assert not buckets["EXPIRED_HISTORY"]

    buckets = bucket_outputs(entries, "w4", 14.5, gates)
    assert not buckets["PREDICTED_RECENT"]
    assert [e.track_id for e in buckets["EXPIRED_HISTORY"]] == [entries[0].track_id]


def test_tracking_never_upgrades_prediction_geometry():
    gates = AssociationGates()
    entries = associate_candidate([], _cand(), 5.0, "w1", gates)
    snapshot = entries[0].geometry
    bucket_outputs(entries, "w_other", 5.5, gates)
    far = [e for e in bucket_outputs(entries, "w_other", 11.5, gates)["EXPIRED_HISTORY"]]
    assert far and far[0].geometry == snapshot  # prediction carries the measured geometry verbatim


def test_finite_support_reject_counts_structured():
    scene = build_scenario("trees_only_negative", 4000)
    cfg = load_wire_config()
    proposal = make_manual_roi_proposal(scene.batch, tuple(scene.roi_aabb_m))
    fit = fit_line_ransac(scene.batch, proposal, cfg.fit)
    assert fit.ok
    support = evaluate_finite_support(scene.batch, fit, cfg.validation, 100.0)
    assert not support.ok
    assert support.reject_counts.get("SPARSE_SUPPORT") == 1
    assert sum(support.reject_counts.values()) == support.reject_reason.count(";") + 1


def test_scorer_unreviewed_truth_yields_not_evaluated_null_metrics():
    truth_dir = Path(__file__).resolve().parents[2] / "tools/scenarios/wire_truth_example_v1.json"
    run_dir = Path(__file__).resolve().parents[2] / \
        "Plan/evidence/hv-line/20261003-w4a-compare-real-zhong2"
    result = score_run_directory(run_dir, truth_dir)
    assert result["status"] == "NOT_EVALUATED"
    assert result["metrics"]["precision"] is None
    assert result["metrics"]["recall"] is None
    assert result["metrics"]["fp_per_minute"] is None
    assert result["tp" if False else "reviewed_truth_count"] == 0


def test_scorer_match_hit_and_miss():
    truth = [{
        "truth_set_id": "t1", "review_status": "reviewed",
        "capture_role": "target_present",
        "target": {"endpoints_m": [[50, -10, 8], [50, 10, 8]]},
    }]
    hit = {"candidate_line": _cand(), "status": "MEASURED_CURRENT"}
    miss_far = {"candidate_line": _cand(point=(90.0, 0.0, 8.0)),
                "status": "MEASURED_CURRENT"}
    res = score_rows([hit], truth, hit_radius_m=5.0)
    assert res["status"] == "EVALUATED"
    assert res["metrics"]["tp"] == 1 and res["metrics"]["fp"] == 0 and res["metrics"]["fn"] == 0
    res = score_rows([miss_far], truth, hit_radius_m=5.0)
    assert res["metrics"]["tp"] == 0 and res["metrics"]["fn"] == 1 and res["metrics"]["fp"] == 1


def test_scorer_one_truth_one_detection_one_to_one():
    # one reviewed truth, two overlapping detections - only the better fit is
    # TP, the other is FP (no duplicate TP on shared support).
    truth = [{
        "truth_set_id": "t1", "review_status": "reviewed",
        "capture_role": "target_present",
        "target": {"endpoints_m": [[50, -10, 8], [50, 10, 8]]},
    }]
    close = {"candidate_line": _cand(), "status": "MEASURED_CURRENT"}
    close_variant = {"candidate_line": _cand(point=(50.0, 0.5, 8.0)),
                     "status": "MEASURED_CURRENT"}
    res = score_rows([close, close_variant], truth, hit_radius_m=5.0)
    assert res["metrics"]["tp"] == 1, f"expected exactly one TP, got {res['metrics']}"
    assert res["metrics"]["fp"] == 1
    assert res["metrics"]["fn"] == 0


def test_scorer_two_truths_same_detection_only_one_tp():
    # two reviewed truths but only one detection matches both; the detection
    # is consumed by the best-matching truth, the other truth becomes FN
    # (or the detection is reserved) - never TP twice on the same ray set.
    truth = [
        {"truth_set_id": "t1", "review_status": "reviewed",
         "capture_role": "target_present",
         "target": {"endpoints_m": [[50, -10, 8], [50, 10, 8]]}},
        {"truth_set_id": "t2", "review_status": "reviewed",
         "capture_role": "target_present",
         "target": {"endpoints_m": [[50, -10, 8.2], [50, 10, 8.2]]}},
    ]
    hit = {"candidate_line": _cand(), "status": "MEASURED_CURRENT"}
    res = score_rows([hit], truth, hit_radius_m=5.0)
    assert res["metrics"]["tp"] == 1  # single detection, one TP total
    # second truth sees no available detection -> FN
    assert res["metrics"]["fn"] == 1
    assert res["metrics"]["fp"] == 0


def test_scorer_target_absent_counts_fp_not_tp():
    truth = [{
        "truth_set_id": "bg1", "review_status": "reviewed",
        "capture_role": "target_absent", "target": None,
    }]
    rows = score_rows([{"candidate_line": _cand()}], truth, hit_radius_m=5.0)
    assert rows["metrics"]["fp"] == 1
    assert rows["metrics"]["tp"] == 0 and rows["metrics"]["fn"] == 0

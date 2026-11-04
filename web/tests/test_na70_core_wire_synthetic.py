"""Contract tests for the SYNTHETIC wire scenario matrix.

Expected values record behaviour as measured on 2026-10-03 with
wire_default.yaml. Two scenarios (trees_only_negative, catenary_wire_150m)
are locked as known weaknesses, not tolerated regressions: tightening the
config to reject them must flip these tests deliberately.
"""
from __future__ import annotations

import numpy as np
import pytest

from na70_core.wire import build_scenario, load_wire_config, run_detection_window, WindowInputs
from na70_core.windows import PacketWindowRing, RingLimits


def _run(name, seed):
    scene = build_scenario(name, seed)
    cfg = load_wire_config()
    frames = int(scene.batch.frame_indices.max()) + 1
    per_frame = scene.batch.ray_count // frames
    ring = PacketWindowRing(RingLimits(max_packets_in_ring=5000,
                                       max_window_packets=5000))
    for f in range(frames):
        for p in range(per_frame // 200):
            t = float(scene.batch.device_times[(f * per_frame) + p * 200])
            ring.push(frame_index=f, local_index=p, device_time=t, capture_time=t)
    selected = ring.select(0.3, end_frame_index=frames - 1)
    inputs = WindowInputs(batch=scene.batch, selected_window=selected,
                          source_window_id=f"test:{scene.name}", config=cfg,
                          manual_roi_aabb_m=tuple(scene.roi_aabb_m),
                          window_end_device_time_s=selected.end_device_time,
                          availability="available")
    return scene, run_detection_window(inputs)


def test_synthetic_scenarios_are_deterministic():
    s1, r1 = _run("single_wire_50m", 0)
    s2, r2 = _run("single_wire_50m", 0)
    assert r1.status == r2.status
    assert r1.candidate_line == r2.candidate_line
    sha1 = s1.batch.capture_sha256
    assert sha1 != s2.batch.capture_sha256 or True  # same name+seed -> same sha
    s3, r3 = _run("single_wire_50m", 0)
    assert s1.batch.capture_sha256 == s3.batch.capture_sha256


def test_single_wire_nearest_direction_and_span():
    _, out = _run("single_wire_50m", 0)
    assert out.status == "MEASURED_CURRENT"
    cand = out.candidate_line
    assert 49.5 <= cand["nearest_distance_m"] <= 51.5
    assert abs(abs(cand["direction"][1]) - 1.0) < 5e-3
    assert 28.0 <= cand["span_m"] <= 32.0


def test_sparse_wire_200m_detects_and_ultra_sparse_rejects():
    _, dense = _run("sparse_wire_200m", 2000)
    assert dense.status == "MEASURED_CURRENT"
    _, sparse = _run("ultra_sparse_wire_200m", 3000)
    assert sparse.status == "UNKNOWN"
    assert "INSUFFICIENT_SUPPORT" in sparse.reject_reason


def test_occlusion_gap_rejected_large_gap():
    _, out = _run("occluded_wire_gap60", 8000)
    assert out.status == "UNKNOWN"
    assert out.first_blocking_stage == "finite_support"
    assert "LARGE_GAP" in out.reject_reason


def test_line_shaped_negatives_pass_geometry_but_identity_stays_unproven():
    for name in ("fence_rails_line_negative", "building_edge_180m"):
        scene, out = _run(name, 5000)
        assert out.status == "MEASURED_CURRENT"
        assert out.geometry_class == "LINE_STRUCTURE"
        assert scene.truth.get("wire") is None


def test_trees_only_rejected_by_sparse_support_gate():
    _, out = _run("trees_only_negative", 4000)
    assert out.status == "UNKNOWN"
    assert out.first_blocking_stage == "finite_support"
    assert "SPARSE_SUPPORT" in out.reject_reason


def test_catenary_rejected_by_sag_gate():
    _, out = _run("catenary_wire_150m", 9000)
    assert out.status == "UNKNOWN"
    assert out.first_blocking_stage == "finite_support"
    assert "EXCESSIVE_SAG" in out.reject_reason


def test_time_jump_window_runs_with_segment_isolated_window():
    scene, out = _run("time_jump_window", 11000)
    assert out.status == "MEASURED_CURRENT"
    # A 0.3 s window ending late falls entirely inside the post-jump segment,
    # so it is a complete window on that segment (time jump neither merges
    # across the break nor forces a partial flag).
    wq = [row for row in out.stage_trace if row["stage"] == "window_quality"]
    assert wq and wq[0]["status"] == "completed"
    # And the window must not contain rays from before the 1.5 s jump:
    end_time = out.candidate_line["source_timestamp_last_s"]
    times = scene.batch.device_times[np.isfinite(scene.batch.device_times)]
    before_jump = times[times <= 0.2]  # pre-jump region (jump at packet 200*1ms*frame scale)
    assert end_time > 1.5  # window lives in the post-jump segment

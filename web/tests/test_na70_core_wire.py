"""Contract tests for na70_core.wire (manual-ROI line fitting + validation)."""
from __future__ import annotations

import numpy as np
import pytest

from na70_core import RayBatch, direction_known_from_codes
from na70_core.wire import (FitConfig, ProposalConfig, ValidationConfig,
                            WireConfig, evaluate_finite_support, fit_line_ransac,
                            load_wire_config, make_manual_roi_proposal,
                            run_detection_window, validate_wire_config_dict,
                            WindowInputs)
from na70_core.windows import PacketWindowRing, RingLimits


def _frame_time(f, l):
    return 100.0 + (f * 100 + l) * 0.001


def _make_batch(n_frames=8, lines=((10.0, 0.0, 0.0, 1.0, 0.0, 0.0),)):
    """Build a synthetic RayBatch where a subset of rays lies on a line.

    Each frame has 100 packets x 200 rays. Rays whose (frame,packet,point)
    matches a small deterministic pattern lie on a 3-D line; the rest are
    background noise in a distant region. The line is entirely inside the
    manual ROI we use in tests.
    """
    frames = n_frames
    total = frames * 100 * 200
    codes = np.zeros((total, 4), dtype=np.uint16)
    points = np.zeros((total, 4), dtype=np.float32)
    fidx, pidx, ptidx = [], [], []
    i = 0
    for f in range(frames):
        for p in range(100):
            for r in range(200):
                idx = f * 100 * 200 + p * 200 + r
                fidx.append(f); pidx.append(f * 100 + p); ptidx.append(p * 200 + r)
                t = idx % 517
                x, y, z = 12.0 + (t % 5) * 0.0001, 0.5 + (t % 7) * 0.0001, 3.0
                codes[idx] = (int((x ** 2 + y ** 2 + z ** 2) ** 0.5 / 0.016),
                              512, 0, 32)
                points[idx, :3] = (x, y, z)
                points[idx, 3] = 32
                i += 1
    # inject line support in a specific packet subset (packet 30..49 of every
    # frame) - a long horizontal run inside the ROI.
    for f in range(frames):
        for p in range(30, 50):
            for r in range(0, 200, 5):
                idx = f * 100 * 200 + p * 200 + r
                # line parameter along x
                s = (r % 200) * 0.05 + 1.0
                x, y, z = s, 2.0, 8.0
                codes[idx] = (int((x ** 2 + y ** 2 + z ** 2) ** 0.5 / 0.016),
                              512, 0, 48)
                points[idx, :3] = (x, y, z)
                points[idx, 3] = 48
    device_times = np.repeat(np.array([_frame_time(f, p) for f in range(frames) for p in range(100)]),
                             200)
    capture_times = device_times.copy()
    return RayBatch.from_decoded(
        capture_sha256="f" * 64, coordinate_frame="lidar_link",
        install_direction=1, codes=codes, points=points,
        frame_indices=np.array(fidx), packet_indices=np.array(pidx),
        point_indices=np.array(ptidx), device_times=device_times,
        capture_times=capture_times)


def _roi():
    return (1.0, 12.0, 1.0, 4.0, 6.0, 10.0)


def test_wire_config_defaults_load():
    cfg = load_wire_config()
    assert cfg.fit.fixed_seed == 7
    assert cfg.fit.thresh_m == pytest.approx(0.6)


def test_wire_config_rejects_nonfinite():
    with pytest.raises(ValueError):
        validate_wire_config_dict({"schema_version": "na70-wire-config-v1",
                                   "fit": {"thresh_m": -1.0}})


def test_manual_roi_proposal_selects_only_roi():
    batch = _make_batch()
    proposal = make_manual_roi_proposal(batch, _roi())
    xyz = batch.points[proposal.mask, :3]
    aabb = np.asarray(_roi(), dtype=float).reshape(3, 2)
    assert (xyz >= aabb[:, 0]).all() and (xyz <= aabb[:, 1]).all()
    assert proposal.ray_count > 0
    out_mask = np.zeros(batch.ray_count, dtype=bool)
    out_mask[proposal.mask] = True
    assert (batch.points[out_mask, 1] >= 1.0).all()
    assert (batch.points[out_mask, 1] <= 4.0).all()


def test_manual_roi_empty_rejected_by_fit():
    batch = _make_batch()
    empty_roi = (200.0, 250.0, 1.0, 4.0, 6.0, 10.0)
    proposal = make_manual_roi_proposal(batch, empty_roi)
    cfg = load_wire_config()
    fit = fit_line_ransac(batch, proposal, cfg.fit)
    assert not fit.ok
    assert fit.reject_reason == "INSUFFICIENT_SUPPORT"
    assert fit.fitted_ray_count == 0


def test_fit_line_ransac_on_synthetic_line():
    batch = _make_batch()
    cfg = load_wire_config()
    aabb = (1.0, 12.0, 1.0, 4.0, 6.0, 10.0)
    proposal = make_manual_roi_proposal(batch, aabb)
    fit = fit_line_ransac(batch, proposal, cfg.fit)
    assert fit.ok, f"fit rejected: {fit.reject_reason}"
    assert abs(abs(fit.direction[0]) - 1.0) < 1e-3
    assert fit.inlier_ray_indices.size > 0
    # spot-check the line passes through (s,2,8) with s in [1,12]
    origin = fit.point_on_line
    direction = fit.direction
    assert 1.999 <= origin[1] <= 2.001
    assert 7.999 <= origin[2] <= 8.001


def test_fit_rejected_when_background_only():
    batch = _make_batch()
    cfg = load_wire_config()
    # ROI away from the injected line - only noise rows with high y values.
    aabb_noise = (50.0, 100.0, 99.0, 100.0, 0.0, 1.0)
    proposal = make_manual_roi_proposal(batch, aabb_noise)
    fit = fit_line_ransac(batch, proposal, cfg.fit)
    assert not fit.ok


def test_validation_gates_span_coverage_and_rms():
    batch = _make_batch()
    cfg = load_wire_config()
    aabb = (1.0, 12.0, 1.0, 4.0, 6.0, 10.0)
    proposal = make_manual_roi_proposal(batch, aabb)
    fit = fit_line_ransac(batch, proposal, cfg.fit)
    report = evaluate_finite_support(batch, fit, cfg.validation,
                                     window_end_time_s=100.8)
    assert report.ok
    assert report.unique_packet_count > 0
    assert report.span_m > 0
    assert 0 < report.coverage_fraction <= 1.0
    assert report.segment_sensor is not None


def test_validation_rejects_small_support_on_background():
    batch = _make_batch()
    cfg = load_wire_config()
    aabb = (1.02, 1.12, 1.0, 4.0, 6.0, 10.0)  # tiny ROI covering only a slice
    proposal = make_manual_roi_proposal(batch, aabb)
    fit = fit_line_ransac(batch, proposal, cfg.fit)
    if fit.ok:
        report = evaluate_finite_support(batch, fit, cfg.validation,
                                         window_end_time_s=100.8)
        assert not report.ok
        assert "INSUFFICIENT_SUPPORT" in report.reject_reason or "LOW_COVERAGE" in report.reject_reason


def test_pipeline_measured_current_on_real_fit():
    batch = _make_batch()
    cfg = load_wire_config()
    ring = PacketWindowRing(RingLimits(max_packets_in_ring=5000,
                                       max_window_packets=5000))
    for f in range(8):
        for l in range(100):
            t = _frame_time(f, l)
            ring.push(frame_index=f, local_index=l, device_time=t, capture_time=t)
    selected = ring.select(1.0, end_frame_index=7)
    inputs = WindowInputs(batch=batch, selected_window=selected,
                          source_window_id="test", manual_roi_aabb_m=_roi(),
                          config=cfg,
                          window_end_device_time_s=selected.end_device_time)
    out = run_detection_window(inputs)
    assert out.status == "MEASURED_CURRENT"
    assert out.geometry_class == "LINE_STRUCTURE"
    assert out.candidate_line is not None
    stage_names = [row["stage"] for row in out.stage_trace]
    assert stage_names[:6] == ["decode", "time_quality", "direction_quality",
                               "motion_gate", "window_quality", "valid_return"]


def test_pipeline_no_valid_returns_blocks_early():
    n = 200
    codes = np.zeros((n, 4), dtype=np.uint16)
    points = np.zeros((n, 4), dtype=np.float32)
    frame_idx = np.zeros(n, dtype=np.int64)
    pkt_idx = np.zeros(n, dtype=np.int64)
    pt_idx = np.arange(n, dtype=np.int64)
    dev = np.full(n, 100.0)
    batch = RayBatch.from_decoded(capture_sha256="e" * 64,
                                  coordinate_frame="lidar_link",
                                  install_direction=1, codes=codes,
                                  points=points, frame_indices=frame_idx,
                                  packet_indices=pkt_idx, point_indices=pt_idx,
                                  device_times=dev, capture_times=dev)
    cfg = load_wire_config()
    ring = PacketWindowRing(RingLimits(max_packets_in_ring=10, max_window_packets=10))
    ring.push(frame_index=0, local_index=0, device_time=100.0, capture_time=100.0)
    selected = ring.select(None)
    inputs = WindowInputs(batch=batch, selected_window=selected,
                          source_window_id="test", manual_roi_aabb_m=_roi(),
                          config=cfg, window_end_device_time_s=100.0)
    out = run_detection_window(inputs)
    assert out.status == "NO_CURRENT_MEASUREMENT"
    assert out.first_blocking_stage == "valid_return"


def test_fit_rejects_non_finite_points():
    n = 64
    codes = np.zeros((n, 4), dtype=np.uint16); codes[:, 0] = 100
    points = np.zeros((n, 4), dtype=np.float32)
    points[: n // 2, 0] = np.nan
    points[n // 2:, 0] = np.inf
    idx = np.arange(n, dtype=np.int64)
    batch = RayBatch.from_decoded(capture_sha256="d" * 64,
                                  coordinate_frame="lidar_link", install_direction=1,
                                  codes=codes, points=points,
                                  frame_indices=idx, packet_indices=idx, point_indices=idx,
                                  device_times=np.full(n, 100.0),
                                  capture_times=np.full(n, 100.0))
    cfg = load_wire_config()

    # 默认 require_valid=True + require_direction_known=True: NaN/Inf 被淘汰,选不进 ROI,触 INSUFFICIENT_SUPPORT
    proposal = make_manual_roi_proposal(batch, (-1, 1000, -1, 1000, -1, 1000))
    fit = fit_line_ransac(batch, proposal, cfg.fit)
    assert not fit.ok
    assert fit.reject_reason == "INSUFFICIENT_SUPPORT"

    # require_valid=False with finiteness-validated ROI will exclude NaN/Inf
    # through `in_box`; to test the fitting layer itself, bypass the helper's
    # mask composition with a synthetic all-true mask.
    from na70_core.wire.proposals import Proposal
    proposal_all = Proposal(kind="manual_roi", aabb_m=np.array([-1, 1000, -1, 1000, -1, 1000]),
                            mask=np.ones(n, dtype=bool))
    fit = fit_line_ransac(batch, proposal_all, cfg.fit)
    assert not fit.ok
    assert fit.reject_reason == "DEGENERATE_SAMPLE"


def test_fit_rejects_all_identical_points():
    n = 32
    codes = np.zeros((n, 4), dtype=np.uint16); codes[:, 0] = 100
    points = np.zeros((n, 4), dtype=np.float32); points[:, 0] = 5.0
    idx = np.arange(n, dtype=np.int64)
    batch = RayBatch.from_decoded(capture_sha256="c" * 64,
                                  coordinate_frame="lidar_link", install_direction=1,
                                  codes=codes, points=points,
                                  frame_indices=idx, packet_indices=idx, point_indices=idx,
                                  device_times=np.full(n, 100.0),
                                  capture_times=np.full(n, 100.0))
    proposal = make_manual_roi_proposal(batch, (0, 10, -1, 1, -1, 1))
    cfg = load_wire_config()
    fit = fit_line_ransac(batch, proposal, cfg.fit)
    assert not fit.ok
    assert fit.reject_reason == "DEGENERATE_SAMPLE"


def test_pipeline_with_install_direction_2():
    n = 64
    codes = np.zeros((n, 4), dtype=np.uint16); codes[:, 0] = 100
    points = np.zeros((n, 4), dtype=np.float32)
    points[: n // 2, :3] = np.array([5.0, 2.0, 8.0])
    points[: n // 2, 0] = points[: n // 2, 0] + np.arange(n // 2) * 0.1
    points[n // 2:, :3] = np.array([50.0, 50.0, 50.0])
    idx = np.arange(n, dtype=np.int64)
    batch = RayBatch.from_decoded(capture_sha256="b" * 64,
                                  coordinate_frame="lidar_link", install_direction=2,
                                  codes=codes, points=points,
                                  frame_indices=idx, packet_indices=idx,
                                  point_indices=idx,
                                  device_times=np.full(n, 100.0),
                                  capture_times=np.full(n, 100.0))
    proposal = make_manual_roi_proposal(batch, (4.0, 10.0, 1.0, 5.0, 6.0, 10.0))
    cfg = load_wire_config()
    fit = fit_line_ransac(batch, proposal, cfg.fit)
    assert fit.ok or fit.reject_reason in ("INSUFFICIENT_SUPPORT", "FIT_REJECTED")

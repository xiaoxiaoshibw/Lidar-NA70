"""SYNTHETIC fixed-point turntable mapping contract and persistence tests."""
import csv
import json
import math
import time
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.turntable import (alignment_metrics, constrained_refinement,
    load_angle_log, load_calibration, run_turntable_job, turntable_pose)
from test_decoder import make_pcap
from test_mapping_api import scene
from test_mapping_api import upload


def calibration(path: Path, offset=(0.0, 0.0, 0.0), unit="deg"):
    body = {
        "schema_version": 1, "status": "SYNTHETIC", "angle_unit": unit,
        "positive_direction": "counter_clockwise_about_positive_z",
        "map_turntable": {"translation_m": [0, 0, 0], "rpy_deg": [0, 0, 0]},
        "turntable_lidar": {"translation_m": list(offset), "rpy_deg": [0, 0, 0]},
    }
    path.write_text(json.dumps(body), "utf-8")
    return path


def angle_log(path: Path, rows):
    fields = ["segment_id", "source_pcap", "yaw_deg", "settle_start_s", "valid_start_s",
              "valid_end_s", "direction", "valid", "angle_source", "note"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    return path


def row(name="a", source="synthetic.pcap", yaw=0, start=0, end=3.5, valid="true",
        angle_source="ESTIMATED_TEMPORAL"):
    return dict(segment_id=name, source_pcap=source, yaw_deg=yaw, settle_start_s=start,
                valid_start_s=start, valid_end_s=end, direction="test", valid=valid,
                angle_source=angle_source, note="SYNTHETIC")


def test_positive_negative_angle_direction_and_eccentric_rotation(tmp_path):
    cfg = load_calibration(calibration(tmp_path / "cal.json", (1, 0, 0)))
    positive = turntable_pose(90, cfg)
    negative = turntable_pose(-90, cfg)
    assert np.allclose(positive[:3, 3], [0, 1, 0], atol=1e-9)
    assert np.allclose(negative[:3, 3], [0, -1, 0], atol=1e-9)
    assert np.allclose(positive[:3, :3] @ [1, 0, 0], [0, 1, 0], atol=1e-9)


def test_angle_unit_overlap_out_of_range_nan_and_missing_source(tmp_path):
    bad = calibration(tmp_path / "bad.json", unit="rad")
    try:
        load_calibration(bad)
    except ValueError as exc:
        assert "deg" in str(exc)
    else:
        raise AssertionError("radian calibration accepted")
    log = angle_log(tmp_path / "overlap.csv", [row(end=2), row("b", start=1, end=3)])
    for durations, message in (({"synthetic.pcap": 10}, "重叠"), ({"synthetic.pcap": 1}, "超出")):
        try:
            load_angle_log(log, durations)
        except ValueError as exc:
            assert message in str(exc)
        else:
            raise AssertionError("invalid time range accepted")
    nan_log = angle_log(tmp_path / "nan.csv", [row(yaw="nan")])
    try:
        load_angle_log(nan_log, {"synthetic.pcap": 10})
    except ValueError as exc:
        assert "有限" in str(exc)
    else:
        raise AssertionError("NaN angle accepted")
    missing = angle_log(tmp_path / "missing.csv", [row(source="missing.pcap")])
    try:
        load_angle_log(missing, {"synthetic.pcap": 10})
    except ValueError as exc:
        assert "未提供" in str(exc)
    else:
        raise AssertionError("missing source accepted")


def test_constrained_refinement_rejects_limit_or_heldout_regression():
    rng = np.random.default_rng(8)
    reference = rng.normal(size=(500, 3)); reference[:, 2] += 1.5
    source = reference.copy()
    angle = math.radians(4)
    source[:, :2] = source[:, :2] @ np.array([[math.cos(angle), -math.sin(angle)],
                                               [math.sin(angle), math.cos(angle)]])
    result = constrained_refinement(source, reference, np.eye(4), max_yaw_deg=0.2,
                                    max_translation_m=0.01, min_fit_improvement_m=0.001)
    assert result["accepted"] is False
    assert max(abs(value) for value in result["correction"]) <= 0.21


def test_quality_metrics_are_finite_for_rigid_structure():
    rng = np.random.default_rng(4)
    points = rng.normal(size=(1000, 3)); shifted = points + [0.1, 0, 0]
    metrics = alignment_metrics(points, shifted)
    assert 0 < metrics["median_m"] < 0.2
    assert metrics["p95_m"] >= metrics["median_m"]
    assert set(metrics["overlap"]) == {"0.3", "0.5", "1.0"}


def test_job_exports_required_evidence_and_is_plain_files_restart_safe(tmp_path):
    pcap = make_pcap(tmp_path / "synthetic.pcap", scene(5))
    log = angle_log(tmp_path / "angles.csv", [row(end=0.4, angle_source="MANUAL")])
    cal = calibration(tmp_path / "cal.json")
    cfg = {
        "schema_version": 1, "mode": "turntable", "development_label": "SYNTHETIC",
        "angle_unit": "deg", "positive_direction": "counter_clockwise_about_positive_z",
        "min_segment_seconds": 0.1, "frame_stride": 1, "min_range_m": 0.1, "max_range_m": 100,
        "submap_voxel_size_m": 0.5, "map_voxel_size_m": 0.5, "max_submap_voxels": 50000,
        "max_map_voxels": 50000, "minimum_submap_points": 20, "rigid_height_min_m": -100,
        "rigid_height_max_m": 100, "quality_overlap_distance_m": [0.3, 0.5, 1.0],
        "stability_max_median_m": 2, "stability_min_overlap_1m": 0,
        "refinement_enabled": True, "refinement_max_yaw_deg": 0.2,
        "refinement_max_translation_m": 0.01, "refinement_min_fit_improvement_m": 0.001,
        "preview_size_px": 128, "preview_max_points_per_source": 1000,
    }
    output = tmp_path / "job"
    result = run_turntable_job([pcap], log, cal, output, cfg, "SYNTHETIC command")
    assert result["job"]["status"] == "ready"
    assert result["qualities"][0]["quality_status"] == "PASS"
    for name in ("job.json", "config.yaml", "source_hashes.json", "angle_log.csv",
                 "turntable_calibration.json", "submap_quality.jsonl", "transforms.json",
                 "validation.json", "map_metadata.json", "map.ply", "map.pcd",
                 "source_colored_preview.png", "baseline_preview.png",
                 "geometry_measurements.json", "README.md"):
        assert (output / name).exists(), name
    # Persistence is filesystem based: a fresh reader can recover the complete job.
    restored = json.loads((output / "job.json").read_text("utf-8"))
    assert restored["status"] == "ready" and restored["output_directory"] == str(output.resolve())
    assert (output / "map.ply").read_bytes().startswith(b"ply\n")
    assert (output / "source_colored_preview.png").read_bytes().startswith(b"\x89PNG")


def test_invalid_or_rotating_segment_is_not_fused(tmp_path):
    pcap = make_pcap(tmp_path / "synthetic.pcap", scene(5))
    log = angle_log(tmp_path / "angles.csv", [row(valid="false", end=0.4)])
    try:
        run_turntable_job([pcap], log, calibration(tmp_path / "cal.json"), tmp_path / "job")
    except ValueError as exc:
        assert "没有通过" in str(exc)
    else:
        raise AssertionError("invalid/rotating segment was fused")


def test_turntable_api_job_survives_service_restart(tmp_path):
    root = tmp_path / "store"
    with TestClient(create_app(root)) as client:
        identifier, meta = upload(client, tmp_path / "source.pcap", frames=5)
        assert meta["status"] == "ready"
        capture = client.get(f"/api/captures/{identifier}").json()
        log_path = angle_log(tmp_path / "api.csv", [row(source=capture["filename"], end=0.4,
                                                         angle_source="MANUAL")])
        cal = json.loads(calibration(tmp_path / "api-cal.json").read_text("utf-8"))
        body = {"capture_ids": [identifier], "angle_log": log_path.read_text("utf-8"),
                "calibration": cal, "config": {"min_segment_seconds": 0.1,
                "minimum_submap_points": 20, "stability_min_overlap_1m": 0,
                "preview_size_px": 128, "preview_max_points_per_source": 1000}}
        response = client.post("/api/mapping/turntable/jobs", json=body)
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        deadline = time.time() + 30
        while time.time() < deadline:
            job = client.get(f"/api/mapping/turntable/jobs/{job_id}").json()
            if job["status"] in {"ready", "error"}: break
            time.sleep(0.02)
        assert job["status"] == "ready", job.get("error")
        assert client.get(f"/api/mapping/turntable/jobs/{job_id}/artifacts/source_colored_preview.png").status_code == 200
        manifest = client.post(f"/api/mapping/turntable/jobs/{job_id}/export").json()
        assert manifest["bundle_bytes"] > 0
    with TestClient(create_app(root)) as client:
        restored = client.get(f"/api/mapping/turntable/jobs/{job_id}").json()
        assert restored["status"] == "ready"


def test_turntable_api_accepts_three_named_captures(tmp_path):
    root = tmp_path / "store"
    with TestClient(create_app(root)) as client:
        ids = []
        for filename in ("center.pcap", "left.pcap", "right.pcap"):
            identifier = str(__import__('uuid').uuid4()); source = make_pcap(tmp_path / filename, scene(4))
            response = client.post('/api/captures', params={"id": identifier, "filename": filename}, content=source.read_bytes())
            assert response.status_code == 202; ids.append(identifier)
        for identifier in ids:
            for _ in range(400):
                if client.get(f"/api/captures/{identifier}").json()["status"] == "ready": break
                time.sleep(0.01)
        log = angle_log(tmp_path / "three.csv", [row("c", "center.pcap", 0, 0, 0.3, angle_source="MANUAL"),
                                                  row("l", "left.pcap", -2, 0, 0.3), row("r", "right.pcap", 3, 0, 0.3)])
        payload = {"capture_ids": ids, "angle_log": log.read_text("utf-8"),
                   "calibration": json.loads(calibration(tmp_path / "three-cal.json").read_text("utf-8")),
                   "config": {"min_segment_seconds": 0.1, "minimum_submap_points": 20,
                              "stability_min_overlap_1m": 0, "preview_size_px": 64,
                              "preview_max_points_per_source": 100}}
        response = client.post('/api/mapping/turntable/jobs', json=payload)
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        for _ in range(1500):
            current = client.get(f"/api/mapping/turntable/jobs/{job_id}").json()
            if current["status"] in {"ready", "error"}: break
            time.sleep(0.01)
        assert current["status"] == "ready", current.get("error")
        assert len(current["source_capture_ids"]) == 3 and len(current["submaps"]) == 3

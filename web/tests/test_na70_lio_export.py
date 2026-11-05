"""Tests for exporting a session into a packet-time LIO package (SYNTHETIC PCAP)."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from na70_core import lio_scans
from test_decoder import make_pcap, payload

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("export_na70_lio_scans",
                                              ROOT / "tools/export_na70_lio_scans.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def write_imu(path, start_unix=1700000000.0, count=300):
    rows = []
    for index in range(count):
        rows.append({"t_mono": 1000.0 + index * 0.001,
                     "t_unix": start_unix + index * 0.001,
                     "segment": 0, "tid": index + 1,
                     "accel": [0.0, 0.0, 9.80665], "gyro": [0.1, 0.2, 0.3]})
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_export_writes_blocked_packet_time_package(tmp_path):
    pcap = make_pcap(tmp_path / "SYNTHETIC-lio.pcap", [payload()] * 200)
    frames = write_imu(tmp_path / "imu_frames.jsonl")
    output = tmp_path / "package"
    manifest = module.export(pcap, frames, output, max_frames=2)
    assert manifest["status"] == "EXPORTED_BLOCKED"
    assert manifest["scans"]["frames"] == 2
    assert manifest["scans"]["points"] == 40000
    assert "time_level_C" in manifest["block_reasons"]
    assert "calibration_missing" in manifest["block_reasons"]
    arrays = lio_scans.load_npz(output / "scans.npz")
    assert arrays["points"].shape == (40000, 4)
    assert arrays["frame_offsets"].tolist() == [0, 20000, 40000]
    with np.load(output / "imu.npz", allow_pickle=False) as imu:
        assert imu["gyro_deg_s"].shape[0] >= 290
        assert bool(imu["has_device_time"][0]) is False
    assert (output / "REPORT.md").is_file()


def test_export_blocks_gap_at_frame_boundary(tmp_path):
    timestamps = [i * 0.001 for i in range(100)] + [0.6 + i * 0.001 for i in range(100)]
    pcap = make_pcap(tmp_path / "SYNTHETIC-gap.pcap", [payload()] * 200,
                     timestamps=timestamps)
    frames = write_imu(tmp_path / "imu_frames.jsonl", count=900)
    manifest = module.export(pcap, frames, tmp_path / "package", max_frames=2)
    assert manifest["time_quality"]["max_packet_gap_s"] > 0.5
    assert manifest["time_quality"]["gaps_over_limit"] == 1
    assert "packet_gap_over_limit" in manifest["block_reasons"]


def test_export_reports_device_time_field(tmp_path):
    pcap = make_pcap(tmp_path / "SYNTHETIC-lio-device.pcap", [payload()] * 100)
    rows = [{"t_mono": 1000.0 + index * 0.001,
             "t_unix": 1700000000.0 + index * 0.001,
             "segment": 0, "tid": index + 1, "sample_us": 1_000_000 + index * 1000,
             "accel": [0.0, 0.0, 9.80665], "gyro": [0.1, 0.2, 0.3]} for index in range(200)]
    frames = tmp_path / "imu-device.jsonl"
    frames.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    manifest = module.export(pcap, frames, tmp_path / "package-device", max_frames=1)
    assert manifest["time_source"] == "device_field_present_unverified"
    assert manifest["imu"]["device_time_present"] is True
    assert "device_time_semantics_unverified" in manifest["block_reasons"]
    with np.load(tmp_path / "package-device" / "imu.npz", allow_pickle=False) as imu:
        assert bool(imu["has_device_time"][0]) is True
        assert len(imu["device_time_s"]) == imu["gyro_deg_s"].shape[0]


def test_export_marks_unverified_candidate_calibration(tmp_path):
    pcap = make_pcap(tmp_path / "SYNTHETIC-lio2.pcap", [payload()] * 100)
    frames = write_imu(tmp_path / "imu2.jsonl")
    calibration = tmp_path / "candidate.json"
    calibration.write_text(json.dumps({"schema": "na70-calibration-candidate-v1",
                                       "R_imu_lidar": np.eye(3).tolist(),
                                       "t_imu_lidar_m": [0, 0, 0],
                                       "gate": {"holdout_validated": False}}), encoding="utf-8")
    manifest = module.export(pcap, frames, tmp_path / "package2", max_frames=1,
                             calibration_path=calibration)
    assert "calibration_candidate_unverified" in manifest["block_reasons"]
    assert "calibration_missing" not in manifest["block_reasons"]


def test_export_handles_incomplete_candidate(tmp_path):
    pcap = make_pcap(tmp_path / "SYNTHETIC-lio4.pcap", [payload()] * 100)
    frames = write_imu(tmp_path / "imu4.jsonl")
    calibration = tmp_path / "incomplete.json"
    calibration.write_text(json.dumps({"schema": "na70-calibration-candidate-v1",
                                       "R_imu_lidar": None, "t_imu_lidar_m": [0, 0, 0]}),
                           encoding="utf-8")
    manifest = module.export(pcap, frames, tmp_path / "package4", max_frames=1,
                             calibration_path=calibration)
    assert "calibration_incomplete" in manifest["block_reasons"]
    assert manifest["calibration"] is None


def test_export_rejects_missing_imu(tmp_path):
    pcap = make_pcap(tmp_path / "SYNTHETIC-lio3.pcap", [payload()] * 100)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert module.main(["--pcap", str(pcap), "--imu-frames", str(empty),
                        "--max-frames", "1", "--output", str(tmp_path / "bad")]) == 2
    assert not (tmp_path / "bad").exists()


def test_export_default_manifest_declares_packet_mode(tmp_path):
    pcap = make_pcap(tmp_path / "SYNTHETIC-lio-packet.pcap", [payload()] * 200)
    frames = write_imu(tmp_path / "imu-packet.jsonl")
    manifest = module.export(pcap, frames, tmp_path / "package-packet", max_frames=1)
    assert manifest["point_time_mode"] == "packet"
    assert manifest["time_quality"]["point_time_mode"] == "packet"
    assert manifest["time_quality"]["time_quality"] == "PACKET_APPROXIMATION"
    assert manifest["time_quality"]["packets_interpolated"] == 0
    assert manifest["time_quality"]["packets_collapsed"] == 0
    assert manifest["time_quality"]["packets_fallback"] == 0
    assert "包级近似" in manifest["note"]


def test_export_interpolated_mode_passthrough_and_manifest(tmp_path):
    pcap = make_pcap(tmp_path / "SYNTHETIC-lio-interp.pcap", [payload()] * 200)
    frames = write_imu(tmp_path / "imu-interp.jsonl")
    output = tmp_path / "package-interp"
    # CLI passthrough, not just the export() call.
    assert module.main(["--pcap", str(pcap), "--imu-frames", str(frames),
                        "--max-frames", "2", "--point-time-mode", "interpolated",
                        "--output", str(output)]) == 0
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["point_time_mode"] == "interpolated"
    quality = manifest["time_quality"]
    assert quality["time_quality"] == "POINT_INTERPOLATED_UNIFORM_PACKET_GAP"
    assert quality["point_time_mode"] == "interpolated"
    assert quality["packets_fallback"] == 1
    assert quality["packets_interpolated"] + quality["packets_collapsed"] + \
        quality["packets_fallback"] == quality["frames"] * 100
    assert "声明近似" in manifest["note"]
    arrays = lio_scans.load_npz(output / "scans.npz")  # roundtrip validation
    packet_starts = np.r_[True, np.diff(arrays["packet_index"]) != 0]
    # Interpolated mode: points inside one packet are no longer all identical.
    assert np.any(np.diff(arrays["point_time_s"][~packet_starts]) != 0)
    assert "声明近似" in (output / "REPORT.md").read_text(encoding="utf-8")


def test_export_rejects_unknown_point_time_mode(tmp_path):
    pcap = make_pcap(tmp_path / "SYNTHETIC-lio-mode.pcap", [payload()] * 100)
    frames = write_imu(tmp_path / "imu-mode.jsonl")
    # argparse rejects the unknown choice before any export runs.
    with pytest.raises(SystemExit):
        module.main(["--pcap", str(pcap), "--imu-frames", str(frames),
                     "--max-frames", "1", "--point-time-mode", "measured",
                     "--output", str(tmp_path / "bad-mode")])
    assert not (tmp_path / "bad-mode").exists()

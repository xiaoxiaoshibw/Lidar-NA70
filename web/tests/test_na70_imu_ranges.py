"""Tests for IMU range/saturation diagnostics (SYNTHETIC frames)."""
import importlib.util
import json
from pathlib import Path

import numpy as np

from na70_core.calibration import imu

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("audit_na70_imu_ranges",
                                              ROOT / "tools/audit_na70_imu_ranges.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def clean_arrays(count=400):
    times = np.arange(count) * 0.005
    accel = np.tile([0.0, 0.0, 9.80665], (count, 1))
    gyro = np.tile([0.1, -0.2, 0.3], (count, 1))
    return times, accel, gyro


def test_clean_data_has_no_saturation():
    times, accel, gyro = clean_arrays()
    result = imu.range_summary(times, accel, gyro, gyro_range=200, accel_range=16)
    assert result["status"] == "OK_NO_SATURATION"
    assert result["event_count"] == 0
    assert abs(result["channels"]["accel"]["z"]["max"] - 9.80665) < 1e-9


def test_configured_range_and_numeric_full_scale_are_flagged():
    times, accel, gyro = clean_arrays()
    gyro[100] = [250.0, 0.0, 0.0]
    result = imu.range_summary(times, accel, gyro, gyro_range=200)
    assert result["status"] == "SATURATION_SUSPECTED"
    kinds = {event["kind"] for event in result["events"]}
    assert "near_configured_range" in kinds

    times, accel, gyro = clean_arrays()
    accel[10:13] = [imu.INT32_FULL_SCALE, 0.0, 0.0]
    result = imu.range_summary(times, accel, gyro)
    kinds = {event["kind"] for event in result["events"]}
    assert "numeric_full_scale" in kinds
    assert "repeated_extreme_plateau" in kinds


def test_repeated_extreme_is_reported():
    times, accel, gyro = clean_arrays()
    gyro[50:54] = [120.0, 0.0, 0.0]
    result = imu.range_summary(times, accel, gyro, gyro_range=100, plateau_samples=3)
    plateaus = [event for event in result["events"] if event["kind"] == "repeated_extreme_plateau"]
    assert plateaus and plateaus[0]["samples"] == 4


def write_frames(path, accel_value=9.8):
    rows = [{"t_mono": 1000 + i * 0.005, "t_unix": 1793000000 + i * 0.005, "segment": 0,
             "tid": i + 1, "accel": [0.0, 0.0, accel_value], "gyro": [0.0, 0.0, 0.0]}
            for i in range(100)]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_cli_status_and_refusal(tmp_path):
    frames = tmp_path / "imu_frames.jsonl"
    write_frames(frames)
    output = tmp_path / "ranges.json"
    assert module.main(["--frames", str(frames), "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "OK_NO_SATURATION"

    write_frames(frames, accel_value=imu.INT32_FULL_SCALE)
    output2 = tmp_path / "ranges2.json"
    assert module.main(["--frames", str(frames), "--output", str(output2)]) == 1
    report = json.loads(output2.read_text(encoding="utf-8"))
    assert report["status"] == "SATURATION_SUSPECTED"
    assert report["event_count"] > 0

    assert module.main(["--frames", str(tmp_path / "missing.jsonl"),
                        "--output", str(tmp_path / "x.json")]) == 2
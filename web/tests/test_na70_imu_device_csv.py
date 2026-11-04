"""Tests for exporting a device-time IMU CSV from frames with sample_us."""
import importlib.util
import json
from pathlib import Path

import pytest

from na70_core.lio import ImuSamples

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("export_na70_imu_device_csv",
                                              ROOT / "tools/export_na70_imu_device_csv.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def write_frames(path, sample_us, *, segment=0):
    rows = [{"t_mono": 1000.0 + i * 0.005, "t_unix": 1793000000.0 + i * 0.005,
             "segment": segment, "tid": i + 1, "sample_us": value,
             "accel": [0.0, 0.0, 9.80665], "gyro": [0.1, 0.2, 0.3]}
            for i, value in enumerate(sample_us)]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_export_produces_lio_contract(tmp_path):
    frames = tmp_path / "imu_frames.jsonl"
    write_frames(frames, [1_000_000 + i * 5000 for i in range(5)])
    output = tmp_path / "imu_device.csv"
    meta = module.export(frames, output)
    assert meta["rows"] == 5
    assert abs(meta["duration_s"] - 0.020) < 1e-12
    lines = output.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "device_time_s,gx,gy,gz,ax,ay,az"
    assert lines[1].startswith("0.000000,0.100000")
    samples = ImuSamples.from_csv(output, frame_id="imu_link", gyro_unit="deg/s",
                                  accel_unit="m/s^2", max_samples=100)
    assert len(samples.device_times) == 5
    assert samples.device_times[0] == 0.0
    assert pytest.approx(samples.device_times[-1], abs=1e-12) == 0.020


def test_missing_device_time_is_refused(tmp_path):
    frames = tmp_path / "imu_frames.jsonl"
    rows = [{"t_mono": 1.0, "t_unix": 2.0, "segment": 0, "tid": 1,
             "accel": [0, 0, 9.8], "gyro": [0, 0, 0]}]
    frames.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="lack sample_us"):
        module.export(frames, tmp_path / "out.csv")
    assert module.main(["--frames", str(frames), "--output", str(tmp_path / "out2.csv")]) == 2
    assert not (tmp_path / "out2.csv").exists()


def test_backwards_without_wrap_is_refused(tmp_path):
    frames = tmp_path / "imu_frames.jsonl"
    write_frames(frames, [1000, 900])
    with pytest.raises(ValueError, match="went backwards"):
        module.export(frames, tmp_path / "out.csv")


def test_32bit_wrap_is_unwrapped(tmp_path):
    frames = tmp_path / "imu_frames.jsonl"
    write_frames(frames, [0xFFFFFF00, 0xFFFFFFF0, 0x00000100])
    output = tmp_path / "out.csv"
    meta = module.export(frames, output)
    assert meta["wrap_count"] == 1
    lines = output.read_text(encoding="utf-8").splitlines()[1:]
    times = [float(line.split(",")[0]) for line in lines]
    assert times == sorted(times) and len(set(times)) == 3
    assert abs(times[-1] - (0x100 + 0x100) / 1e6) < 1e-12


def test_multiple_segments_require_selection(tmp_path):
    frames = tmp_path / "imu_frames.jsonl"
    rows = []
    for segment in (0, 1):
        for index in range(3):
            rows.append({"t_mono": 1000.0 + segment + index * 0.005,
                         "t_unix": 1793000000.0 + segment + index * 0.005,
                         "segment": segment, "tid": index + 1,
                         "sample_us": 1000 + index * 5000,
                         "accel": [0, 0, 9.8], "gyro": [0, 0, 0]})
    frames.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="multiple segments"):
        module.export(frames, tmp_path / "all.csv")
    meta = module.export(frames, tmp_path / "seg1.csv", segment=1)
    assert meta["segment"] == 1 and meta["rows"] == 3
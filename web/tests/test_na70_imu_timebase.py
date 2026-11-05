"""Tests for the IMU time-base audit (SYNTHETIC chunk/frame streams)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("audit_na70_imu_timebase",
                                              ROOT / "tools/audit_na70_imu_timebase.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def write_streams(tmp_path, *, batches=6, per_batch=10, device_time=False, skip_tid=None):
    frames = []
    chunks = []
    tid = 1
    for batch in range(batches):
        time = 1000.0 + batch * 0.05
        chunks.append({"t_mono": time, "t_unix": 1793000000.0 + batch * 0.05,
                       "bytes": 670, "segment": 0})
        for index in range(per_batch):
            if skip_tid is not None and tid == skip_tid:
                tid += 1
            row = {"t_mono": time, "t_unix": 1793000000.0 + batch * 0.05, "segment": 0,
                   "tid": tid, "accel": [0.0, 0.0, 9.8], "gyro": [0.0, 0.0, 0.0]}
            if device_time:
                row["sample_us"] = batch * 50000 + index * 5000
            frames.append(row)
            tid += 1
    frame_path = tmp_path / "imu_frames.jsonl"
    chunk_path = tmp_path / "imu_chunks.jsonl"
    frame_path.write_text("".join(json.dumps(row) + "\n" for row in frames), encoding="utf-8")
    chunk_path.write_text("".join(json.dumps(row) + "\n" for row in chunks), encoding="utf-8")
    return frame_path, chunk_path


def test_batched_host_time_reports_conservative_bound(tmp_path):
    frames, chunks = write_streams(tmp_path)
    result = module.audit(frames, chunks)
    assert result["status"] == "OK_HOST_TIME_ONLY"
    assert result["time_level"] == "C_HOST_RECEIVE_ONLY"
    assert result["batches"]["frames"] == 60
    assert result["batches"]["unique_host_times"] == 6
    assert result["batches"]["frames_per_batch_min"] == 10
    assert abs(result["batches"]["batch_interval_median_s"] - 0.05) < 1e-9
    assert result["host_time_bound"]["conservative_s"] is None
    assert "上界未知" in result["host_time_bound"]["meaning"]
    assert result["sequence"]["tid_gap_events"] == 0


def test_device_time_field_is_reported_without_promoting_sync(tmp_path):
    frames, chunks = write_streams(tmp_path, device_time=True)
    result = module.audit(frames, chunks)
    assert result["status"] == "OK_DEVICE_TIME_PRESENT"
    assert result["time_level"] == "DECLARED_DEVICE_FIELD_PRESENT"
    assert result["claims"]["time_sync_validated"] is False


def test_tid_gap_is_counted(tmp_path):
    frames, chunks = write_streams(tmp_path, skip_tid=25)
    result = module.audit(frames, chunks)
    assert result["sequence"]["tid_gap_events"] == 1
    assert result["sequence"]["estimated_missing_frames"] == 1
    assert result["sequence"]["events"][0]["previous"] == 24
    assert result["sequence"]["events"][0]["current"] == 26


def test_unmatched_frame_time_is_rejected(tmp_path):
    frames, chunks = write_streams(tmp_path)
    rows = [json.loads(line) for line in frames.read_text(encoding="utf-8").splitlines()]
    rows[0]["t_mono"] = 999.0
    frames.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert module.main(["--frames", str(frames), "--chunks", str(chunks),
                        "--output", str(tmp_path / "out.json")]) == 2
    report = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert report["status"] == "INVALID_DATA"

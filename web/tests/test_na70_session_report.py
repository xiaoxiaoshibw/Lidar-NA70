"""Tests for the one-command session diagnostic report (SYNTHETIC session)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("report_na70_session", ROOT / "tools/report_na70_session.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def make_session(root, *, batches=200, per_batch=10, moving_window=None):
    root.mkdir()
    frames = []
    chunks = []
    tid = 1
    for batch in range(batches):
        time = 1000.0 + batch * 0.05
        chunks.append({"segment": 0, "offset": batch * 670, "bytes": 670,
                       "t_mono": time, "t_unix": 1793000000.0 + batch * 0.05})
        for index in range(per_batch):
            gyro = [0.0, 0.0, 0.0]
            if moving_window is not None and moving_window[0] <= batch < moving_window[1]:
                gyro = [2.0, 0.0, 0.0]
            frames.append({"t_mono": time, "t_unix": 1793000000.0 + batch * 0.05,
                           "segment": 0, "tid": tid, "accel": [0.0, 0.0, 9.80665],
                           "gyro": gyro})
            tid += 1
    (root / "imu_frames.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in frames), encoding="utf-8")
    (root / "imu_chunks.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in chunks), encoding="utf-8")
    return root


def test_static_session_is_ready(tmp_path):
    session = make_session(tmp_path / "static")
    result = module.report(session)
    assert result["status"] == "STATIC_LIKE_READY"
    assert result["time_level"] == "C_HOST_RECEIVE_ONLY"
    assert result["device_time_present"] is False
    assert result["saturation_suspected"] is False
    assert result["static_baseline_usable"] is True
    assert result["checks"]["overlap"] == "SKIPPED_NO_PCAP"
    assert abs(result["summary"]["host_time_bound_s"] - 0.05) < 1e-9
    assert result["verdicts"]["may_be_used_for_dynamic_calibration"] is False


def test_moving_session_is_rejected(tmp_path):
    session = make_session(tmp_path / "moving", moving_window=(20, 40))
    result = module.report(session)
    assert result["status"] == "REJECTED_NOT_STATIC"
    assert result["static_baseline_usable"] is False
    assert result["static_baseline_usable"] is False


def test_cli_writes_report_and_refuses_overwrite(tmp_path):
    session = make_session(tmp_path / "static")
    output = tmp_path / "report"
    assert module.main(["--session", str(session), "--output", str(output)]) == 0
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "STATIC_LIKE_READY"
    assert (output / "REPORT.md").is_file()
    assert module.main(["--session", str(session), "--output", str(output)]) == 2
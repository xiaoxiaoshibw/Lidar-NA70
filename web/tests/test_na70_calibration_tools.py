"""End-to-end tests for the calibration CLI tools using SYNTHETIC inputs."""
import importlib.util
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from na70_core.calibration import synthetic

ROOT = Path(__file__).resolve().parents[2]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"tools/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_frames(path, faces, *, noise=0.002):
    """Ten-second windows at 200 Hz; face i starts at i*10 s (from the first row)."""
    matrix = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    bias = np.array([0.10, -0.05, 0.03])
    rows = []
    t = 0.0
    for face in faces:
        direction = np.array(synthetic.AXES[face])
        for index in range(2000):
            a = 9.80665 * direction + bias + noise * math.sin(index * 0.7)
            rows.append({"t_mono": 1000.0 + t, "t_unix": 1793000000.0 + t, "segment": 0,
                         "accel": [float(value) for value in a],
                         "gyro": [0.0, 0.0, 0.0]})
            t += 0.005
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return matrix, bias


def test_six_face_tool_solves_and_rejects_motion(tmp_path):
    tool = load_tool("solve_na70_six_face")
    faces = ["+z", "-z", "+x", "-x", "+y", "-y"]
    frames = tmp_path / "imu_frames.jsonl"
    matrix, bias = write_frames(frames, faces)
    spec = {"windows": [{"face": face, "segment": 0, "start_s": index * 10.0,
                         "end_s": index * 10.0 + 9.99} for index, face in enumerate(faces)]}
    windows = tmp_path / "windows.json"
    windows.write_text(json.dumps(spec), encoding="utf-8")
    output = tmp_path / "six-face.json"
    config = json.loads((ROOT / "tools/lio/static_check.yaml").read_text(encoding="utf-8"))
    report = tool.evaluate(frames, spec, config)
    assert report["status"] == "SOLVED_CANDIDATE", report.get("reasons")
    assert np.allclose(report["solution"]["transform"]["bias_m_s2"], bias, atol=0.01)
    assert np.allclose(report["solution"]["transform"]["M"], np.eye(3), atol=0.01)

    assert tool.main(["--frames", str(frames), "--windows", str(windows),
                      "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "SOLVED_CANDIDATE"

    moving = tmp_path / "moving.jsonl"
    rows = [json.loads(line) for line in frames.read_text(encoding="utf-8").splitlines()]
    for row in rows[2000:4000]:
        row["gyro"] = [2.0, 0.0, 0.0]
    moving.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    rejected = tool.evaluate(moving, spec, config)
    assert rejected["status"] == "INSUFFICIENT_DATA"
    assert "window_not_static:-z" in rejected["reasons"][0]


def test_imu_run_tool_reports_static_and_short_allan(tmp_path):
    tool = load_tool("analyze_na70_imu_run")
    frames = tmp_path / "imu_frames.jsonl"
    write_frames(frames, ["+z"])
    config = json.loads((ROOT / "tools/lio/static_check.yaml").read_text(encoding="utf-8"))
    result = tool.analyse(frames, config)
    assert result["status"] == "STATIC_LIKE"
    assert result["allan"]["status"] == "NOT_EVALUATED"
    assert result["allan"]["reason"] == "record_too_short"


def test_imu_run_does_not_treat_batched_arrivals_as_gaps(tmp_path):
    tool = load_tool("analyze_na70_imu_run")
    frames = tmp_path / "batched.jsonl"
    rows = []
    time = 0.0
    for index in range(2000):
        if index % 2 == 0:
            time += 0.01
        rows.append({"t_mono": 1000.0 + time, "t_unix": 1793000000.0 + time, "segment": 0,
                     "accel": [0.0, 0.0, 9.80665], "gyro": [0.0, 0.0, 0.0]})
    frames.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    config = json.loads((ROOT / "tools/lio/static_check.yaml").read_text(encoding="utf-8"))
    result = tool.analyse(frames, config)
    segment = result["segments"][0]
    assert segment["duplicate_host_times"] == 1000
    assert abs(segment["median_positive_dt_s"] - 0.01) < 1e-9
    assert segment["gaps_over_1_5_median"] == 0
    assert result["status"] == "STATIC_LIKE"


def test_imu_run_uses_host_batch_allan_for_long_batched(tmp_path):
    tool = load_tool("analyze_na70_imu_run")
    batches = 1300
    rows = []
    for batch in range(batches):
        time = 1000.0 + batch * 0.05
        for _ in range(10):
            rows.append({"t_mono": time, "t_unix": 1793000000.0 + batch * 0.05,
                         "segment": 0, "accel": [0.0, 0.0, 9.80665], "gyro": [0.0, 0.0, 0.0]})
    frames = tmp_path / "long-batched.jsonl"
    frames.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    config = json.loads((ROOT / "tools/lio/static_check.yaml").read_text(encoding="utf-8"))
    result = tool.analyse(frames, config)
    assert result["status"] == "STATIC_LIKE"
    assert result["allan"]["time_basis"] == "HOST_BATCH_AVERAGED"
    assert result["allan"]["batches"] == batches
    assert result["allan"]["table"]


def test_imu_run_time_slice_skips_handling(tmp_path):
    tool = load_tool("analyze_na70_imu_run")
    batches = 1700
    rows = []
    for batch in range(batches):
        time = 1000.0 + batch * 0.05
        gyro = [2.0, 0.0, 0.0] if batch < 400 else [0.0, 0.0, 0.0]
        for _ in range(10):
            rows.append({"t_mono": time, "t_unix": 1793000000.0 + batch * 0.05,
                         "segment": 0, "accel": [0.0, 0.0, 9.80665], "gyro": gyro})
    frames = tmp_path / "handling.jsonl"
    frames.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    config = json.loads((ROOT / "tools/lio/static_check.yaml").read_text(encoding="utf-8"))
    assert tool.analyse(frames, config)["status"] == "REJECTED"
    sliced = tool.analyse(frames, config, start_s=20.0)
    assert sliced["status"] == "STATIC_LIKE"
    assert sliced["allan"]["time_basis"] == "HOST_BATCH_AVERAGED"


def test_compose_tool_builds_proper_inverse(tmp_path):
    tool = load_tool("compose_na70_calibration")
    rotation = {"status": "SOLVED_CANDIDATE", "R_imu_lidar": np.eye(3).tolist(),
                "time_offset_s": 0.012, "time_offset_resolution_s": 0.0001,
                "residual": {"relative": 0.02}, "observability": {"status": "THREE_AXIS"},
                "euler_zyx_deg": [0.0, 0.0, 0.0]}
    path = tmp_path / "rotation.json"
    path.write_text(json.dumps(rotation), encoding="utf-8")
    output = tmp_path / "candidate.json"
    assert tool.main(["--rotation", str(path), "--translation-mm", "43,0,-60",
                      "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "CANDIDATE"
    assert report["t_imu_lidar_m"] == [0.043, 0.0, -0.06]
    assert np.allclose(np.asarray(report["T_lidar_imu"]) @ np.asarray(report["T_imu_lidar"]),
                       np.eye(4))

    incomplete = dict(rotation, status="INSUFFICIENT_MOTION")
    path.write_text(json.dumps(incomplete), encoding="utf-8")
    output2 = tmp_path / "candidate2.json"
    assert tool.main(["--rotation", str(path), "--translation-mm", "43,0,-60",
                      "--output", str(output2)]) == 2


def test_range_model_fit_from_plane_runs(tmp_path):
    tool = load_tool("fit_na70_range_model")
    paths = []
    for index, distance in enumerate((5.0, 10.0, 20.0)):
        angle = math.radians(0.5 * index)
        path = tmp_path / f"plane-{distance:g}.json"
        path.write_text(json.dumps({
            "schema": "na70-lidar-plane-run-v1", "status": "FIT",
            "plane": {"normal_distance_m": distance * 1.01 + 0.02,
                      "normal_unit": [math.cos(angle), math.sin(angle), 0.0],
                      "reference": {"reference_distance_m": distance},
                      "residual_rms_m": 0.004}}), encoding="utf-8")
        paths.append(path)
    result = tool.fit(paths)
    assert result["status"] == "FIT"
    assert abs(result["fit"]["slope"] - 1.01) < 1e-9
    assert abs(result["fit"]["intercept_m"] - 0.02) < 1e-9
    assert result["angular_consistency"]["pairs"] == 3
    assert 0.9 < result["angular_consistency"]["max_deg"] < 1.1
    output = tmp_path / "range.json"
    assert tool.main(["--planes", *map(str, paths), "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "FIT"
    assert tool.main(["--planes", str(paths[0]), str(paths[1]),
                      "--output", str(tmp_path / "short.json")]) == 1


def test_plane_tool_fits_synthetic_wall(tmp_path, monkeypatch):
    tool = load_tool("analyze_na70_lidar_planes")
    data = synthetic.plane_dataset(distance_m=10.0, noise_m=0.003, count=2000, seed=2)
    xyz = data["points"]
    points = np.column_stack([xyz, np.full(len(xyz), 42.0)]).astype("<f4")

    monkeypatch.setattr(tool.decoder, "scan_pcap",
                        lambda path: SimpleNamespace(frames=[{"packet_capture_times": [1.0]}],
                                                     duration=0.1))
    monkeypatch.setattr(tool.decoder, "decode_frame",
                        lambda path, frame, install_direction=1, source=None: points)
    pcap = tmp_path / "dummy.pcap"
    pcap.write_bytes(b"")
    output = tmp_path / "plane.json"
    code = tool.main(["--pcap", str(pcap), "--end-frame", "1",
                      "--roi=-100,100,-100,100,-100,100",
                      "--reference-distance-m", "10", "--output", str(output)])
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "FIT"
    assert abs(report["plane"]["reference"]["error_m"]) < 0.02
    with np.testing.assert_raises(ValueError):
        tool.parse_roi("1,2,3")
"""SYNTHETIC tests for the local calibration batch report pipeline.

Builds a full synthetic batch (STATIC + CAL-ROT + six faces) with a monkeypatched
decoder that returns one synthetic wall plane per frame, so the plane-rotation
``calibrate()`` path runs end to end without a real PCAP.  Nothing here proves
device capability: it only covers the batch wiring, status mapping and the
rotation recovery math on SYNTHETIC data.
"""
import importlib.util
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy.spatial.transform import Rotation

from backend import decoder

ROOT = Path(__file__).resolve().parents[2]

spec = importlib.util.spec_from_file_location("report_na70_calibration_batch",
                                              ROOT / "tools/report_na70_calibration_batch.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

FACES = ["+z", "-z", "+x", "-x", "+y", "-y"]
FACE_AXES = {"+z": (0, 0, 1), "-z": (0, 0, -1),
             "+x": (1, 0, 0), "-x": (-1, 0, 0),
             "+y": (0, 1, 0), "-y": (0, -1, 0)}
BIAS = np.array([0.10, -0.05, 0.03])
BASE_UNIX = 1793000000.0
FRAME_COUNT = 24
FRAME_DT = 0.25
WALL_DISTANCE = 3.0
WORLD_NORMAL = np.array([0.0, 1.0, 0.0])
NOISE_M = 0.003
TRUE_R_IL = Rotation.from_euler("zyx", [22.0, -7.0, 4.0], degrees=True).as_matrix()


def body_rotation(t_rel):
    """Deterministic slow three-axis device rotation R_WI(t)."""
    yaw = 30.0 * math.sin(0.8 * t_rel)
    pitch = 25.0 * math.sin(0.53 * t_rel + 1.0)
    roll = 20.0 * math.sin(0.71 * t_rel + 2.0)
    return Rotation.from_euler("zyx", [yaw, pitch, roll], degrees=True).as_matrix()


def write_static_session(path, direction, *, duration=10.0):
    path.mkdir(parents=True, exist_ok=True)
    axis = np.asarray(direction, dtype=float)
    rows = []
    for index in range(int(duration / 0.005)):
        accel = 9.80665 * axis + BIAS + 0.002 * math.sin(index * 0.7)
        rows.append({"t_mono": 1000.0 + index * 0.005, "t_unix": BASE_UNIX + index * 0.005,
                     "segment": 0, "accel": [float(value) for value in accel],
                     "gyro": [0.0, 0.0, 0.0]})
    (path / "imu_frames.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def write_rotation_session(path):
    path.mkdir(parents=True, exist_ok=True)
    rows = []
    start, stop, step = -1.0, 7.0, 0.005
    index = 0
    t_rel = start
    while t_rel <= stop:
        q = Rotation.from_matrix(body_rotation(t_rel)).as_quat()  # x, y, z, w
        rows.append({"t_mono": 1000.0 + (t_rel - start), "t_unix": BASE_UNIX + t_rel,
                     "segment": 0, "accel": [0.0, 0.0, 9.80665], "gyro": [0.0, 0.0, 0.0],
                     "quaternion": [float(q[3]), float(q[0]), float(q[1]), float(q[2])]})
        t_rel += step
        index += 1
    (path / "imu_frames.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    raw = path / "raw"
    raw.mkdir(exist_ok=True)
    (raw / "lidar.pcap").write_bytes(b"")
    return path


def wall_points(t_rel):
    """One synthetic wall seen from the rotated device, in LiDAR coordinates."""
    normal_lidar = TRUE_R_IL.T @ body_rotation(t_rel).T @ WORLD_NORMAL
    normal_lidar /= np.linalg.norm(normal_lidar)
    helper = np.array([1.0, 0.0, 0.0])
    if abs(float(np.dot(helper, normal_lidar))) > 0.9:
        helper = np.array([0.0, 1.0, 0.0])
    e1 = np.cross(normal_lidar, helper)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(normal_lidar, e1)
    rng = np.random.default_rng(1000 + int(round(t_rel / FRAME_DT)))
    u = rng.uniform(-1.5, 1.5, 1500)
    v = rng.uniform(-1.0, 1.0, 1500)
    points = WALL_DISTANCE * normal_lidar + np.outer(u, e1) + np.outer(v, e2)
    points = points + NOISE_M * rng.standard_normal(points.shape)
    return np.column_stack([points, np.full(len(points), 42.0)]).astype("<f4")


def fake_scan_pcap(path):
    frames = [{"packet_capture_times": [BASE_UNIX + i * FRAME_DT]} for i in range(FRAME_COUNT)]
    return SimpleNamespace(frames=frames, duration=FRAME_COUNT * FRAME_DT)


def fake_decode_frame(path, frame, install_direction=1, source=None):
    return wall_points(frame["packet_capture_times"][0] - BASE_UNIX)


def build_batch(tmp_path, *, with_rotation=True, rotation_pcap=True):
    sessions = {
        "STATIC": {"task": "CAL-STATIC", "session_id": "synthetic-static",
                   "local": str(write_static_session(tmp_path / "session-static", (0, 0, 1)))},
        "FACES": {face: {"task": f"CAL-FACE-{face}", "session_id": face,
                         "local": str(write_static_session(tmp_path / f"session-face-{face}",
                                                           FACE_AXES[face]))}
                  for face in FACES},
    }
    if with_rotation:
        local = write_rotation_session(tmp_path / "session-rot")
        if not rotation_pcap:
            (local / "raw" / "lidar.pcap").unlink()
        sessions["ROT"] = {"task": "CAL-ROT", "session_id": "synthetic-rot",
                           "local": str(local)}
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps({"schema": "na70-calibration-batch-v1", "sessions": sessions}),
                     encoding="utf-8")
    return batch


def angle_between(left, right):
    cosine = (np.trace(np.asarray(left).T @ np.asarray(right)) - 1.0) / 2.0
    return math.degrees(math.acos(float(np.clip(cosine, -1.0, 1.0))))


def test_full_batch_reports_rotation_ready_translation_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(decoder, "scan_pcap", fake_scan_pcap)
    monkeypatch.setattr(decoder, "decode_frame", fake_decode_frame)
    batch = build_batch(tmp_path)
    output = tmp_path / "report"

    result = module.process(batch, output, frame_step=1, max_offset=0.1, step=0.02)

    assert result["status"] == "ROTATION_READY_TRANSLATION_PENDING"
    assert result["stages"]["static"]["status"] == "STATIC_LIKE"
    assert result["stages"]["six_face"]["status"] == "SOLVED_CANDIDATE"
    assert result["stages"]["rotation"]["status"] == "SOLVED_CANDIDATE"
    assert angle_between(result["R_imu_lidar"], TRUE_R_IL) < 0.5
    assert abs(result["time_offset_s"]) <= 0.02
    assert result["translation"] is None
    assert result["claims"] == {"extrinsic_frozen": False, "time_sync_validated": False}
    for name in ("summary.json", "static.json", "six-face.json", "rotation.json", "REPORT.md"):
        assert (output / name).is_file(), name
    rotation = json.loads((output / "rotation.json").read_text(encoding="utf-8"))
    assert rotation["translation"]["value"] is None
    assert rotation["claims"]["holdout_validated"] is False
    report = (output / "REPORT.md").read_text(encoding="utf-8")
    assert "ROTATION_READY_TRANSLATION_PENDING" in report
    assert "平移 t_IL 未填写" in report


def test_batch_without_rotation_or_pcap_is_partial(tmp_path):
    no_rotation = module.process(build_batch(tmp_path, with_rotation=False),
                                 tmp_path / "report-no-rot")
    assert no_rotation["status"] == "PARTIAL"
    assert no_rotation["stages"]["rotation"]["status"] == "NOT_PROVIDED"
    assert "R_imu_lidar" not in no_rotation

    missing_pcap = module.process(build_batch(tmp_path, rotation_pcap=False),
                                  tmp_path / "report-no-pcap")
    assert missing_pcap["status"] == "PARTIAL"
    assert missing_pcap["stages"]["rotation"]["status"] == "MISSING_PCAP"
    assert "R_imu_lidar" not in missing_pcap


def test_report_refuses_to_overwrite_existing_output(tmp_path):
    batch = build_batch(tmp_path, with_rotation=False)
    output = tmp_path / "report"
    output.mkdir()
    assert module.main(["--batch", str(batch), "--output", str(output)]) == 2
    assert not (output / "summary.json").exists()

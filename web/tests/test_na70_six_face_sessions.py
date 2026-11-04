"""Tests for six-face solving across separate capture sessions (SYNTHETIC)."""
import importlib.util
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("solve_na70_six_face_sessions",
                                              ROOT / "tools/solve_na70_six_face_sessions.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

FACES = ["+z", "-z", "+x", "-x", "+y", "-y"]
BIAS = np.array([0.10, -0.05, 0.03])


def write_session(root, face, *, gyro=(0.0, 0.0, 0.0), duration=10.0):
    path = root / f"session-{face}"
    path.mkdir(exist_ok=True)
    direction = np.array(module.FACE_DIRECTIONS[face])
    rows = []
    steps = int(duration / 0.005)
    for index in range(steps):
        accel = 9.80665 * direction + BIAS + 0.002 * math.sin(index * 0.7)
        rows.append({"t_mono": 1000.0 + index * 0.005, "t_unix": 1793000000.0 + index * 0.005,
                     "segment": 0, "tid": index + 1,
                     "accel": [float(value) for value in accel], "gyro": list(gyro)})
    (path / "imu_frames.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def make_batch(tmp_path):
    faces = {face: {"task": f"CAL-FACE-{face}", "session_id": face,
                    "local": str(write_session(tmp_path, face))} for face in FACES}
    batch = {"schema": "na70-calibration-batch-v1", "sessions": {"FACES": faces}}
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(batch), encoding="utf-8")
    return path


def test_six_sessions_solve_bias(tmp_path):
    batch = make_batch(tmp_path)
    config = json.loads((ROOT / "tools/lio/static_check.yaml").read_text(encoding="utf-8"))
    result = module.evaluate(batch, config)
    assert result["status"] == "SOLVED_CANDIDATE", result.get("reasons")
    assert np.allclose(result["solution"]["transform"]["bias_m_s2"], BIAS, atol=0.01)
    output = tmp_path / "six.json"
    assert module.main(["--batch", str(batch), "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "SOLVED_CANDIDATE"


def test_moving_face_is_rejected(tmp_path):
    batch = make_batch(tmp_path)
    write_session(tmp_path, "+z", gyro=(2.0, 0.0, 0.0))
    config = json.loads((ROOT / "tools/lio/static_check.yaml").read_text(encoding="utf-8"))
    result = module.evaluate(batch, config)
    assert result["status"] == "INSUFFICIENT_DATA"
    assert "+z" in result["reasons"][0]
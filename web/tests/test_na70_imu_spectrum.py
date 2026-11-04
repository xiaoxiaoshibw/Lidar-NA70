import importlib.util
import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("imu_spectrum", ROOT / "tools/analyze_imu_vibration.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_synthetic_tone_and_sequence_gap(tmp_path):
    path = tmp_path / "frames.jsonl"
    rows = [{"tid": i + 1, "segment": 0, "accel": [0, 0, 9.8],
             "gyro": [math.sin(2 * math.pi * 10 * i / 200), 0, 0]} for i in range(2048)]
    def write():
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    write()
    result = module.analyze(path)
    assert result["axes"]["gx"]["nominal_peak_hz"] == pytest.approx(10, abs=0.2)
    assert result["axes"]["gy"]["nominal_peak_hz"] is None
    rows[1000]["tid"] += 1
    write()
    with pytest.raises(ValueError, match="discontinuity"):
        module.analyze(path)

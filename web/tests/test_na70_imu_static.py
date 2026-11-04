import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("imu_static", ROOT / "tools/check_na70_imu_static.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
CONFIG = json.loads((ROOT / "tools/lio/static_check.yaml").read_text(encoding="utf-8"))


def test_static_diagnostic_rejects_motion_and_nonfinite(tmp_path):
    path = tmp_path / "imu.jsonl"
    rows = [{"t_mono": i / 200, "segment": 0, "accel": [0, 0, 9.8], "gyro": [0, 0, 0]}
            for i in range(2000)]
    def write():
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    write()
    assert module.check(path, CONFIG)["status"] == "STATIC_LIKE"
    for row in rows[500:1000]:
        row["gyro"] = [2, 0, 0]
    write()
    assert module.check(path, CONFIG)["status"] == "REJECTED"
    rows[100]["accel"][0] = float("nan")
    write()
    assert module.check(path, CONFIG)["status"] == "INVALID_DATA"


def test_short_tail_is_not_ignored(tmp_path):
    path = tmp_path / "imu.jsonl"
    rows = [{"t_mono": i / 200, "segment": 0, "accel": [0, 0, 9.8], "gyro": [0, 0, 0]}
            for i in range(2020)]
    for row in rows[2000:]:
        row["gyro"] = [5, 0, 0]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert module.check(path, CONFIG)["status"] == "REJECTED"

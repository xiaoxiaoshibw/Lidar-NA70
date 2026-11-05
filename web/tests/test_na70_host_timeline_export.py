"""SYNTHETIC export preserves receive batches and selected ray indices."""
import base64
import csv
import importlib.util
import json
from pathlib import Path

import numpy as np

from test_decoder import make_pcap, payload

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("timeline_export", ROOT / "tools/na70_host_timeline_export.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_synthetic_timeline_preserves_batch_and_source_indices(tmp_path):
    session = tmp_path / "session"
    (session / "raw").mkdir(parents=True)
    # Each 200-ray packet alternates zero and valid range; no synthetic point time.
    packet = b"".join((payload(distance=0)[:7], payload(distance=1000)[:7])) * 100 + payload()[-10:]
    make_pcap(session / "raw/lidar.pcap", [packet] * 100)
    rows = [{"t_unix": 1700000000.05, "tid": tid, "euler": [float(tid), 0, 0],
             "gyro": [0, 0, 0], "accel": [0, 0, 9.8], "quaternion": [1, 0, 0, 0]}
            for tid in (1, 2)]
    (session / "imu_frames.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    trajectory = tmp_path / "trajectory.csv"
    with trajectory.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(["t_s", "x", "y", "z"])
        writer.writerow([1700000000.05, 1, 2, 3])
    output = tmp_path / "output"
    manifest = module.export(session, trajectory, output, points_per_frame=3)
    assert manifest["frames"] == 1 and manifest["imu_batches"] == 1 and manifest["imu_frames"] == 2
    html = (output / "index.html").read_text(encoding="utf-8")
    data = json.loads(html.split("const DATA=", 1)[1].split(";\nconst $", 1)[0])
    frame, batch = data["frames"][0], data["batches"][0]
    indices = np.frombuffer(base64.b64decode(frame["source_indices"]), dtype="<u4")
    assert indices.tolist() == [1, 9999, 19999]
    assert frame["eligible"] == 10000 and frame["count"] == 3
    assert batch["count"] == 2 and batch["min"][0] == 1 and batch["max"][0] == 2
    assert batch["tid"] == 2 and len(data["poses"]) == 1
    assert [s["tid"] for s in batch["samples"]] == [1, 2]
    assert batch["samples"][0]["quaternion"] == [1, 0, 0, 0]
    assert batch["gyro_min"] == [0, 0, 0] and batch["gyro_max"] == [0, 0, 0]
    assert data["model"]["vertices"] and data["model"]["faces"]

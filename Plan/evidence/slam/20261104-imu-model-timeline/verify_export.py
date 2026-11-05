"""Check every displayed IMU frame against the source without inventing times."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[4]
page = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
data = json.loads(page.split("const DATA=", 1)[1].split(";\nconst $", 1)[0])
source = root / "radar_capture_data/na70_sessions/20261104-170341/imu_frames.jsonl"
fields = ("tid", "quaternion", "euler", "gyro", "accel")
count = 0
with source.open(encoding="utf-8") as stream:
    for batch in data["batches"]:
        assert batch["count"] == len(batch["samples"])
        for sample in batch["samples"]:
            raw = json.loads(next(stream))
            assert raw["t_unix"] - data["origin_unix_s"] == batch["t"]
            assert all(raw[key] == sample[key] for key in fields)
            count += 1
    assert next(stream, None) is None
assert count == 20052 and len(data["batches"]) == 164
print(json.dumps({"status": "PASS", "source_frames": count, "receive_batches": len(data["batches"]),
                  "fields": fields, "batch_time_preserved": True}, ensure_ascii=False, indent=2))

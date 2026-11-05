"""Check exported stack input against the previous timeline without changing either file."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent


def load_data(path):
    html = path.read_text(encoding="utf-8")
    return json.loads(html.split("const DATA=", 1)[1].split(";\nconst $", 1)[0])


new = load_data(OUT / "index.html")
old = load_data(ROOT / "Plan/evidence/slam/20261104-imu-model-timeline/index.html")
fields = ["frames", "batches", "poses", "model", "origin_unix_s", "sources", "points_per_frame"]
same = {field: new[field] == old[field] for field in fields}
assert all(same.values()), same
frames = new["frames"]
windows = []
for frame, length in [(0, 100), (60, 10), (len(frames) - 1, 100)]:
    first = max(0, frame - length + 1)
    selected = frames[first:frame + 1]
    windows.append({"requested": length, "first_frame": selected[0]["index"],
                    "last_frame": selected[-1]["index"], "actual_frames": len(selected),
                    "start_s": selected[0]["start"], "end_s": selected[-1]["end"],
                    "sampled_points": sum(f["count"] for f in selected),
                    "last_frame_complete": selected[-1]["complete"]})
paths = ["tools/na70_host_timeline_view.html", "tools/na70_host_timeline_export.py",
         "web/tests/test_na70_timeline_stack.cjs", "web/tests/test_na70_timeline_ui.cjs",
         "web/tests/test_na70_timeline_math.cjs", "web/tests/test_na70_host_timeline_export.py"]
hashes = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}
print(json.dumps({"status": "PASS", "same_data_as_previous_timeline": same,
                  "windows": windows, "code_sha256": hashes}, indent=2, ensure_ascii=False))

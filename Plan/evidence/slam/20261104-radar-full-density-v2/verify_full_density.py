"""Read-only check of full-density export versus earlier 1200-point export."""
import base64
import hashlib
import json
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
OLD = ROOT / "Plan/evidence/slam/20261104-radar-stack-timeline/index.html"
PAGE = OUT / "index.html"

def load(path):
    text = path.read_text(encoding="utf-8")
    return json.loads(text.split("const DATA=", 1)[1].split(";\nconst $", 1)[0])

def arr(text, dtype, columns=1):
    return np.frombuffer(base64.b64decode(text, validate=True), dtype=dtype).reshape(-1, columns)

start = time.perf_counter()
new, old = load(PAGE), load(OLD)
parse_s = time.perf_counter() - start
assert len(new["frames"]) == len(old["frames"]) == 834
total = old_total = mismatch_old_1200 = 0
windows = {"all_le_150": 0, "old_le_150": 0, "all_le_50": 0, "old_le_50": 0}
for i, (a, b) in enumerate(zip(new["frames"], old["frames"])):
    assert (a["index"], a["start"], a["end"], a["eligible"], a["rays"]) == (
        b["index"], b["start"], b["end"], b["eligible"], b["rays"])
    full_i = arr(a["source_indices"], "<u4").ravel()
    full_p = arr(a["xyzi"], "<f4", 4)
    old_i = arr(b["source_indices"], "<u4").ravel()
    old_p = arr(b["xyzi"], "<f4", 4)
    assert len(full_i) == len(full_p) == a["eligible"] == a["count"]
    assert np.all(np.diff(full_i.astype(np.int64)) > 0)
    pos = np.searchsorted(full_i, old_i)
    assert np.array_equal(full_i[pos], old_i) and np.array_equal(full_p[pos], old_p)
    count = len(old_i)
    js_pos = np.array([0 if count == 1 else len(full_i)-1 if k == count-1 else
                       int(k * ((len(full_i) - 1) / (count - 1)))
                       for k in range(count)], dtype=int)
    mismatch_old_1200 += int(not np.array_equal(full_i[js_pos], old_i))
    total += len(full_i)
    old_total += len(old_i)
    if 91 <= i <= 100:
        full_r = np.linalg.norm(full_p[:, :3], axis=1)
        old_r = np.linalg.norm(old_p[:, :3], axis=1)
        for distance in (50, 150):
            windows[f"all_le_{distance}"] += int((full_r <= distance).sum())
            windows[f"old_le_{distance}"] += int((old_r <= distance).sum())
assert new["batches"] == old["batches"] and new["poses"] == old["poses"] and new["model"] == old["model"]
template = ROOT / "tools/na70_host_timeline_view.html"
manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
assert hashlib.sha256(template.read_bytes()).hexdigest() == manifest["template_sha256"]
assert hashlib.sha256(PAGE.read_bytes()).hexdigest() == manifest["page_sha256"]
result = {"status": "PASS", "frames_checked": 834, "source_indices_and_xyzi_preserved": True,
          "new_candidate_points": total, "previous_exported_points": old_total,
          "frames_with_1200_sampling_mismatch": mismatch_old_1200,
          "compare_frames_91_to_100": windows, "page_bytes": PAGE.stat().st_size,
          "page_parse_both_versions_seconds": parse_s,
          "template_sha256": manifest["template_sha256"], "page_sha256": manifest["page_sha256"]}
print(json.dumps(result, indent=2))

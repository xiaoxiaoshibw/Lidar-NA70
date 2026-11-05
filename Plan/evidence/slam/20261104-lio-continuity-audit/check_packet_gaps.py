"""Compare previous within-frame statistics with all packet boundaries, read-only."""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "packages/na70_core"))
from na70_core.lio_scans import ScanSet, time_quality

source = ROOT / "Plan/evidence/slam/20261104-lio-diagnostic/lio-package-margin1s/scans.npz"
with np.load(source, allow_pickle=False) as archive:
    scans = ScanSet(**{key: archive[key] for key in ScanSet.__dataclass_fields__})
old_gaps = []
for lo, hi in zip(scans.frame_offsets[:-1], scans.frame_offsets[1:]):
    old_gaps.extend(np.diff(np.unique(scans.point_time_s[lo:hi])).tolist())
boundaries = scans.frame_start_s[1:] - scans.frame_end_s[:-1]
report = {
    "source": str(source.relative_to(ROOT)),
    "prior_within_frame_max_s": max(old_gaps, default=0.0),
    "frame_boundary_max_s": float(boundaries.max()),
    "frame_boundary_gaps_over_0_2_s": int(np.sum(boundaries > 0.2)),
    "updated": time_quality(scans),
}
print(json.dumps(report, ensure_ascii=False, indent=2))

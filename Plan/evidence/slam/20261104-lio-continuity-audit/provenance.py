"""Record input and actual working-tree file hashes, without copying raw data."""
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
paths = [
    "radar_capture_data/na70_sessions/20261104-170341/raw/lidar.pcap",
    "radar_capture_data/na70_sessions/20261104-170341/raw/imu.bin",
    "radar_capture_data/na70_sessions/20261104-170341/raw/imu_chunks.jsonl",
    "radar_capture_data/na70_sessions/20261104-170341/imu_frames.jsonl",
    "Plan/evidence/slam/20261104-lio-diagnostic/lio-package-margin1s/scans.npz",
    "Plan/evidence/slam/20261104-lio-diagnostic/lio-package-margin1s/imu.npz",
    "Plan/evidence/slam/20261104-lio-diagnostic/lio-package-margin1s/manifest.json",
    "Plan/evidence/slam/20261104-lio-diagnostic/trajectory.csv",
    "Plan/evidence/slam/20261104-lio-diagnostic/fastlio-config.yaml",
    "Plan/evidence/slam/20261104-lio-diagnostic/fastlio.log",
    "Plan/evidence/slam/20261104-lio-diagnostic/run_fastlio_diagnostic.sh",
    "packages/na70_core/na70_core/lio_scans.py",
    "web/tests/test_na70_lio_scans.py",
    "web/tests/test_na70_lio_export.py",
    "docs/NA70_CALIBRATION_WORKFLOW.md",
    "Plan/README.md",
    "web/backend/decoder.py",
    "tools/export_na70_lio_scans.py",
    "tools/lio/replay_na70_package.py",
    "tools/lio/apply_fast_lio_na70_patch.py",
    "tools/lio/na70_fast_lio.yaml",
    "Plan/evidence/slam/20261104-lio-continuity-audit/check_packet_gaps.py",
    "Plan/evidence/slam/20261104-lio-continuity-audit/provenance.py",
]
files = {}
for name in paths:
    path = ROOT / name
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    files[name] = {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}

def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8")

result = {"head": git("rev-parse", "HEAD").strip(),
          "git_status_short": git("status", "--short", "--untracked-files=normal"),
          "platform": platform.platform(), "python": sys.version,
          "numpy": importlib.metadata.version("numpy"),
          "pytest": importlib.metadata.version("pytest"),
          "pyserial": importlib.metadata.version("pyserial"), "files": files}
print(json.dumps(result, indent=2, ensure_ascii=False))

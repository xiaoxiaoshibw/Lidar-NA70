"""Analysis-path throughput and result-digest baseline.

Runs ``analyze_capture`` on the real captures under ``web/data`` and records the
wall time together with a digest of the produced per-point masks and the
headline metrics.  A performance change is only accepted when this digest and
these metrics are unchanged, so the baseline proves the optimisation did not
alter the analysis result.
"""
import hashlib
import json
import platform
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.motion import ANALYSIS_VERSION, MotionConfig, analyze_capture


def unique_captures(root: Path) -> dict[str, Path]:
    """Map filename -> data folder, keeping the first folder per filename."""
    picked: dict[str, Path] = {}
    for folder in sorted(root.iterdir()):
        meta_path, frames_path, capture_path = folder / "meta.json", folder / "frames.json", folder / "capture.pcap"
        if not (meta_path.exists() and frames_path.exists() and capture_path.exists()):
            continue
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
        except (ValueError, OSError):
            continue
        picked.setdefault(meta.get("filename", folder.name), folder)
    return picked


def main() -> None:
    web = Path(__file__).resolve().parents[1]
    repeat = 1
    selected: set[str] = set()
    for argument in sys.argv[1:]:
        if argument.startswith("--repeat="):
            repeat = max(1, int(argument.split("=", 1)[1]))
        else:
            selected.add(argument)
    captures = unique_captures(web / "data")
    if selected:
        captures = {name: path for name, path in captures.items() if name in selected}
    results = []
    for name, folder in captures.items():
        frames = json.loads((folder / "frames.json").read_text("utf-8"))
        capture_path = folder / "capture.pcap"
        walls = []
        for _ in range(repeat):
            output = Path(tempfile.mkdtemp(prefix="na70-analysis-"))
            started = time.perf_counter()
            summary = analyze_capture(capture_path, frames, output, MotionConfig())
            walls.append(time.perf_counter() - started)
        masks = (output / "analysis_masks.bin").read_bytes()
        record = dict(
            file=name, folder=folder.name, size=capture_path.stat().st_size, frames=len(frames),
            wall_seconds=round(min(walls), 4), wall_all=[round(value, 4) for value in walls],
            reported_seconds=summary["elapsed_seconds"],
            stable_ray_count=summary["stable_ray_count"], candidate_points=summary["candidate_points"],
            moving_points=summary["moving_points"], moving_frames=summary["moving_frames"],
            moving_tracks=summary["moving_tracks"], band_count=summary["band_count"],
            masks_sha256=hashlib.sha256(masks).hexdigest(), masks_bytes=len(masks),
            bands=[dict(band_id=band["band_id"], label=band["label"], score=round(band["score"], 6),
                        time_start=round(band["time_start"], 6), time_end=round(band["time_end"], 6),
                        point_count=band["point_count"], displacement=round(band["displacement"], 6),
                        speed=round(band["speed"], 6)) for band in summary["bands"]],
        )
        results.append(record)
        print(json.dumps(record, ensure_ascii=False))
    report = dict(environment=platform.platform(), python=sys.version, numpy=np.__version__,
                  analysis_version=ANALYSIS_VERSION, results=results)
    target = web / "test-results" / "benchmark_analysis.json"
    target.parent.mkdir(exist_ok=True, parents=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf-8")
    print(f"wrote {target}")


if __name__ == "__main__":
    main()

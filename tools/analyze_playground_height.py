"""Measure per-sweep relative height bands before playground map filtering."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
from backend.decoder import DECODER_VERSION, scan_pcap  # noqa: E402
from backend.mapping import configuration  # noqa: E402
from backend.sweeps import SWEEP_VERSION, build_sweeps, sweep_configuration  # noqa: E402


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def analyze(path, cfg, high_thresholds=(4.5, 6.0, 8.0)):
    scan = scan_pcap(path)
    sweeps = build_sweeps(path, scan.frames, sweep_configuration(cfg))
    rows, all_heights = [], []
    for sweep in sweeps:
        points = sweep.points(valid_only=True)[:, :3]
        ranges = np.linalg.norm(points, axis=1)
        points = points[np.isfinite(points).all(axis=1) & (ranges >= 2.0) & (ranges <= 60.0)]
        if not len(points):
            rows.append(dict(index=sweep.index, valid_points=0, ground_z=None,
                             height_quantiles=None, high_ratios={str(v): 0.0 for v in high_thresholds}))
            continue
        ground_z = float(np.median(points[:, 2]))
        height = points[:, 2] - ground_z
        all_heights.append(height)
        rows.append(dict(
            index=sweep.index, valid_points=len(points), ground_z=ground_z,
            height_quantiles=dict(zip(("p01", "p05", "p50", "p95", "p99", "max"),
                                      [float(v) for v in np.percentile(height, [1, 5, 50, 95, 99, 100])])),
            high_ratios={str(v): float((height > v).mean()) for v in high_thresholds},
        ))
    heights = np.concatenate(all_heights) if all_heights else np.empty(0)
    return dict(
        input=dict(path=str(Path(path).resolve()), sha256=sha256(path),
                   duration_seconds=scan.duration, packets=scan.valid_packets, sweeps=len(sweeps)),
        decoder_version=DECODER_VERSION, sweep_version=SWEEP_VERSION,
        global_height_quantiles=dict(zip(("p01", "p05", "p50", "p95", "p99", "max"),
                                         [float(v) for v in np.percentile(heights, [1, 5, 50, 95, 99, 100])])) if len(heights) else None,
        high_ratio={str(v): float((heights > v).mean()) for v in high_thresholds} if len(heights) else {},
        sweeps_above_threshold={str(v): int(sum(row["high_ratios"].get(str(v), 0) > 0.05 for row in rows))
                                for v in high_thresholds},
        sweeps=rows,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pcaps", nargs="+", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cfg = configuration()
    reports = {}
    for path in args.pcaps:
        report = analyze(path, cfg)
        reports[path.stem] = report
        (args.output_dir / f"{path.stem}-height.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(path.name, json.dumps({"global": report["global_height_quantiles"],
                                     "high_ratio": report["high_ratio"],
                                     "sweeps_above": report["sweeps_above_threshold"]}, ensure_ascii=False))
    (args.output_dir / "height-summary.json").write_text(
        json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()


"""Analyze fixed and coverage-driven NA70 sweep candidates on real PCAPs.

This command intentionally delegates packet parsing to ``backend.decoder`` and
all grouping to ``backend.sweeps``; it is diagnostics, not a second scan model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))

from backend.decoder import DECODER_VERSION, scan_pcap  # noqa: E402
from backend.mapping import configuration as mapping_configuration  # noqa: E402
from backend.sweeps import (  # noqa: E402
    SWEEP_VERSION, SweepConfig, build_sweeps, correspondence_coverage,
    sweep_configuration,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values, q):
    return round(float(np.percentile(values, q)), 6) if values else None


def spatial_points(sweep):
    points = sweep.points(valid_only=True)[:, :3]
    ranges = np.linalg.norm(points, axis=1)
    return points[np.isfinite(points).all(axis=1) & (ranges >= 1.0) & (ranges <= 80.0)]


def analyze_mode(sweeps, config):
    rows = []
    previous_bins = None
    previous_points = None
    for sweep in sweeps:
        metadata = sweep.metadata(config, include_packets=False)
        valid_codes = sweep.codes[sweep.valid_mask]
        bins = sweep.angular_bins(config)
        points = spatial_points(sweep)
        ranges = np.linalg.norm(points, axis=1) if len(points) else np.empty(0)
        if previous_bins is None:
            ray_overlap = None
            spatial = {str(value): None for value in (0.3, 0.6, 1.0)}
        else:
            left = {tuple(value) for value in previous_bins.tolist()}
            right = {tuple(value) for value in bins.tolist()}
            ray_overlap = round(len(left & right) / max(1, min(len(left), len(right))), 6)
            spatial = correspondence_coverage(previous_points, points)
        metadata.update(
            repeat_ray_ratio=ray_overlap,
            correspondence_coverage=spatial,
            range_m=dict(
                min=round(float(ranges.min()), 6) if len(ranges) else None,
                p25=round(float(np.percentile(ranges, 25)), 6) if len(ranges) else None,
                median=round(float(np.median(ranges)), 6) if len(ranges) else None,
                p75=round(float(np.percentile(ranges, 75)), 6) if len(ranges) else None,
                p95=round(float(np.percentile(ranges, 95)), 6) if len(ranges) else None,
                max=round(float(ranges.max()), 6) if len(ranges) else None,
            ),
            near_field_ratio_10m=round(float((ranges <= 10).mean()), 6) if len(ranges) else 0.0,
            filtered_spatial_point_count=len(points),
            invalid_angle_point_count=int(((sweep.codes[:, 0] > 0)
                                            & ((sweep.codes[:, 1] == 0xFFFF)
                                               | (sweep.codes[:, 2] == 0xFFFF))).sum()),
            motion_distortion_risk=metadata["duration"] > 0.5,
        )
        rows.append(metadata)
        previous_bins = bins
        previous_points = points

    comparable = rows[1:]
    return dict(
        config=config.__dict__,
        sweep_count=len(rows),
        complete_sweeps=sum(row["complete"] for row in rows),
        incomplete_sweeps=sum(not row["complete"] for row in rows),
        empty_sweeps=sum(row["valid_point_count"] == 0 for row in rows),
        anomalous_sweeps=sum(bool(row["anomalies"]) for row in rows),
        packet_count=sum(row["packet_count"] for row in rows),
        duration_seconds=dict(
            median=percentile([row["duration"] for row in rows], 50),
            p95=percentile([row["duration"] for row in rows], 95),
            max=max((row["duration"] for row in rows), default=None),
        ),
        repeat_ray_ratio_median=percentile(
            [row["repeat_ray_ratio"] for row in comparable if row["repeat_ray_ratio"] is not None], 50),
        correspondence_coverage_median={
            distance: percentile([
                row["correspondence_coverage"][distance] for row in comparable
                if row["correspondence_coverage"][distance] is not None
            ], 50)
            for distance in ("0.3", "0.6", "1.0")
        },
        sweeps=rows,
    )


def fixed_config(packet_count):
    return SweepConfig(
        min_packets=packet_count,
        min_points=0,
        max_packets=packet_count,
        max_duration=3600.0,
        target_angular_bins=1_000_000,
        min_angular_coverage=1.0,
    )


def analyze_capture(path: Path, variable_config: SweepConfig):
    started = time.perf_counter()
    scan = scan_pcap(path)
    modes = {}
    for name, config in (("fixed_100", fixed_config(100)),
                         ("fixed_200", fixed_config(200)),
                         ("coverage_driven", variable_config)):
        modes[name] = analyze_mode(build_sweeps(path, scan.frames, config), config)
    return dict(
        schema_version=1,
        decoder_version=DECODER_VERSION,
        sweep_version=SWEEP_VERSION,
        input=dict(path=str(path.resolve()), size_bytes=path.stat().st_size,
                   sha256=sha256(path), duration_seconds=scan.duration,
                   valid_packets=scan.valid_packets, invalid_packets=scan.invalid_packets,
                   legacy_100_packet_frames=len(scan.frames), warnings=scan.warnings),
        modes=modes,
        elapsed_seconds=round(time.perf_counter() - started, 3),
    )


def markdown(report):
    source = report["input"]
    lines = [
        f"# {Path(source['path']).name} sweep coverage report", "",
        f"- SHA-256: `{source['sha256']}`",
        f"- Duration: {source['duration_seconds']:.3f} s",
        f"- Packets / legacy frames: {source['valid_packets']} / {source['legacy_100_packet_frames']}",
        f"- Decoder / sweep: `{report['decoder_version']}` / `{report['sweep_version']}`", "",
        "| Mode | Sweeps | Complete | Incomplete | Empty | Anomalous | Duration p50/p95 (s) | Repeat rays p50 | Correspondence 0.3/0.6/1.0 m p50 |",
        "|---|---:|---:|---:|---:|---:|---|---:|---|",
    ]
    for name, mode in report["modes"].items():
        duration = mode["duration_seconds"]
        coverage = mode["correspondence_coverage_median"]
        lines.append(
            f"| {name} | {mode['sweep_count']} | {mode['complete_sweeps']} | "
            f"{mode['incomplete_sweeps']} | {mode['empty_sweeps']} | {mode['anomalous_sweeps']} | "
            f"{duration['median']} / {duration['p95']} | {mode['repeat_ray_ratio_median']} | "
            f"{coverage['0.3']} / {coverage['0.6']} / {coverage['1.0']} |"
        )
    lines.extend([
        "", "The JSON companion contains every candidate sweep boundary, angular range, "
        "range quantiles, near-field ratio, time anomaly, and adjacent-sweep coverage. "
        "`motion_distortion_risk` is a duration warning, not proof of distortion.", "",
    ])
    return "\n".join(lines)


def write_index(output_dir, command, environment):
    reports = []
    summary = ["# NA70 playground sweep coverage summary", "",
               "All rows use the frozen `mapping.yaml` sweep configuration.", "",
               "| Capture | Packets | Sweeps | Complete | 0.3 m | 0.6 m | 1.0 m | Anomalous |",
               "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for json_path in sorted(output_dir.glob("*-sweeps.json")):
        report = json.loads(json_path.read_text(encoding="utf-8"))
        mode = report["modes"]["coverage_driven"]
        coverage = mode["correspondence_coverage_median"]
        reports.append(dict(input=report["input"]["path"], json=json_path.name,
                            markdown=json_path.with_suffix(".md").name,
                            sha256=report["input"]["sha256"]))
        summary.append(
            f"| {Path(report['input']['path']).name} | {report['input']['valid_packets']} | "
            f"{mode['sweep_count']} | {mode['complete_sweeps']} | {coverage['0.3']} | "
            f"{coverage['0.6']} | {coverage['1.0']} | {mode['anomalous_sweeps']} |"
        )
    manifest = dict(schema_version=1, command=command, environment=environment, reports=reports)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pcaps", nargs="*", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-duration", type=float)
    parser.add_argument("--target-angular-bins", type=int)
    args = parser.parse_args()
    base = sweep_configuration(mapping_configuration())
    config = SweepConfig(**{
        **base.__dict__,
        **({"max_duration": args.max_duration} if args.max_duration is not None else {}),
        **({"target_angular_bins": args.target_angular_bins}
           if args.target_angular_bins is not None else {}),
    }).validate()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    environment = dict(platform=platform.platform(), python=sys.version.split()[0],
                       numpy=np.__version__, pid=os.getpid())
    for path in args.pcaps:
        report = analyze_capture(path, config)
        stem = path.stem + "-sweeps"
        json_path = args.output_dir / f"{stem}.json"
        md_path = args.output_dir / f"{stem}.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                        allow_nan=False), encoding="utf-8")
        md_path.write_text(markdown(report), encoding="utf-8")
        print(f"{path.name}: {report['elapsed_seconds']} s -> {json_path}")
    write_index(args.output_dir, " ".join(sys.argv), environment)


if __name__ == "__main__":
    main()

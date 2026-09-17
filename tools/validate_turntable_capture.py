"""Preflight a fixed-point turntable capture using production contracts."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))

from backend.decoder import DECODER_VERSION, scan_pcap  # noqa: E402
from backend.turntable import TURNTABLE_VERSION, load_angle_log, load_calibration  # noqa: E402


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pcap", action="append", required=True)
    parser.add_argument("--angle-log", required=True)
    parser.add_argument("--calibration", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    paths = {Path(value).name: Path(value).resolve() for value in args.pcap}
    scans = {name: scan_pcap(path) for name, path in paths.items()}
    durations = {name: scan.duration for name, scan in scans.items()}
    rows = load_angle_log(Path(args.angle_log), durations)
    calibration = load_calibration(Path(args.calibration))
    excluded = {}
    for name, duration in durations.items():
        intervals = sorted((row.valid_start_s, row.valid_end_s) for row in rows if row.source_pcap == name and row.valid)
        gaps = []; cursor = 0.0
        for start, end in intervals:
            if start > cursor: gaps.append([cursor, start])
            cursor = max(cursor, end)
        if cursor < duration: gaps.append([cursor, duration])
        excluded[name] = gaps
    report = dict(version=TURNTABLE_VERSION, status="PASS", label="DEVELOPMENT / DIAGNOSTIC / ESTIMATED_ANGLE",
                  decoder_version=DECODER_VERSION, python=sys.version, platform=platform.platform(),
                  inputs={name: dict(path=str(path), sha256=sha256(path), duration_s=scans[name].duration,
                                     frame_count=len(scans[name].frames), valid_packets=scans[name].valid_packets,
                                     warnings=scans[name].warnings) for name, path in paths.items()},
                  segments=[dict(segment_id=row.segment_id, source_pcap=row.source_pcap,
                                  valid=row.valid, yaw_deg=row.yaw_deg, angle_source=row.angle_source,
                                  valid_start_s=row.valid_start_s, valid_end_s=row.valid_end_s,
                                  estimated=row.estimated) for row in rows],
                  calibration_status=calibration.get("status"),
                  excluded_rotation_intervals=excluded,
                  note="This preflight validates contracts and time bounds; it does not claim measured turntable accuracy.")
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), "utf-8")
    print(output)


if __name__ == "__main__":
    main()

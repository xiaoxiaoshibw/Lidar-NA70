"""Inspect PCAP capture timestamps and IMU host-receive chunks, without claiming synchronization."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "packages/na70_core"))
from backend.decoder import scan_pcap  # noqa: E402


def audit(pcap, chunks):
    try:
        scan = scan_pcap(Path(pcap))
        packet_times = [t for frame in scan.frames for t in frame["packet_capture_times"]]
        with Path(chunks).open(encoding="utf-8") as f:
            chunk_times = [json.loads(line)["t_unix"] for line in f]
        if not packet_times or not chunk_times:
            raise ValueError("one or both streams have no observed timestamps")
        if any(not isinstance(t, (int, float)) or not math.isfinite(t) for t in packet_times + chunk_times):
            raise ValueError("invalid or nonfinite timestamp")
        if any(b < a for times in (packet_times, chunk_times) for a, b in zip(times, times[1:])):
            raise ValueError("host timestamp moved backwards")
        start = max(packet_times[0], chunk_times[0])
        end = min(packet_times[-1], chunk_times[-1])
        return {"schema": "na70-host-overlap-v1", "status": "observed" if end > start else "no_overlap",
                "lidar_packets": len(packet_times), "imu_chunks": len(chunk_times),
                "lidar_first_unix_s": packet_times[0], "lidar_last_unix_s": packet_times[-1],
                "imu_first_unix_s": chunk_times[0], "imu_last_unix_s": chunk_times[-1],
                "host_overlap_s": round(max(0.0, end - start), 6),
                "max_lidar_capture_gap_s": round(max((b-a for a, b in zip(packet_times, packet_times[1:])), default=0), 6),
                "max_imu_chunk_gap_s": round(max((b-a for a, b in zip(chunk_times, chunk_times[1:])), default=0), 6),
                "invalid_packets": scan.invalid_packets, "warnings": scan.warnings,
                "limits": ["host receive times do not establish sensor sampling times or synchronization",
                           "overlap of endpoints does not prove continuous valid data"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"schema": "na70-host-overlap-v1", "status": "failed", "error": str(exc)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pcap", type=Path)
    parser.add_argument("imu_chunks", type=Path)
    args = parser.parse_args()
    result = audit(args.pcap, args.imu_chunks)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

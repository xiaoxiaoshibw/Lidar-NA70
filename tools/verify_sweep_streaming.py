"""Verify one-shot and chunked streaming SweepBuilder boundaries on PCAPs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))

from backend.decoder import scan_pcap  # noqa: E402
from backend.mapping import configuration  # noqa: E402
from backend.sweeps import SweepBuilder, packets_from_scan, sweep_configuration  # noqa: E402


def signatures(sweeps):
    return [(s.index, s.packets[0].source_index, s.packets[-1].source_index,
             len(s.packets), s.completion_reason, s.complete) for s in sweeps]


def digest(rows):
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pcaps", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cfg = sweep_configuration(configuration())
    reports = []
    for path in args.pcaps:
        scan = scan_pcap(path)
        batch_builder = SweepBuilder(cfg)
        batch = signatures(batch_builder.build(packets_from_scan(path, scan.frames)))
        stream_builder = SweepBuilder(cfg)
        streamed = []
        # Deliberately vary adapter chunk boundaries; the builder still receives
        # packets one by one, exactly as a bounded UDP/ROS2 queue will.
        chunk_sizes = (1, 7, 64, 3, 128, 11)
        chunk, chunk_index = [], 0
        for packet in packets_from_scan(path, scan.frames):
            chunk.append(packet)
            if len(chunk) == chunk_sizes[chunk_index % len(chunk_sizes)]:
                for item in chunk:
                    streamed.extend(stream_builder.add(item))
                chunk = []
                chunk_index += 1
        for item in chunk:
            streamed.extend(stream_builder.add(item))
        streamed.extend(stream_builder.finish())
        streamed = signatures(streamed)
        reports.append(dict(
            input=str(path.resolve()), packets=scan.valid_packets,
            batch_sweeps=len(batch), streaming_sweeps=len(streamed),
            batch_sha256=digest(batch), streaming_sha256=digest(streamed),
            identical=batch == streamed,
        ))
        print(path.name, reports[-1]["identical"], len(batch))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(schema_version=1, reports=reports),
                                      ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

"""Replay raw YESENSE chunks and compare each decoded frame with the saved JSONL."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

package_root = Path(__file__).resolve().parents[1] / "packages" / "na70_core"
if package_root.is_dir():
    sys.path.insert(0, str(package_root))
from na70_core.capture.yesense import FrameParser  # noqa: E402


def audit(root):
    root = Path(root)
    parser = FrameParser()
    count = offset = mismatches = resets = 0
    segment = None
    errors = []
    checksum_events = []
    try:
        with (root / "raw/imu.bin").open("rb") as raw, \
             (root / "raw/imu_chunks.jsonl").open(encoding="utf-8") as chunks, \
             (root / "imu_frames.jsonl").open(encoding="utf-8") as output:
            for line in chunks:
                chunk = json.loads(line)
                if chunk["offset"] != offset or not isinstance(chunk["bytes"], int) or chunk["bytes"] <= 0:
                    raise ValueError(f"invalid chunk at byte {offset}")
                if segment is not None and chunk["segment"] != segment:
                    if chunk["segment"] < segment:
                        raise ValueError("segment went backwards")
                    parser.reset()
                    resets += 1
                segment = chunk["segment"]
                data = raw.read(chunk["bytes"])
                if len(data) != chunk["bytes"]:
                    raise ValueError(f"raw stream truncated at byte {offset}")
                offset += len(data)
                previous_checksum = parser.bad_checksum
                frames = parser.feed(data)
                if parser.bad_checksum > previous_checksum and len(checksum_events) < 100:
                    checksum_events.append({"chunk_start": offset - len(data), "chunk_bytes": len(data),
                                            "host_unix_s": chunk["t_unix"],
                                            "errors": parser.bad_checksum - previous_checksum})
                for frame in frames:
                    line = output.readline()
                    if not line:
                        raise ValueError(f"saved frames end before raw frame {count}")
                    saved = json.loads(line)
                    expected = {"t_mono": round(chunk["t_mono"], 6), "t_unix": round(chunk["t_unix"], 6),
                                "segment": segment, "tid": frame.tid}
                    for field in ("accel", "gyro", "euler", "quaternion"):
                        value = getattr(frame, field)
                        if value is not None:
                            expected[field] = [round(v, 6) for v in value]
                    if frame.sample_us is not None:
                        expected["sample_us"] = frame.sample_us
                    if saved != expected:
                        mismatches += 1
                    count += 1
            if raw.read(1):
                raise ValueError("raw stream contains bytes not covered by chunk records")
            if output.readline():
                raise ValueError("saved frames contain rows not reconstructed from raw bytes")
        if mismatches:
            errors.append(f"{mismatches} decoded frames differ from saved JSONL")
        if parser.bad_checksum or parser.bad_payload:
            errors.append(f"raw parse errors: checksum={parser.bad_checksum}, payload={parser.bad_payload}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(str(exc))
    return {"schema": "na70-imu-raw-audit-v1", "status": "failed" if errors else "ok", "errors": errors,
            "raw_bytes": offset, "matched_frames": count - mismatches, "mismatches": mismatches,
            "checksum_errors": parser.bad_checksum, "payload_errors": parser.bad_payload,
            "checksum_events": checksum_events,
            "discarded_bytes": parser.discarded_bytes, "pending_bytes": parser.pending_bytes,
            "segment_resets": resets,
            "limits": ["uses the capture parser; not independent protocol validation",
                       "host receive timestamps are not device sampling timestamps"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    result = audit(parser.parse_args().session)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

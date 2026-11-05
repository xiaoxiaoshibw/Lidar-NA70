"""Measure the NA70 field of view from a session PCAP.

This is the classic-PCAP path: it decodes raw distance/pitch/azimuth codes with
``web/backend/decoder.py`` and reports the min/max angle codes of valid returns
(distance code > 0 and neither angle code 0xFFFF), converted to degrees.
The 0xFFFF and zero-code ray counts are recorded as-is: their vendor semantics
are UNVERIFIED, so no conclusion is drawn from them.

NA70 is a non-repetitive-scanning lidar: filling the full pattern needs a long
static session (>= 60 s recommended).  A short session only yields a lower
bound on the min/max angles.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "na70_core"))
sys.path.insert(0, str(ROOT / "web"))
from backend import decoder  # noqa: E402

AZIMUTH_CODE_TO_DEG = 0.0432
PITCH_CODE_TO_DEG = 0.0258
ANGLE_CODE_ALL_FF = 0xFFFF


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _counts(distance, pitch, yaw):
    both_ff = (pitch == ANGLE_CODE_ALL_FF) & (yaw == ANGLE_CODE_ALL_FF)
    return {
        "rays": int(len(distance)),
        "valid_rays": int(((distance > 0) & (pitch != ANGLE_CODE_ALL_FF)
                           & (yaw != ANGLE_CODE_ALL_FF)).sum()),
        "distance_code_zero": int((distance == 0).sum()),
        "distance_code_ffff": int((distance == ANGLE_CODE_ALL_FF).sum()),
        "pitch_code_ffff": int((pitch == ANGLE_CODE_ALL_FF).sum()),
        "yaw_code_ffff": int((yaw == ANGLE_CODE_ALL_FF).sum()),
        "both_angle_codes_ffff": int(both_ff.sum()),
        "pitch_code_zero": int((pitch == 0).sum()),
        "yaw_code_zero": int((yaw == 0).sum()),
    }


def measure(pcap):
    """Scan one capture and return the per-frame and session FOV statistics."""
    scan = decoder.scan_pcap(Path(pcap))
    per_frame = []
    frame_valid_pitch = []
    frame_valid_yaw = []
    total = None
    with Path(pcap).open("rb") as source:
        for frame in scan.frames:
            codes = decoder.decode_frame_codes(Path(pcap), frame, source=source)
            distance, pitch, yaw = codes[:, 0], codes[:, 1], codes[:, 2]
            counts = _counts(distance, pitch, yaw)
            valid = (distance > 0) & (pitch != ANGLE_CODE_ALL_FF) & (yaw != ANGLE_CODE_ALL_FF)
            entry = {
                "frame": int(frame["index"]),
                "capture_time_s": float(frame["capture_time"]),
                **counts,
            }
            if valid.any():
                pitch_min, pitch_max = int(pitch[valid].min()), int(pitch[valid].max())
                yaw_min, yaw_max = int(yaw[valid].min()), int(yaw[valid].max())
                entry.update({
                    "pitch_code_min": pitch_min, "pitch_code_max": pitch_max,
                    "azimuth_code_min": yaw_min, "azimuth_code_max": yaw_max,
                    "pitch_deg_min": pitch_min * PITCH_CODE_TO_DEG,
                    "pitch_deg_max": pitch_max * PITCH_CODE_TO_DEG,
                    "azimuth_deg_min": yaw_min * AZIMUTH_CODE_TO_DEG,
                    "azimuth_deg_max": yaw_max * AZIMUTH_CODE_TO_DEG,
                })
                frame_valid_pitch.append((pitch_min, pitch_max))
                frame_valid_yaw.append((yaw_min, yaw_max))
            else:
                entry.update({"pitch_code_min": None, "pitch_code_max": None,
                              "azimuth_code_min": None, "azimuth_code_max": None})
            if total is None:
                total = {}
            for key in counts:
                total[key] = total.get(key, 0) + counts[key]
            per_frame.append(entry)

    if frame_valid_pitch:
        pitch_min = min(low for low, _ in frame_valid_pitch)
        pitch_max = max(high for _, high in frame_valid_pitch)
        yaw_min = min(low for low, _ in frame_valid_yaw)
        yaw_max = max(high for _, high in frame_valid_yaw)
    else:
        pitch_min = pitch_max = yaw_min = yaw_max = None
    session = {
        **(total or {}),
        "pitch_code_min": pitch_min, "pitch_code_max": pitch_max,
        "azimuth_code_min": yaw_min, "azimuth_code_max": yaw_max,
    }
    if pitch_min is not None:
        session.update({
            "pitch_deg_min": pitch_min * PITCH_CODE_TO_DEG,
            "pitch_deg_max": pitch_max * PITCH_CODE_TO_DEG,
            "pitch_span_deg": (pitch_max - pitch_min) * PITCH_CODE_TO_DEG,
            "azimuth_deg_min": yaw_min * AZIMUTH_CODE_TO_DEG,
            "azimuth_deg_max": yaw_max * AZIMUTH_CODE_TO_DEG,
            "azimuth_span_deg": (yaw_max - yaw_min) * AZIMUTH_CODE_TO_DEG,
        })
    return {
        "schema": "na70-fov-measurement-v1",
        "status": "MEASURED",
        "decoder_version": decoder.DECODER_VERSION,
        "pcap": {"path": str(pcap), "sha256": sha256(pcap)},
        "frames": len(scan.frames),
        "duration_s": float(scan.duration),
        "session": session,
        "per_frame": per_frame,
        "note": "NA70 是非重复扫描：铺满完整 pattern 需要长时间（建议 >=60 s 静止）会话；"
                "短会话的最小/最大角度是下界估计。0xFFFF 与零码射线的厂家语义未核实，"
                "此处计数仅作记录，不下结论。",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcap", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path,
                        help="output JSON path; the file must not exist")
    args = parser.parse_args(argv)
    if args.output.exists():
        print(json.dumps({"status": "OUTPUT_EXISTS", "error": f"{args.output} 已存在，不覆盖"},
                         ensure_ascii=False))
        return 2
    try:
        result = measure(args.pcap)
    except (OSError, ValueError, decoder.CaptureError) as exc:
        print(json.dumps({"status": "INVALID_DATA", "error": str(exc)}, ensure_ascii=False))
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    session = result["session"]
    print(json.dumps({
        "status": result["status"], "frames": result["frames"],
        "pitch_span_deg": session.get("pitch_span_deg"),
        "azimuth_span_deg": session.get("azimuth_span_deg"),
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Exploratory IMU spectrum by frame index; nominal rate is not a sampling timestamp."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from scipy.signal import welch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/na70_core"))
from na70_core.capture.yesense import tid_follows  # noqa: E402


def analyze(path, nominal_hz=200.0):
    path = Path(path)
    if not np.isfinite(nominal_hz) or nominal_hz <= 0:
        raise ValueError("nominal_hz must be finite and positive")
    values, previous_tid, previous_segment = [], None, None
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for index, line in enumerate(f):
            digest.update(line)
            row = json.loads(line)
            fields = row.get("fields", row)
            segment = row.get("segment", 0)
            tid = row["tid"]
            if not isinstance(tid, int) or not 0 <= tid <= 65535 or not isinstance(segment, int) or segment < 0:
                raise ValueError(f"invalid tid or segment at row {index}")
            if previous_tid is not None and (segment != previous_segment or not tid_follows(previous_tid, tid)):
                raise ValueError(f"segment or sequence discontinuity at row {index}")
            previous_tid, previous_segment = tid, segment
            sample = np.asarray([*fields["accel"], *fields["gyro"]], dtype=float)
            if sample.shape != (6,) or not np.isfinite(sample).all():
                raise ValueError(f"invalid sample at row {index}")
            values.append(sample)
    data = np.asarray(values)
    if len(data) < 1024:
        raise ValueError("at least 1024 complete samples are required")
    freq, psd = welch(data, fs=nominal_hz, nperseg=1024, axis=0, detrend="constant")
    selected = (freq >= 0.5) & (freq <= min(90.0, nominal_hz / 2))
    if not np.any(selected):
        raise ValueError("nominal rate is too low for the diagnostic frequency range")
    axes = {}
    for axis, name in enumerate(("ax", "ay", "az", "gx", "gy", "gz")):
        spectrum = psd[selected, axis]
        peak = int(np.argmax(spectrum))
        axes[name] = {"std": float(np.std(data[:, axis], ddof=1)),
                      "nominal_peak_hz": float(freq[selected][peak]) if spectrum[peak] > 0 else None,
                      "peak_psd": float(spectrum[peak])}
    return {"schema": "na70-imu-spectrum-diagnostic-v1", "samples": len(data),
            "sha256": digest.hexdigest(), "nominal_sample_hz": nominal_hz,
            "frequency_range_nominal_hz": [float(freq[selected][0]), float(freq[selected][-1])],
            "welch_segment_samples": 1024, "axes": axes,
            "limits": ["sample index and nominal rate only; device sample times unavailable",
                       "peak frequencies may be aliased or shifted; not evidence of a physical source",
                       "gyro unit inferred, not independently calibrated", "not an Allan variance or noise-density estimate"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frames", type=Path)
    parser.add_argument("--nominal-hz", type=float, default=200.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.frames, args.nominal_hz)
    with args.output.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

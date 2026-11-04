"""Export a session's decoded scans and IMU samples as one LIO input package.

The package keeps packet-level time, original frame/packet/point indices and an
explicit time-quality/blocker list.  It does not deskew, transform points, or
claim LIO readiness: the consumer must check the time level, calibration and
motion envelope.
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
from na70_core import lio_scans  # noqa: E402
from na70_core.calibration import imu as imu_core  # noqa: E402
from na70_core.calibration.frames import load_imu_frames  # noqa: E402


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _calibration(path):
    if path is None:
        return None, ["calibration_missing"]
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "T_imu_lidar" in data:
        if data["T_imu_lidar"] is None:
            return None, ["calibration_incomplete"]
        matrix = np.asarray(data["T_imu_lidar"], dtype=np.float64)
    elif "R_imu_lidar" in data and ("t_imu_lidar_m" in data or "t_imu_lidar" in data):
        translation = data.get("t_imu_lidar_m", data.get("t_imu_lidar"))
        if data["R_imu_lidar"] is None or translation is None:
            return None, ["calibration_incomplete"]
        matrix = np.eye(4)
        matrix[:3, :3] = np.asarray(data["R_imu_lidar"], dtype=np.float64)
        matrix[:3, 3] = np.asarray(translation, dtype=np.float64).reshape(3)
    else:
        raise ValueError("calibration needs T_imu_lidar or R_imu_lidar + t_imu_lidar(_m)")
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError("calibration matrix must be finite 4x4")
    reasons = []
    if not (data.get("gate") or {}).get("holdout_validated"):
        reasons.append("calibration_candidate_unverified")
    return matrix, reasons


def export(pcap, imu_frames, output, *, calibration_path=None, start_frame=0,
           max_frames=None, imu_margin_s=0.1, max_packet_gap_s=0.2, install_direction=1):
    output = Path(output)
    scan = decoder.scan_pcap(pcap)
    end = len(scan.frames) if max_frames is None else min(len(scan.frames), start_frame + max_frames)
    if not 0 <= start_frame < end:
        raise ValueError("frame range is empty")
    frames = []
    with Path(pcap).open("rb") as source:
        for index in range(start_frame, end):
            frame = scan.frames[index]
            points = decoder.decode_frame(pcap, frame, install_direction, source=source)
            frames.append((points, np.asarray(frame["packet_capture_times"], dtype=np.float64)))
    scans = lio_scans.build_scans(frames)
    quality = lio_scans.time_quality(scans, max_packet_gap_s=max_packet_gap_s)
    origin_unix = float(frames[0][1][0])
    end_unix = float(frames[-1][1][-1])

    data = load_imu_frames(imu_frames)
    selected = (data["unix"] >= origin_unix - imu_margin_s) & (data["unix"] <= end_unix + imu_margin_s)
    if int(selected.sum()) < 10:
        raise ValueError("fewer than ten IMU samples in the scan window")
    raw_rows = []
    with Path(imu_frames).open(encoding="utf-8") as source:
        for line in source:
            raw_rows.append(json.loads(line))
    window_rows = [row for row, keep in zip(raw_rows, selected) if keep]
    has_device = all(row.get("sample_us") is not None for row in window_rows)
    if has_device:
        device_time_s, wraps = imu_core.unwrap_microseconds(
            np.asarray([row["sample_us"] for row in window_rows], dtype=np.int64))
    else:
        device_time_s = np.empty(0, dtype=np.float64)
        wraps = 0

    reasons = []
    if has_device:
        time_source = "device_field_present_unverified"
        reasons.append("device_time_semantics_unverified")
    else:
        time_source = "host_receive_only"
        reasons.append("time_level_C")
    calibration, calibration_reasons = _calibration(calibration_path)
    reasons.extend(calibration_reasons)
    reasons.append("motion_envelope_unverified")
    covers = bool(data["unix"][selected][0] <= origin_unix and data["unix"][selected][-1] >= end_unix)
    if not covers:
        reasons.append("imu_does_not_bracket_scan")
    if quality["gaps_over_limit"]:
        reasons.append("packet_gap_over_limit")

    output.mkdir(parents=True, exist_ok=False)
    lio_scans.save_npz(output / "scans.npz", scans, origin_unix_s=origin_unix)
    np.savez_compressed(
        output / "imu.npz",
        t_host_unix=data["unix"][selected], t_mono=data["times"][selected],
        segment=data["segments"][selected], gyro_deg_s=data["gyro"][selected],
        accel_m_s2=data["accel"][selected], device_time_s=device_time_s,
        has_device_time=np.array([has_device]), device_time_wraps=np.array([wraps]))
    manifest = {
        "schema": "na70-lio-scan-package-v1",
        "status": "EXPORTED_BLOCKED" if reasons else "EXPORTED",
        "lio_ready": False,
        "block_reasons": sorted(set(reasons)),
        "time_source": time_source,
        "time_quality": quality,
        "scans": {"start_frame": start_frame, "frames": end - start_frame,
                  "points": int(len(scans.points)), "origin_unix_s": origin_unix,
                  "end_unix_s": end_unix},
        "imu": {"samples": int(selected.sum()), "device_time_present": has_device,
                "device_time_wraps": wraps,
                "first_unix_s": float(data["unix"][selected][0]),
                "last_unix_s": float(data["unix"][selected][-1]),
                "brackets_scan": covers,
                "source": str(imu_frames), "source_sha256": sha256(imu_frames)},
        "calibration": None if calibration is None else calibration.tolist(),
        "files": {
            "pcap": {"path": str(pcap), "sha256": sha256(pcap)},
            "scans": {"path": "scans.npz", "sha256": sha256(output / "scans.npz")},
            "imu": {"path": "imu.npz", "sha256": sha256(output / "imu.npz")},
        },
        "note": "逐点时间为包级近似；适配器/滤波器仍需检查时间等级、外参与运动包络。",
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (output / "REPORT.md").write_text(
        "# NA70 LIO 扫描包\n\n"
        f"- 状态：**{manifest['status']}**，`lio_ready=false`\n"
        f"- 帧：{manifest['scans']['frames']}，点：{manifest['scans']['points']}\n"
        f"- 时间源：`{time_source}`；时间质量：`{quality['time_quality']}`\n"
        f"- 阻塞项：{', '.join(manifest['block_reasons'])}\n\n"
        "逐点时间由包时间复制，不是实测逐点时间；本包不执行去畸变、坐标变换或 SLAM。\n",
        encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcap", required=True, type=Path)
    parser.add_argument("--imu-frames", required=True, type=Path)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--imu-margin-s", type=float, default=0.1)
    parser.add_argument("--max-packet-gap-s", type=float, default=0.2)
    parser.add_argument("--install-direction", type=int, choices=(1, 2), default=1)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = export(args.pcap, args.imu_frames, args.output,
                          calibration_path=args.calibration, start_frame=args.start_frame,
                          max_frames=args.max_frames, imu_margin_s=args.imu_margin_s,
                          max_packet_gap_s=args.max_packet_gap_s,
                          install_direction=args.install_direction)
    except (OSError, ValueError, TypeError, KeyError, decoder.CaptureError) as exc:
        print(json.dumps({"status": "INVALID_DATA", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": manifest["status"], "points": manifest["scans"]["points"],
                      "block_reasons": manifest["block_reasons"], "output": str(args.output)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
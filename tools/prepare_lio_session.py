"""Audit PCAP + normalized IMU CSV; export packet-timed diagnostic inputs.

Run with web/.venv/Scripts/python.exe. No USB decoding, clock fitting or SLAM.
Exit 0: supplied input declarations pass, 2: blocked/invalid. Never overwrites.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "na70_core"))
sys.path.insert(0, str(ROOT / "web"))
from backend import decoder, thin_wire  # noqa: E402
from na70_core import RayBatch  # noqa: E402
from na70_core.lio import ImuSamples, prepare_inputs  # noqa: E402


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def reject_nonfinite(token):
    raise ValueError(f"nonfinite JSON value: {token}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcap", required=True, type=Path)
    parser.add_argument("--imu-csv", type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "tools/lio/preflight_default.yaml")
    parser.add_argument("--end-frame", required=True, type=int)
    parser.add_argument("--duration", type=float, default=0.1)
    parser.add_argument("--install-direction", choices=(1, 2), type=int, default=1)
    parser.add_argument("--evidence-kind", required=True,
                        choices=("SYNTHETIC", "PUBLIC_DATASET", "REAL_PCAP_DIAGNOSTIC"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    # A failed or cancelled preparation remains inspectable; never reuse its path.
    args.output.mkdir(parents=True, exist_ok=False)
    cfg = None
    try:
        cfg = json.loads(args.config.read_text("utf-8"), parse_constant=reject_nonfinite)
        if not np.isfinite(args.duration) or args.duration <= 0:
            raise ValueError("duration must be positive and finite")
        scan = decoder.scan_pcap(args.pcap)
        if not 0 <= args.end_frame < len(scan.frames):
            raise ValueError("end-frame outside capture")
        window_cfg = thin_wire.load_config()
        window_cfg.update(install_direction=args.install_direction,
                          max_packet_gap_s=cfg["max_packet_gap_s"],
                          max_window_points=cfg["max_window_points"])
        timeline = thin_wire.packet_timeline(scan, window_cfg)
        packets, window = thin_wire.select_window(timeline, args.end_frame, args.duration, window_cfg)
        if window["time_quality"] != "ok" or window["partial_window"]:
            raise ValueError(f"window rejected: {window['time_quality']}")
        arrays = thin_wire.decode_window(args.pcap, scan, packets, window_cfg)
        capture_sha = sha256_file(args.pcap)
        batch = RayBatch.from_decoded(
            capture_sha256=capture_sha, coordinate_frame="lidar_link",
            install_direction=args.install_direction,
            **{key: arrays[key] for key in ("codes", "points", "frame_indices", "packet_indices",
                                          "point_indices", "device_times", "capture_times")})
        imu = None
        if args.imu_csv:
            imu = ImuSamples.from_csv(args.imu_csv, frame_id=cfg["imu_frame"],
                                      gyro_unit=cfg["gyro_unit"], accel_unit=cfg["accel_unit"],
                                      max_samples=cfg["max_imu_samples"])
        report, prepared = prepare_inputs(batch, imu, cfg)
        report["window"] = window
        # Keep raw packet/device/capture time references as well as filtered LIO input.
        np.savez_compressed(args.output / "inputs.npz", **prepared,
                            raw_device_times=batch.device_times, raw_capture_times=batch.capture_times,
                            raw_source_refs=batch.source_refs(np.ones(batch.ray_count, dtype=bool)))
    except (ValueError, TypeError, KeyError, OSError) as exc:
        report = dict(status="BLOCKED", block_reasons=[str(exc)], measurement_status="NOT_EVALUATED",
                      deskew_applied=False, claims={"lio_validated": False, "slam_validated": False})
    manifest = dict(schema_version="na70-lio-session-manifest-v1", argv=sys.argv[1:] if argv is None else argv,
                    evidence_kind=args.evidence_kind, platform=platform.platform(), python=platform.python_version(),
                    pcap_sha256=sha256_file(args.pcap) if args.pcap.is_file() else None,
                    imu_sha256=sha256_file(args.imu_csv) if args.imu_csv and args.imu_csv.is_file() else None,
                    config=cfg, config_sha256=sha256_file(args.config) if args.config.is_file() else None,
                    source_sha256={str(path.relative_to(ROOT)): sha256_file(path) for path in (
                        Path(__file__), ROOT / "packages/na70_core/na70_core/lio.py",
                        ROOT / "web/backend/decoder.py", ROOT / "web/backend/thin_wire.py")})
    report["evidence_kind"] = args.evidence_kind
    save_json(args.output / "manifest.json", manifest)
    save_json(args.output / "report.json", report)
    (args.output / "REPORT.md").write_text(
        f"# LIO 输入预检：{report['status']}\n\n类型：{args.evidence_kind}。"
        "本运行不执行运动补偿或 SLAM。\n\n"
        + "\n".join(f"- {reason}" for reason in report["block_reasons"]) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "block_reasons": report["block_reasons"],
                      "output": str(args.output)}, ensure_ascii=False))
    return 0 if report["status"] == "INPUT_CONTRACT_PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())

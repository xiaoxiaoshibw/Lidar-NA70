"""Assemble a NA70 scene from a PCAP and a traceable pose trajectory.

Poses must be in the same clock as the PCAP packet capture times.  The tool is
an assembly step only: it interpolates a supplied trajectory, applies the pose
at each point's packet time, voxel-downsamples, and writes provenance.

A ``--calibration`` file is required for IMU-frame poses and must contain both
``R_imu_lidar`` and ``t_imu_lidar``; a missing translation is not filled with
zero.  LiDAR-frame poses need no extrinsic.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "na70_core"))
sys.path.insert(0, str(ROOT / "web"))
from backend import decoder  # noqa: E402
from na70_core import scene  # noqa: E402

POSE_COLUMNS = ("t_s", "tx", "ty", "tz", "qx", "qy", "qz", "qw")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_poses(path):
    """Read ``t_s,tx,ty,tz,qx,qy,qz,qw`` rows (quaternion x,y,z,w)."""
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as source:
        rows = [row for row in csv.reader(source) if row and not row[0].strip().startswith("#")]
    if not rows:
        raise ValueError("empty pose CSV")
    try:
        float(rows[0][0])
        header = None
    except ValueError:
        header = [name.strip() for name in rows[0]]
        rows = rows[1:]
    if header is not None:
        missing = [name for name in POSE_COLUMNS if name not in header]
        if missing:
            raise ValueError(f"pose CSV header missing {','.join(missing)}")
        index = [header.index(name) for name in POSE_COLUMNS]
    else:
        if any(len(row) != len(POSE_COLUMNS) for row in rows):
            raise ValueError("pose CSV rows need eight columns: t_s,tx,ty,tz,qx,qy,qz,qw")
        index = list(range(len(POSE_COLUMNS)))
    data = np.array([[float(row[column]) for column in index] for row in rows], dtype=np.float64)
    if len(data) < 2:
        raise ValueError("at least two poses are required")
    if not np.isfinite(data).all():
        raise ValueError("pose CSV contains non-finite values")
    if np.any(np.diff(data[:, 0]) <= 0):
        raise ValueError("pose times must strictly increase")
    quaternions = data[:, [7, 4, 5, 6]]  # w,x,y,z
    norms = np.linalg.norm(quaternions, axis=1)
    if np.any(norms <= 0):
        raise ValueError("pose quaternions must be non-zero")
    return {"times": data[:, 0], "positions": data[:, 1:4],
            "quaternions": quaternions / norms[:, None]}


def load_calibration(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "T_imu_lidar" in data:
        matrix = np.asarray(data["T_imu_lidar"], dtype=np.float64)
    elif "R_imu_lidar" in data and "t_imu_lidar" in data:
        rotation = np.asarray(data["R_imu_lidar"], dtype=np.float64)
        translation = np.asarray(data["t_imu_lidar"], dtype=np.float64).reshape(3)
        matrix = np.eye(4)
        matrix[:3, :3] = rotation
        matrix[:3, 3] = translation
    else:
        raise ValueError("calibration needs T_imu_lidar or both R_imu_lidar and t_imu_lidar")
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError("calibration matrix must be a finite 4x4")
    return matrix, data


def build(pcap, poses_path, output, *, pose_frame="imu", calibration_path=None,
          install_direction=1, start_frame=0, max_frames=None, voxel_size=0.05,
          max_gap_s=0.2):
    poses = load_poses(poses_path)
    trajectory = scene.Trajectory(times=poses["times"], positions=poses["positions"],
                                  quaternions=poses["quaternions"], frame=pose_frame)
    calibration = None
    calibration_meta = None
    if pose_frame == "imu":
        if calibration_path is None:
            raise ValueError("--calibration is required for --pose-frame imu")
        calibration, calibration_meta = load_calibration(calibration_path)
    scan = decoder.scan_pcap(pcap)
    end = len(scan.frames) if max_frames is None else min(len(scan.frames), start_frame + max_frames)
    if not 0 <= start_frame < end:
        raise ValueError("frame range is empty")
    builder = scene.MapBuilder(trajectory, T_imu_lidar=calibration, voxel_size=voxel_size,
                               max_gap_s=max_gap_s)
    with Path(pcap).open("rb") as source:
        for frame_index in range(start_frame, end):
            frame = scan.frames[frame_index]
            points = decoder.decode_frame(pcap, frame, install_direction, source=source)
            packet_times = np.asarray(frame["packet_capture_times"], dtype=np.float64)
            point_times = np.repeat(packet_times, 200)
            if len(point_times) != len(points):
                raise ValueError(f"frame {frame_index}: packet/point count mismatch "
                                 f"({len(point_times)} vs {len(points)}); no time padding")
            builder.add_frame(points, point_times, frame_index=frame_index)
    result = builder.finish()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    map_path = output / "map.ply"
    result.save_ply(map_path)
    trajectory_path = output / "trajectory.csv"
    trajectory_lines = ["t_s,tx,ty,tz,qx,qy,qz,qw"]
    for time, position, quaternion in zip(poses["times"], poses["positions"], poses["quaternions"]):
        # Stored wxyz; the CSV contract is TUM-style xyzw.
        trajectory_lines.append(
            f"{time:.6f}," + ",".join(f"{value:.9f}" for value in position)
            + "," + ",".join(f"{quaternion[index]:.9f}" for index in (1, 2, 3, 0)))
    trajectory_path.write_text("\n".join(trajectory_lines) + "\n", encoding="utf-8")
    report = {
        "schema": "na70-scene-build-v1",
        "status": "BUILT" if result.stats["points"] else "EMPTY_MAP",
        "evidence_kind": "REAL_PCAP",
        "inputs": {
            "pcap": str(pcap), "pcap_sha256": sha256(pcap),
            "poses": str(poses_path), "poses_sha256": sha256(poses_path),
            "calibration": None if calibration_path is None else str(calibration_path),
            "calibration_sha256": None if calibration_path is None else sha256(calibration_path),
        },
        "trajectory": {
            "frame": pose_frame, "samples": int(len(poses["times"])),
            "start_s": float(poses["times"][0]), "end_s": float(poses["times"][-1]),
            "clock_assumption": "pose t_s shares the PCAP packet_capture_time clock",
        },
        "calibration_meta": calibration_meta,
        "build": {
            "install_direction": install_direction,
            "start_frame": start_frame, "frames_decoded": end - start_frame,
            "voxel_size_m": voxel_size, "max_gap_s": max_gap_s,
            "deskew": "POSE_INTERPOLATED_AT_PACKET_TIME",
        },
        "map": result.stats,
        "map_file": {"path": "map.ply", "sha256": sha256(map_path), "bytes": map_path.stat().st_size},
        "trajectory_file": {"path": "trajectory.csv", "sha256": sha256(trajectory_path),
                            "bytes": trajectory_path.stat().st_size},
        "claims": {"map_accuracy_validated": False, "pose_truth": False,
                   "hardware_sync_validated": False},
        "limitations": [
            "地图只反映输入位姿；位姿错误会直接进入地图",
            "逐点时间由包接收时间复制，不是已验证的逐点设备时间",
            "未做回环、全局优化或与独立控制点对账；准确度 NOT_EVALUATED",
        ],
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (output / "REPORT.md").write_text(
        "# NA70 场景构建\n\n"
        f"- 状态：**{report['status']}**\n"
        f"- 帧：{report['build']['frames_decoded']}，地图点：{result.stats['points']}，"
        f"体素：{result.stats['voxels']}\n"
        f"- 轨迹：{report['trajectory']['samples']} 个位姿，"
        f"{report['trajectory']['start_s']:.3f}–{report['trajectory']['end_s']:.3f} s"
        f"（trajectory.csv）\n"
        f"- 丢弃：{result.stats['dropped']}\n\n"
        "位姿与时间来自外部轨迹；本构建不验证准确度、同步或回环。\n",
        encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcap", required=True, type=Path)
    parser.add_argument("--poses", required=True, type=Path)
    parser.add_argument("--pose-frame", choices=("imu", "lidar"), default="imu")
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--install-direction", type=int, choices=(1, 2), default=1)
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--voxel-size", type=float, default=0.05)
    parser.add_argument("--max-gap-s", type=float, default=0.2)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = build(args.pcap, args.poses, args.output, pose_frame=args.pose_frame,
                       calibration_path=args.calibration, install_direction=args.install_direction,
                       start_frame=args.start_frame, max_frames=args.max_frames,
                       voxel_size=args.voxel_size, max_gap_s=args.max_gap_s)
    except (OSError, ValueError, TypeError, KeyError, decoder.CaptureError) as exc:
        print(json.dumps({"status": "INVALID_DATA", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": report["status"], "points": report["map"]["points"],
                      "output": str(args.output)}, ensure_ascii=False))
    return 0 if report["status"] == "BUILT" else 2


if __name__ == "__main__":
    raise SystemExit(main())
"""Run a headless GLIM CPU smoke on the author's PUBLIC_DATASET example."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--bag", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=30.0)
    args = parser.parse_args()
    if not math.isfinite(args.duration) or args.duration <= 0:
        parser.error("duration must be positive")
    args.output.mkdir(parents=True, exist_ok=False)
    cfg = args.output.resolve() / "config"
    shutil.copytree(args.workspace / "src/glim/config", cfg)
    root = (cfg / "config.json").read_text()
    for source, target in (("config_odometry_gpu", "config_odometry_cpu"),
                           ("config_sub_mapping_gpu", "config_sub_mapping_passthrough"),
                           ("config_global_mapping_gpu", "config_global_mapping_pose_graph")):
        if root.count(source) != 1:
            raise ValueError("unexpected upstream config layout")
        root = root.replace(source, target)
    (cfg / "config.json").write_text(root)
    sensors = (cfg / "config_sensors.json").read_text()
    sensors, count = re.subn(r'("T_lidar_imu"\s*:\s*)\[[^\]]*\]',
                            r'\1[-0.006, 0.012, -0.008, 0, 0, 0, 1]', sensors)
    if count != 1:
        raise ValueError("unexpected upstream extrinsic config layout")
    (cfg / "config_sensors.json").write_text(sensors)
    ros = (cfg / "config_ros.json").read_text()
    ros, count = re.subn(r'("extension_modules"\s*:\s*)\[[^\]]*\]', r'\1[]', ros)
    if count != 1:
        raise ValueError("unexpected upstream extension config layout")
    (cfg / "config_ros.json").write_text(ros)
    command = ["ros2", "run", "glim_ros", "glim_rosbag", str(args.bag.resolve()),
               "--ros-args", "-p", f"config_path:={cfg}", "-p", "auto_quit:=true",
               "-p", f"dump_path:={args.output.resolve() / 'dump'}",
               "-p", f"playback_duration:={args.duration}"]
    start = time.monotonic()
    with (args.output / "replay.log").open("w") as log:
        result = subprocess.run(["/usr/bin/time", "-v", "-o", str(args.output / "resources.txt"), *command],
                                stdout=log, stderr=subprocess.STDOUT)
    counts = {}
    for path in (args.output / "dump").glob("*.txt"):
        if path.name.startswith(("traj_", "odom_")):
            rows = [[float(value) for value in line.split()] for line in path.read_text().splitlines()
                    if line.strip() and not line.startswith("#")]
            if any(len(row) != 8 or not all(math.isfinite(value) for value in row) for row in rows):
                raise ValueError(f"invalid TUM trajectory: {path}")
            counts[path.name] = len(rows)
    versions = {}
    for repo in ("glim", "glim_ros2"):
        versions[repo] = subprocess.check_output(
            ["git", "-C", str(args.workspace / "src" / repo), "rev-parse", "HEAD"], text=True).strip()
    quality_warnings = [line for line in (args.output / "replay.log").read_text().splitlines()
                        if "not enough IMU" in line or "timestamp rewind" in line or "large time gap" in line]
    report = dict(status="PUBLIC_DATASET_SMOKE_COMPLETED" if result.returncode == 0 and any(counts.values())
                  else "FAILED", environment="WSL2_HUMBLE_X86_64", evidence_kind="PUBLIC_DATASET",
                  command=command, duration_requested_s=args.duration, elapsed_s=time.monotonic() - start,
                  exit_code=result.returncode, trajectory_rows=counts, versions=versions,
                  runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  configuration_sha256={path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                        for path in sorted(cfg.glob("*.json"))},
                  quality_warnings=quality_warnings,
                  input_quality_status="REQUIRES_REVIEW" if quality_warnings else "NOT_EVALUATED",
                  accuracy_status="NOT_EVALUATED", na70_validated=False, jetson_validated=False)
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return 0 if report["status"] == "PUBLIC_DATASET_SMOKE_COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

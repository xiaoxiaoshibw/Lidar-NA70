#!/usr/bin/env bash
# Build the detector and replay one PointCloud2 bag in WSL2.
# Usage: ./scripts/wsl2_build_and_replay.sh /path/to/bag [input_topic] [warmup_frames]
set -euo pipefail

bag_path="${1:-}"
input_topic="${2:-/inno_lidar_cloud}"
warmup_frames="${3:-100}"

if [[ -z "${bag_path}" ]]; then
  echo "usage: $0 /path/to/bag [input_topic] [warmup_frames]" >&2
  exit 2
fi
if [[ ! -e "${bag_path}" ]]; then
  echo "bag path does not exist: ${bag_path}" >&2
  exit 2
fi
bag_path="$(realpath -- "${bag_path}")"
if [[ ! -f "/opt/ros/humble/setup.bash" ]]; then
  echo "ROS2 Humble is not installed at /opt/ros/humble" >&2
  echo "Use an Ubuntu 22.04 WSL2 distro, then install/source ROS2 Humble." >&2
  exit 1
fi

workspace_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
source /opt/ros/humble/setup.bash
cd "${workspace_dir}"
colcon build --symlink-install --packages-select na70_motion_detector
source install/setup.bash

cleanup() {
  if [[ -n "${launch_pid:-}" ]] && kill -0 "${launch_pid}" 2>/dev/null; then
    kill "${launch_pid}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "Checking bag topics..."
ros2 bag info "${bag_path}"
echo "Starting detector; input=${input_topic}, warmup_frames=${warmup_frames}"
ros2 launch na70_motion_detector motion_detector.launch.py \
  "input_topic:=${input_topic}" "warmup_frames:=${warmup_frames}" &
launch_pid=$!
sleep 2
echo "Replaying bag (Ctrl-C stops replay and detector)..."
ros2 bag play "${bag_path}" --clock

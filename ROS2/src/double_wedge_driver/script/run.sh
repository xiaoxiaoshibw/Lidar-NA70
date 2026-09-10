#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
workspace_dir="$(cd "${script_dir}/../../.." && pwd)"

if [[ -f /opt/ros/humble/setup.bash ]]; then
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
else
  echo "ROS2 Humble was not found at /opt/ros/humble" >&2
  exit 1
fi

if [[ ! -f "${workspace_dir}/install/setup.bash" ]]; then
  echo "The ROS2 workspace is not built: ${workspace_dir}/install/setup.bash" >&2
  echo "Run: colcon build --symlink-install" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "${workspace_dir}/install/setup.bash"

if ! command -v gnome-terminal >/dev/null 2>&1; then
  echo "gnome-terminal is required by this convenience script" >&2
  exit 1
fi

gnome-terminal -- bash -lc \
  "source /opt/ros/humble/setup.bash && source '${workspace_dir}/install/setup.bash' && ros2 launch double_wedge_driver start_lidar.launch.py; exec bash"

sleep 1

gnome-terminal -- bash -lc \
  "source /opt/ros/humble/setup.bash && source '${workspace_dir}/install/setup.bash' && ros2 launch double_wedge_driver test_lidar.launch.py use_rviz:=true; exec bash"

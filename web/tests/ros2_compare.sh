#!/usr/bin/env bash
set -eo pipefail
WEB_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROS_SRC="$WEB_ROOT/../ROS2/src/double_wedge_driver"
BUILD_ROOT="${NA70_WEB_ROS_BUILD:-/tmp/na70-web-ros2-validation}"
source /opt/ros/humble/setup.bash
mkdir -p "$BUILD_ROOT"
cd "$BUILD_ROOT"
colcon build --base-paths "$ROS_SRC" --build-base build --install-base install --packages-select double_wedge_driver --cmake-args -DCMAKE_BUILD_TYPE=Release
source install/setup.bash
export ROS_DOMAIN_ID=174
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
python3 "$WEB_ROOT/tests/ros2_compare.py" --install "$BUILD_ROOT/install" --output "$WEB_ROOT/test-results"

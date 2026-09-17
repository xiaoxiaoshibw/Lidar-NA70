#!/usr/bin/env bash
source /opt/ros/humble/setup.bash
NA70_GLIM_PREFIX="$HOME/na70-glim-eval/prefix"
export LD_LIBRARY_PATH="$NA70_GLIM_PREFIX/usr/local/lib:$NA70_GLIM_PREFIX/usr/lib:$NA70_GLIM_PREFIX/usr/lib/x86_64-linux-gnu:$NA70_GLIM_PREFIX/opt/ros/humble/lib:/opt/ros/humble/lib:${LD_LIBRARY_PATH:-}"
export AMENT_PREFIX_PATH="$NA70_GLIM_PREFIX/opt/ros/humble:${AMENT_PREFIX_PATH:-}"
export PATH="$NA70_GLIM_PREFIX/opt/ros/humble/bin:$PATH"
if [[ "${1:-}" == "check" ]]; then
  ldd "$NA70_GLIM_PREFIX/opt/ros/humble/lib/glim_ros/glim_rosbag" | grep 'not found' || true
  find "$NA70_GLIM_PREFIX" -name 'libgtsam*.so*'
fi

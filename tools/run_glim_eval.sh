#!/usr/bin/env bash
set -e
source /mnt/d/Code/radar/tools/glim_eval_env.sh
exec "$NA70_GLIM_PREFIX/opt/ros/humble/lib/glim_ros/glim_rosbag" "$@"

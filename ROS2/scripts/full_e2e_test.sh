#!/usr/bin/env bash
# Full E2E: synthetic_cloud_publisher -> motion_detector_node -> has_motion
set -e
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file:///mnt/d/Code/radar/ROS2/config/cyclonedds_wsl.xml

SRC=/mnt/d/Code/radar/ROS2/src/na70_motion_detector

echo "=== starting motion_detector (warmup_frames=5) ==="
cd $SRC
python3 -m na70_motion_detector.motion_detector_node \
    --ros-args -p warmup_frames:=5 -p min_moving_points:=10 \
    > /tmp/motion_node.log 2>&1 &
MOTION_PID=$!

sleep 3

echo "=== starting synthetic publisher ==="
python3 -m na70_motion_detector.synthetic_cloud_publisher \
    --start-delay 1 --rate 5 --warmup 5 --moving-frames 3 --clear-frames 4 \
    > /tmp/synth_pub2.log 2>&1 &
SYNTH_PID=$!

echo "=== monitoring has_motion for 15s ==="
timeout 15 ros2 topic echo /motion_detector/has_motion --field data 2>/dev/null || true

wait $SYNTH_PID 2>/dev/null
sleep 2
kill $MOTION_PID 2>/dev/null || true

echo "=== motion_detector log ==="
cat /tmp/motion_node.log

#!/usr/bin/env bash
source /opt/ros/humble/setup.bash
export ROS_LOCALHOST_ONLY=0
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
ros2 run demo_nodes_py talker > /tmp/talker.log 2>&1 &
TPID=$!
sleep 3
timeout 6 ros2 run demo_nodes_py listener > /tmp/listener.log 2>&1
kill "$TPID" 2>/dev/null || true
echo "--- talker ---"
cat /tmp/talker.log
echo "--- listener ---"
cat /tmp/listener.log

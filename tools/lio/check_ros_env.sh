#!/usr/bin/env bash
source /opt/ros/humble/setup.bash
echo "domain=$ROS_DOMAIN_ID rmw=$RMW_IMPLEMENTATION local=$ROS_LOCALHOST_ONLY"
ros2 pkg list 2>/dev/null | grep -E 'demo_nodes|fast_lio|rmw_' | head -20

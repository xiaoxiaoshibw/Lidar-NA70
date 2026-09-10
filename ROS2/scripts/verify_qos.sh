#!/usr/bin/env bash
# Verify BEST_EFFORT large message delivery after Cyclone DDS buffer fix.
set -e
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file:///mnt/d/Code/radar/ROS2/config/cyclonedds_wsl.xml

echo "=== kernel buffer ==="
sysctl net.core.rmem_max net.core.rmem_default net.core.wmem_max

echo "=== cyclonedds config ==="
echo "CYCLONEDDS_URI=$CYCLONEDDS_URI"

echo "=== topic list (should see /be_* and /rel_*) ==="
ros2 topic list 2>/dev/null | grep -E '/(be|rel)_' || echo "(no probe topics found — is qos_probe running?)"

echo "=== delivery test (timeout 4s each, count bytes received) ==="
for t in /be_16 /be_1024 /be_12000 /be_131072 /rel_12000 /rel_131072; do
    bytes=$(timeout 4 ros2 topic echo "$t" --once --field data 2>/dev/null | wc -c)
    if [ "$bytes" -gt 0 ]; then
        echo "  PASS  $t  (${bytes} bytes)"
    else
        echo "  FAIL  $t  (0 bytes — timed out)"
    fi
done

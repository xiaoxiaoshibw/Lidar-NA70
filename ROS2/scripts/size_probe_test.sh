#!/usr/bin/env bash
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file:///mnt/d/Code/radar/ROS2/config/cyclonedds_wsl_peer.xml

rm -f /tmp/probe.log
timeout 60 python3 /mnt/d/Code/radar/ROS2/scripts/size_probe.py > /tmp/probe.log 2>&1 &
PID=$!
sleep 5

SIZES=(16 256 1024 4096 8192 12000 16384 32768 65536 131072)
for s in "${SIZES[@]}"; do
  out=$(timeout 3 ros2 topic echo --no-daemon --once --field data "/probe_${s}" 2>/dev/null)
  if printf '%s' "$out" | grep -q "x"; then
    echo "SIZE ${s}: RECEIVED"
  else
    echo "SIZE ${s}: DROPPED"
  fi
done

kill $PID 2>/dev/null
wait 2>/dev/null
echo "--- probe log ---"
head -8 /tmp/probe.log
#!/usr/bin/env bash
# usage: qos_probe_full.sh <cyclonedds-config-file>
CFG="$1"
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI="file://${CFG}"

rm -f /tmp/qosprobe.log
timeout 60 python3 /mnt/d/Code/radar/ROS2/scripts/qos_probe.py > /tmp/qosprobe.log 2>&1 &
PID=$!
sleep 5
for s in 16 1024 12000 131072; do
  ro=$(timeout 3 ros2 topic echo --no-daemon --once --field data --qos-reliability reliable "/rel_${s}" 2>/dev/null)
  bo=$(timeout 3 ros2 topic echo --no-daemon --once --field data --qos-reliability best_effort "/be_${s}" 2>/dev/null)
  if printf '%s' "$ro" | grep -q "x"; then relres=RECV; else relres=DROP; fi
  if printf '%s' "$bo" | grep -q "x"; then beres=RECV; else beres=DROP; fi
  echo "SIZE ${s}: REL=${relres} BE=${beres}"
done
kill $PID 2>/dev/null
wait 2>/dev/null
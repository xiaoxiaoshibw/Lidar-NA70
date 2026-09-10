#!/usr/bin/env bash
# usage: qos_probe_fast.sh <cyclonedds-config-file>
# Reports whether BEST_EFFORT String samples of 12000 and 131072 bytes are delivered.
CFG="$1"
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI="file://${CFG}"

rm -f /tmp/qosprobe.log
timeout 30 python3 /mnt/d/Code/radar/ROS2/scripts/qos_probe.py > /tmp/qosprobe.log 2>&1 &
PID=$!
sleep 5
for s in 12000 131072; do
  bo=$(timeout 3 ros2 topic echo --no-daemon --once --field data --qos-reliability best_effort "/be_${s}" 2>/dev/null)
  if printf '%s' "$bo" | grep -q "x"; then
    echo "${CFG##*/}: BEST_EFFORT ${s} -> RECEIVED"
  else
    echo "${CFG##*/}: BEST_EFFORT ${s} -> DROPPED"
  fi
done
kill $PID 2>/dev/null
wait 2>/dev/null
#!/usr/bin/env bash
# Minimal NA70 -> FAST-LIO smoke: no recorder, 5-frame package, CycloneDDS.
PKG="${1:?package directory}"
source /opt/ros/humble/setup.bash
source /home/radar/na70_lio_ws/install/setup.bash
export ROS_LOCALHOST_ONLY=0
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
cd /tmp
rm -f /tmp/na70_smoke.log
ros2 launch fast_lio mapping.launch.py config_file:=na70.yaml rviz:=false > /tmp/na70_smoke.log 2>&1 &
LPID=$!
sleep 8
python3 /mnt/d/Code/radar/tools/lio/replay_na70_package.py \
  --package "$PKG" --pace 0.2 --linger 3 --discovery-wait 3
RC=$?
sleep 5
FPID=$(pgrep -f fastlio_mapping | head -1 || true)
if [ -n "${FPID:-}" ]; then kill -INT "$FPID" 2>/dev/null || true; fi
for _ in $(seq 1 10); do kill -0 "$LPID" 2>/dev/null || break; sleep 1; done
kill -TERM "$LPID" 2>/dev/null || true
pkill -f fastlio_mapping 2>/dev/null || true
wait "$LPID" 2>/dev/null || true
echo "------ fastlio log ------"
cat /tmp/na70_smoke.log
exit "$RC"

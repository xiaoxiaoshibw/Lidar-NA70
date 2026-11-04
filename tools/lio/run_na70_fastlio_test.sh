#!/usr/bin/env bash
# Run patched FAST-LIO and the NA70 replay node in one shell for DDS discovery.
PKG="${1:?package directory}"
source /opt/ros/humble/setup.bash
source /home/radar/na70_lio_ws/install/setup.bash
export ROS_LOCALHOST_ONLY=0
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
cd /tmp
rm -f /tmp/na70_combined.log
ros2 launch fast_lio mapping.launch.py config_file:=na70.yaml rviz:=false > /tmp/na70_combined.log 2>&1 &
LPID=$!
sleep 12
python3 /mnt/d/Code/radar/tools/lio/record_na70_fastlio_output.py \
  --odom /tmp/na70_odom.csv --cloud /tmp/na70_cloud.ply --duration 30 > /tmp/na70_record.log 2>&1 &
RPID=$!
sleep 3
python3 /mnt/d/Code/radar/tools/lio/replay_na70_package.py \
  --package "$PKG" --pace 0.2 --linger 3 --discovery-wait 5
RC=$?
wait "$RPID" 2>/dev/null || true
sleep 2
kill -INT "$LPID" 2>/dev/null || true
FPID=$(pgrep -f fastlio_mapping | head -1 || true)
if [ -n "${FPID:-}" ]; then
  kill -INT "$FPID" 2>/dev/null || true
fi
for _ in $(seq 1 10); do
  kill -0 "$LPID" 2>/dev/null || break
  sleep 1
done
kill -TERM "$LPID" 2>/dev/null || true
pkill -f fastlio_mapping 2>/dev/null || true
wait "$LPID" 2>/dev/null || true
echo "------ recorder ------"
cat /tmp/na70_record.log 2>/dev/null || true
echo "------ fastlio log ------"
tail -80 /tmp/na70_combined.log
exit "$RC"

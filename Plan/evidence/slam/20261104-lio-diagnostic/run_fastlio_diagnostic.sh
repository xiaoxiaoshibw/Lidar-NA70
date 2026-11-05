#!/usr/bin/env bash
# WSL2/Humble diagnostic replay, original batched host timestamps retained.
root=/mnt/d/Code/radar
out="$root/Plan/evidence/slam/20261104-lio-diagnostic"
source /opt/ros/humble/setup.bash
source /home/radar/na70_lio_ws/install/setup.bash
export ROS_LOCALHOST_ONLY=0 RMW_IMPLEMENTATION=rmw_cyclonedds_cpp ROS_DOMAIN_ID=89 OMP_NUM_THREADS=2
cd "$out"
ros2 launch fast_lio mapping.launch.py config_file:=na70.yaml rviz:=false > fastlio.log 2>&1 &
launch_pid=$!
trap 'kill -INT "$launch_pid" 2>/dev/null || true' EXIT
sleep 12
python3 "$root/tools/lio/record_na70_fastlio_output.py" --odom trajectory.csv --cloud latest-cloud.ply --duration 43 > recorder.log 2>&1 &
rec_pid=$!
sleep 3
python3 "$root/tools/lio/replay_na70_package.py" --package "$out/lio-package-margin1s" --pace 0.2 --linger 3 --discovery-wait 5 > replay.log 2>&1
replay_status=$?
wait "$rec_pid"
record_status=$?
kill -INT "$launch_pid" 2>/dev/null || true
# A background shell may have inherited SIGINT ignored. Signal only this launch's
# mapping child so FAST-LIO can shut down without touching other ROS processes.
node_pid=$(pgrep -P "$launch_pid" -f fastlio_mapping | head -1 || true)
if [ -n "$node_pid" ]; then kill -INT "$node_pid" 2>/dev/null || true; fi
wait "$launch_pid" 2>/dev/null || true
trap - EXIT
printf 'replay_exit=%s recorder_exit=%s\n' "$replay_status" "$record_status" > exit-status.txt
exit "$replay_status"

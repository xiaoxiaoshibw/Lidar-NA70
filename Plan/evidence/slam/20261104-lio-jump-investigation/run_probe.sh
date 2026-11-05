#!/usr/bin/env bash
set -o pipefail
root=/mnt/d/Code/radar
out="$root/Plan/evidence/slam/20261104-lio-jump-investigation"
source /opt/ros/humble/setup.bash
export ROS_LOCALHOST_ONLY=0 RMW_IMPLEMENTATION=rmw_cyclonedds_cpp ROS_DOMAIN_ID=91 OMP_NUM_THREADS=2
python3 "$out/probe.py" > "$out/probe.json" 2> "$out/probe-error.log" &
probe_pid=$!
sleep 3
python3 "$root/tools/lio/replay_na70_package.py" --package "$root/Plan/evidence/slam/20261104-lio-diagnostic/lio-package-margin1s" --pace 0.2 --linger 3 --discovery-wait 5 > "$out/probe-replay.log" 2>&1
replay_status=$?
wait "$probe_pid"
probe_status=$?
printf 'replay_exit=%s probe_exit=%s\n' "$replay_status" "$probe_status" > "$out/probe-exit-status.txt"
exit "$((replay_status || probe_status))"

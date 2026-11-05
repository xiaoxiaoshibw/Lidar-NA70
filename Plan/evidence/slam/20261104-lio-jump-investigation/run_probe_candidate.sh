#!/usr/bin/env bash
set -o pipefail
root=/mnt/d/Code/radar
out="$root/Plan/evidence/slam/20261104-lio-jump-investigation"
source /opt/ros/humble/setup.bash
export ROS_LOCALHOST_ONLY=0 RMW_IMPLEMENTATION=rmw_cyclonedds_cpp ROS_DOMAIN_ID=92 OMP_NUM_THREADS=2
python3 "$out/probe.py" > "$out/probe-candidate.json" 2> "$out/probe-candidate-error.log" &
probe_pid=$!
sleep 3
start=$(date +%s%N)
python3 "$out/replay_candidate.py" --package "$root/Plan/evidence/slam/20261104-lio-diagnostic/lio-package-margin1s" --pace 0.2 --linger 3 --discovery-wait 5 > "$out/probe-candidate-replay.log" 2>&1
replay_status=$?
end=$(date +%s%N)
wait "$probe_pid"
probe_status=$?
printf 'replay_exit=%s probe_exit=%s replay_elapsed_ns=%s\n' "$replay_status" "$probe_status" "$((end-start))" > "$out/probe-candidate-exit-status.txt"
exit "$((replay_status || probe_status))"

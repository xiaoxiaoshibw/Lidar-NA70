#!/usr/bin/env bash
B=/mnt/d/Code/radar/ROS2
for cfg in cfg_multicast_true.xml cfg_shm_max.xml; do
  echo "========== TESTING ${cfg} =========="
  bash "${B}/scripts/qos_probe_fast.sh" "${B}/config/${cfg}"
done
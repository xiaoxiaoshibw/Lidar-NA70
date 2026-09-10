#!/usr/bin/env bash
# Persist the Cyclone DDS environment for ROS 2 on WSL2 mirrored networking.
#
# Mirrored mode drops Fast DDS multicast discovery. This appends to ~/.bashrc:
#   - RMW_IMPLEMENTATION=rmw_cyclonedds_cpp   (switch middleware to Cyclone DDS)
#   - CYCLONEDDS_URI=...                      (confine DDS to the lo interface)
#
# Run once (idempotent):
#   bash /mnt/d/Code/radar/ROS2/scripts/setup_dds_env.sh
#
# Open a NEW terminal afterwards (or `source ~/.bashrc`) so the env applies.
set -euo pipefail

if grep -q "RMW_IMPLEMENTATION=rmw_cyclonedds_cpp" ~/.bashrc 2>/dev/null; then
  echo "Already configured in ~/.bashrc"
  exit 0
fi

{
  echo
  echo "# --- ROS 2 DDS on WSL2 mirrored networking (loopback + Cyclone DDS) ---"
  echo 'export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp'
  echo 'export CYCLONEDDS_URI=file:///mnt/d/Code/radar/ROS2/config/cyclonedds_wsl.xml'
} >> ~/.bashrc

echo "Appended DDS env to ~/.bashrc"
tail -n 5 ~/.bashrc
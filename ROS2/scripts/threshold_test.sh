#!/bin/bash
cd /mnt/d/Code/radar/ROS2
for sz in 1400 1500 2048 4096 8192; do
    echo "--- ${sz}B ---"
    python3 scripts/raw_udp_test.py $sz 2>&1 | grep -E "RECV|receiver done"
done

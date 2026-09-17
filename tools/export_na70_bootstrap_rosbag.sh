#!/usr/bin/env bash
set -e
source /opt/ros/humble/setup.bash
python3 /mnt/d/Code/radar/tools/export_na70_rosbag.py /mnt/d/Code/radar/web/data/defcea4d-51db-4f7f-b226-c5ffac4805df "$HOME/na70-glim-eval/006-bag-bootstrap-snapshot" --reject-invalid-time --bootstrap 30 --bootstrap-snapshot

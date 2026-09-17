"""Prepare a CPU LiDAR-only GLIM config under the isolated user prefix."""
import json
from pathlib import Path
import re
import shutil

root=Path.home()/'na70-glim-eval'
source=root/'prefix/opt/ros/humble/share/glim/config'
target=root/'config-ct'
shutil.copytree(source,target,dirs_exist_ok=True)
def edit(name,section,changes):
    path=target/name
    text=re.sub(r'/\*.*?\*/','',path.read_text(),flags=re.S)
    text=re.sub(r'//[^\n]*','',text)
    cfg=json.loads(text);cfg[section].update(changes)
    path.write_text(json.dumps(cfg,indent=2))
edit('config.json','global',dict(config_odometry='config_odometry_ct.json',
                               config_sub_mapping='config_sub_mapping_passthrough.json',
                               config_global_mapping='config_global_mapping_pose_graph.json'))
edit('config_ros.json','glim_ros',dict(enable_local_mapping=True,enable_global_mapping=True,
                                    extension_modules=[],points_topic='/na70/points',imu_topic='/unused_imu',
                                    imu_frame_id='na70_lidar',lidar_frame_id='na70_lidar',base_frame_id='na70_lidar',publish_imu2lidar=False))
edit('config_sensors.json','sensors',dict(T_lidar_imu=[0,0,0,0,0,0,1],global_shutter_lidar=False,
                                        autoconf_perpoint_times=False,perpoint_relative_time=True,perpoint_time_scale=1.0))
edit('config_preprocess.json','preprocess',dict(distance_far_thresh=80.,downsample_resolution=.25))
edit('config_global_mapping_pose_graph.json','global_mapping',dict(enable_optimization=False,min_travel_dist=1e9))
print(target)

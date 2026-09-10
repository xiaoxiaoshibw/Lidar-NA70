from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    share = Path(get_package_share_directory("na70_motion_detector"))
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "input_topic",
                default_value="/inno_lidar_cloud",
                description="PointCloud2 topic to inspect",
            ),
            DeclareLaunchArgument(
                "moving_points_topic",
                default_value="/motion_detector/moving_points",
                description="Topic for points classified as changed",
            ),
            DeclareLaunchArgument(
                "has_motion_topic",
                default_value="/motion_detector/has_motion",
                description="Topic for the debounced motion state",
            ),
            DeclareLaunchArgument(
                "warmup_frames",
                default_value="100",
                description="Number of empty-scene frames used for background learning",
            ),
            Node(
                package="na70_motion_detector",
                executable="motion_detector_node",
                name="na70_motion_detector",
                output="screen",
                parameters=[
                    str(share / "config" / "motion_detector.yaml"),
                    {
                        "input_topic": LaunchConfiguration("input_topic"),
                        "moving_points_topic": LaunchConfiguration("moving_points_topic"),
                        "has_motion_topic": LaunchConfiguration("has_motion_topic"),
                        "warmup_frames": ParameterValue(
                            LaunchConfiguration("warmup_frames"), value_type=int
                        ),
                    },
                ],
            )
        ]
    )

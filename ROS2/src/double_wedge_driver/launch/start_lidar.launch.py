from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("udp_port", default_value="2368"),            # UDP监听端口
        DeclareLaunchArgument("frame_id", default_value="lidar_frame"),     # 坐标系名称
        DeclareLaunchArgument("lidar_topic", default_value="/lidar_data"),  # 激光雷达扫描包topic
        DeclareLaunchArgument("livox_topic", default_value="/livox_data"),  # Livox格式点云topic
        DeclareLaunchArgument("status_topic", default_value="/livox_status"),# 激光雷达状态topic
        Node(
            package="double_wedge_driver",
            executable="double_wedge_driver_lidar",
            name="double_wedge_driver_lidar",
            output="screen",
            parameters=[{
                "udp_port": ParameterValue(LaunchConfiguration("udp_port"), value_type=int),
                "frame_id": LaunchConfiguration("frame_id"),
                "lidar_topic": LaunchConfiguration("lidar_topic"),
                "livox_topic": LaunchConfiguration("livox_topic"),
                "status_topic": LaunchConfiguration("status_topic"),
            }],
        ),
    ])

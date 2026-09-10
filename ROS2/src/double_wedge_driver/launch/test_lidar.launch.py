from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    rviz_config = PathJoinSubstitution([
        FindPackageShare("double_wedge_driver"),
        "config",
        "inno_lidar_rviz.rviz",
    ])
    return LaunchDescription([
        DeclareLaunchArgument("use_rviz", default_value="false"),
        DeclareLaunchArgument("input_topic", default_value="/lidar_data"),
        DeclareLaunchArgument("output_topic", default_value="/inno_lidar_cloud"),
        DeclareLaunchArgument("frame_id", default_value="lidar_link"),
        Node(
            package="double_wedge_driver",
            executable="double_wedge_driver_test_lidar",
            name="double_wedge_driver_test_lidar",
            output="screen",
            parameters=[{
                "input_topic": LaunchConfiguration("input_topic"),
                "output_topic": LaunchConfiguration("output_topic"),
                "frame_id": LaunchConfiguration("frame_id"),
            }],
        ),
        Node(
            package="rviz2",
            executable="rviz2",
            name="rviz2",
            output="screen",
            arguments=["-d", rviz_config],
            condition=IfCondition(LaunchConfiguration("use_rviz")),
        ),
    ])

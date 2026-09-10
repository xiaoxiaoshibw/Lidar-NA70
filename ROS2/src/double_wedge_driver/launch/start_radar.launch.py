from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("serial_port", default_value="/dev/ttyUSB0"),       # 串口设备
        DeclareLaunchArgument("baud_rate", default_value="57600"),                # 波特率
        DeclareLaunchArgument("blind_area", default_value="0.5"),                 # 盲区距离(米)
        DeclareLaunchArgument("hight_air_mode", default_value="false"),           # 高空模式
        DeclareLaunchArgument("frame_id", default_value="map"),                   # 坐标系名称
        DeclareLaunchArgument("radar_topic", default_value="/radar_data"),        # 点云数据topic
        DeclareLaunchArgument("radar_message_topic", default_value="/radar_msg"), # 雷达消息topic
        DeclareLaunchArgument("radar_status_topic", default_value="/radar_status"),# 雷达状态topic
        Node(
            package="double_wedge_driver",
            executable="double_wedge_driver_radar",
            name="double_wedge_driver_radar",
            output="screen",
            parameters=[{
                "radar_serial_port": LaunchConfiguration("serial_port"),
                "radar_baud_rate": ParameterValue(LaunchConfiguration("baud_rate"), value_type=int),
                "blind_area": ParameterValue(LaunchConfiguration("blind_area"), value_type=float),
                "hight_air_mode": ParameterValue(LaunchConfiguration("hight_air_mode"), value_type=bool),
                "frame_id": LaunchConfiguration("frame_id"),
                "radar_topic": LaunchConfiguration("radar_topic"),
                "radar_message_topic": LaunchConfiguration("radar_message_topic"),
                "radar_status_topic": LaunchConfiguration("radar_status_topic"),
            }],
        ),
    ])

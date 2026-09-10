# double_wedge_driver ROS2

这是 `inno_double_wedge_driver_ws` 的 ROS2 Humble 迁移版本，目标平台为 Jetson Orin Nano（Ubuntu 22.04 / ARM64）。迁移保留原有 UDP 激光雷达协议、串口毫米波雷达协议、点云坐标变换和自定义消息字段。

## 依赖

- ROS2 Humble：`rclcpp`、`std_msgs`、`sensor_msgs`、`rosidl_default_generators`
- `pcl_conversions`、PCL（`libpcl-all-dev`）、Boost.Asio、Eigen3（`eigen`）
- 毫米波雷达使用 Linux `termios`，不再依赖 ROS1 `serial` 包
- 未使用 `pcl_ros`

## 编译

```bash
cd /path/to/ROS2
source /opt/ros/humble/setup.bash
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install --packages-select double_wedge_driver
source install/setup.bash
```

## 启动

激光雷达默认监听 UDP `2368`：

```bash
ros2 launch double_wedge_driver start_lidar.launch.py
```

毫米波雷达：

```bash
ros2 launch double_wedge_driver start_radar.launch.py \
  serial_port:=/dev/ttyUSB0 baud_rate:=57600
```

合并激光点云并可选启动 RViz2：

```bash
ros2 launch double_wedge_driver test_lidar.launch.py use_rviz:=true
```

也可以使用 `src/double_wedge_driver/script/run.sh` 同时启动激光节点和 RViz2 测试节点。

## 默认话题

| 类型 | 话题 | 消息类型 |
| --- | --- | --- |
| 激光扫描包 | `/lidar_data` | `LidarScanBundle`（私有，内含 `PointCloud2[]`） |
| Livox 兼容点云 | `/livox_data` | `CustomMsg`（私有） |
| 激光状态 | `/livox_status` | `LidarStatus`（私有） |
| 合并激光点云 | `/inno_lidar_cloud` | `sensor_msgs/PointCloud2` |
| 毫米波点云 | `/radar_data` | `sensor_msgs/PointCloud2` |
| 毫米波目标消息 | `/radar_msg` | `RadarMsg`（私有） |
| 毫米波状态 | `/radar_status` | `RadarStatus`（私有） |

注意：主激光驱动只发布私有消息，RViz2 无法直接显示 `/lidar_data`。
当前唯一的标准激光点云是由测试/合并节点产出的 `/inno_lidar_cloud`。
详见 [`点云接口与坐标系现状.md`](点云接口与坐标系现状.md)。

## 坐标系现状

| 节点 | 默认 `frame_id` |
| --- | --- |
| 激光驱动 | `lidar_frame` |
| 合并节点 | `lidar_link` |
| 毫米波驱动 | `map` |

工程当前**不发布任何 TF**。合并节点只改写 `frame_id` 而未做坐标变换
（该行为继承自 ROS1）。`/radar_data` 的 `map` 与 RViz2 的 `Fixed Frame: lidar_link`
之间无 TF 链路，同时显示时预期报 TF 错误。三者的真实坐标关系待实测确认，
在获得依据前不添加零位姿 static TF。详见 [`点云接口与坐标系现状.md`](点云接口与坐标系现状.md)。

## 关键参数

激光节点：`udp_port`、`frame_id`、`lidar_topic`、`livox_topic`、`status_topic`。

毫米波节点：`radar_serial_port`、`radar_baud_rate`、`blind_area`、`hight_air_mode`、`frame_id` 以及三个话题参数。串口设备需要运行账户具备 `dialout` 权限。

## 检查

```bash
ros2 pkg executables double_wedge_driver
ros2 interface show double_wedge_driver/msg/LidarScanBundle
ros2 topic list
ros2 topic hz /inno_lidar_cloud
ros2 topic echo /livox_status --once
```

当前迁移结果已完成工程结构、消息、节点、Launch、RViz2 和运行脚本的代码迁移；Jetson 原生编译、真实雷达收数、RViz2 显示和连续运行测试需要在目标设备及真实硬件可用后执行。

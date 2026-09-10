# na70_motion_detector

固定NA70的最小运动存在检测节点。启动后先用空场景学习体素背景，再检测不属于背景的新点；候选点连续达到阈值后发布运动状态。

## Topics

| 方向 | Topic | 类型 |
|---|---|---|
| 输入 | `/inno_lidar_cloud` | `sensor_msgs/msg/PointCloud2` |
| 输出 | `/motion_detector/has_motion` | `std_msgs/msg/Bool` |
| 输出 | `/motion_detector/moving_points` | `sensor_msgs/msg/PointCloud2` |
| 输出 | `/motion_detector/moving_count` | `std_msgs/msg/UInt32` |
| 输出 | `/motion_detector/moving_ratio` | `std_msgs/msg/Float32` |

## 本机先验证算法（不需要 ROS2）

在已安装 NumPy 的 Python 环境中，从该包目录执行：

```bash
python -m na70_motion_detector.offline_demo
```

命令默认运行一个空场景加运动目标的合成冒烟测试。输出中连续两帧
`has_motion=1` 即表示检测器状态已经触发。也可以传入保存的帧数组：

```bash
python -m na70_motion_detector.offline_demo frames.npy
python -m na70_motion_detector.offline_demo frames.npz
```

数组要求为 `[帧数, 点数, 通道数]`，前3列是 `x,y,z`，第4列（如果有）是
`intensity`。由于普通 NumPy 数组要求每帧点数相同，变长点云请先补 NaN；
检测器会自动忽略 NaN。安装本 ROS2 包后也可以使用
`motion_detector_offline` 命令。

## 在 WSL2 的 ROS2 Humble 中构建和回放

以下命令在 WSL2 Ubuntu 22.04、ROS2 Humble 环境执行。工作区通过
`/mnt/d/Code/radar/ROS2` 访问 Windows 文件：

先检查 WSL2 发行版和 ROS2 是否存在（Windows PowerShell 中执行
`wsl --list --verbose`，WSL2 中执行 `command -v ros2`）。本项目固定使用
Ubuntu 22.04 + ROS2 Humble，与 Jetson 目标环境保持一致；Ubuntu 24.04 不作为
本项目的测试环境，也不切换到 Jazzy。若当前发行版不是 Ubuntu 22.04，应先
重装/新建 Ubuntu 22.04 WSL2 发行版，再按 Humble 官方步骤安装 ROS2。

```bash
cd /mnt/d/Code/radar/ROS2
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-select na70_motion_detector
source install/setup.bash
```

先启动检测节点（录包中的输入话题必须是 `sensor_msgs/msg/PointCloud2`）：

```bash
ros2 launch na70_motion_detector motion_detector.launch.py \
  input_topic:=/inno_lidar_cloud warmup_frames:=100
```

用于ROS2冒烟测试的合成点云发布器会默认等待3秒，让订阅关系先完成发现：

```bash
ros2 run na70_motion_detector synthetic_cloud_publisher \
  --warmup 5 --start-delay 3
```

WSL2发现较慢时可延长运动段，保证节点有足够时间建立DDS连接：

```bash
ros2 run na70_motion_detector synthetic_cloud_publisher \
  --warmup 10 --moving-frames 40 --clear-frames 10 \
  --start-delay 10 --rate 5
```

在另一个 WSL2 终端回放 rosbag。回放前约100帧必须是无人空场景，建议先确认
包内话题和类型：

```bash
source /opt/ros/humble/setup.bash
ros2 bag info /path/to/bag
ros2 bag play /path/to/bag --clock
```

如果录包中的点云话题名称不同，可通过 launch 参数切换：

```bash
ros2 launch na70_motion_detector motion_detector.launch.py \
  input_topic:=/your/pointcloud_topic
```

观察检测结果：

```bash
ros2 topic echo /motion_detector/has_motion
ros2 topic echo /motion_detector/moving_count
ros2 topic hz /motion_detector/moving_points
```

也可以从工作区直接使用脚本完成构建、检查 bag、启动节点和回放：

```bash
cd /mnt/d/Code/radar/ROS2/src/na70_motion_detector
chmod +x scripts/wsl2_build_and_replay.sh
./scripts/wsl2_build_and_replay.sh /path/to/bag /inno_lidar_cloud 100
```

脚本只负责本机 WSL2 验证，不连接真实雷达；bag 必须已经在 Orin 或其他
采集端录好并复制到 WSL2 可访问的位置。

想重新学习背景时调用服务，然后重新播放一段空场景开头的 bag：

```bash
ros2 service call /motion_detector/reset_background std_srvs/srv/Trigger '{}'
```

## 在 WSL2 的最小验收标准

1. `colcon build` 成功且 `ros2 launch` 能启动节点。
2. 空场景 warmup 完成后，`has_motion` 保持 `False`，`moving_count` 接近0。
3. 人或其他物体进入场景后，连续两帧达到阈值，`has_motion` 变为 `True`。
4. 目标离开后连续 `clear_frames` 个低于阈值的帧，状态恢复 `False`。
5. `/motion_detector/moving_points` 能在 RViz2 中显示候选点。

## Build and run on Jetson

```bash
cd /path/to/ROS2
source /opt/ros/humble/setup.bash
colcon build --packages-select na70_motion_detector
source install/setup.bash
ros2 launch na70_motion_detector motion_detector.launch.py
```

启动后的前100帧必须保持场景尽量无人。需要重新学习背景时：

```bash
ros2 service call /motion_detector/reset_background std_srvs/srv/Trigger '{}'
```

观察结果：

```bash
ros2 topic echo /motion_detector/has_motion
ros2 topic echo /motion_detector/moving_count
```

RViz添加PointCloud2并选择 `/motion_detector/moving_points`，用于判断候选点是否落在真实移动目标上。

## Current limitation

这一版判断的是“相对启动背景出现了持续变化”，不是完整的物理运动估计。目标在背景学习期间出现会被吸收；目标后来进入并长时间静止时仍可能继续被判为变化。下一阶段再通过动态背景、聚类和跟踪解决。

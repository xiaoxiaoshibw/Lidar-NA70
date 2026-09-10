# 06 Luna本机与WSL2执行计划

负责人：Luna  
状态：IN_PROGRESS  
目标：先在本机证明“有没有运动候选物体”，再通过WSL2跑通ROS2，不进入聚类、跟踪和分类。

## 1. Luna编码范围

允许修改：

- `ROS2/src/na70_motion_detector/**`；
- 与本机离线演示和WSL2测试直接相关的新脚本；
- 本文件对应的状态和测试记录。

禁止修改：

- NA70驱动协议解析和硬件接口；
- MapMOS代码、环境或权重；
- 聚类、跟踪、PointNet和PointPillars；
- 未经验证就把WSL2结果写成Jetson或真机通过。

## 2. 第一阶段：Windows本机算法验证

交付物：

1. ROS无关的 `VoxelMotionDetector`；
2. 标准库可运行的自动测试；
3. 离线演示脚本，合成静态背景、移动目标和清空场景；
4. 每帧输出 `ready/has_motion/moving_count/moving_ratio`；
5. 参数非法、空点、NaN/Inf和背景重置测试。

验收：

```text
背景学习：NOT_READY
空场景：CLEAR
移动目标连续出现：MOTION
目标消失并达到clear_frames：CLEAR
```

## 3. 第二阶段：WSL2 ROS2冒烟测试

本机目标环境：

```text
WSL版本：2
发行版：Ubuntu-22.04
安装位置：D:\WSL\Ubuntu-22.04
ROS2：Humble
状态：原Ubuntu-24.04已删除，22.04待用户手动安装
```

安装完成后，推荐在WSL ext4中建立构建目录：

```bash
mkdir -p ~/na70_ws/src
ln -s /mnt/d/Code/radar/ROS2/src/na70_motion_detector ~/na70_ws/src/na70_motion_detector
cd ~/na70_ws
source /opt/ros/jazzy/setup.bash
colcon build --packages-select na70_motion_detector
source install/setup.bash
ros2 launch na70_motion_detector motion_detector.launch.py
```

Luna需要补充合成PointCloud2发布器或ROS2集成测试，使背景学习后自动注入运动点，并验证：

```text
/motion_detector/has_motion
/motion_detector/moving_count
/motion_detector/moving_ratio
/motion_detector/moving_points
/motion_detector/reset_background
```

## 4. 第三阶段：WSL2 PCAP全链路

在依赖齐全后构建：

```bash
colcon build --packages-select double_wedge_driver na70_motion_detector
```

启动顺序：

1. `double_wedge_driver_lidar`监听UDP 2368；
2. `double_wedge_driver_test_lidar`生成 `/inno_lidar_cloud`；
3. `na70_motion_detector`订阅标准点云；
4. `tools/replay_pcap.py`向WSL内的127.0.0.1:2368回放PCAP；
5. 记录Topic频率、运动状态、候选点数和日志。

PCAP如果没有“空背景→明确人员进入”的真值，只能验证链路、稳定性和误报，不能据此给出检出率。

## 5. 第四阶段：Humble兼容性

Jazzy通过后有两条路线：

1. 快速开发继续使用现有WSL2/Jazzy，最终直接到Jetson/Humble验收；
2. 若Jetson兼容问题频繁，再新增Ubuntu 22.04 WSL2实例，安装Humble做同版本预验证。

默认先走路线1，避免为了简单节点提前增加第二套WSL维护成本。

## 6. 完成定义

Luna本轮完成必须同时满足：

- Windows自动测试通过；
- 离线演示可复现；
- WSL2构建/运行脚本明确；
- 若环境允许，Jazzy ROS2冒烟测试通过并有日志；
- 所有结论标明 `WINDOWS_OFFLINE`、`WSL2_JAZZY`或`JETSON_HUMBLE`证据层级；
- 未通过项保留为未通过，不用模拟结果代替。

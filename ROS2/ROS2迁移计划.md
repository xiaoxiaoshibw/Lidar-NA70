# inno_double_wedge_driver ROS2 迁移计划

> 本文件在迁移工作区中作为目标环境、实施范围和验收基线使用，不是要求在 Windows 本机原样执行的命令清单。当前已落地内容与尚未完成的 Jetson 实机验收见《迁移进度.md》。

## 1. 迁移目标

将当前 ROS1 Catkin 工程迁移到 ROS2，并在 NVIDIA Jetson Orin Nano 上稳定运行。

目标运行环境：

- 硬件：NVIDIA Jetson Orin Nano
- 系统：JetPack 6 / Ubuntu 22.04 / ARM64（aarch64）
- ROS：ROS2 Humble
- 构建工具：`colcon` + `ament_cmake`
- C++ 标准：C++17
- 激光雷达通信：UDP，默认端口 `2368`
- 毫米波雷达通信：串口，设备名称通过 ROS2 参数配置

迁移时保留现有雷达协议解析、点云处理和业务逻辑，只替换 ROS1 接口、构建系统及启动配置。

## 2. 预计工期

| 阶段 | 预计时间 | 交付结果 |
| --- | ---: | --- |
| 工程结构与依赖迁移 | 0.5 天 | ROS2 包可被 `colcon` 识别 |
| 自定义消息迁移 | 0.5 天 | 消息接口生成成功 |
| 激光雷达节点迁移 | 0.5～1 天 | UDP 数据可转换并发布为 ROS2 话题 |
| 毫米波雷达节点迁移 | 0.5～1 天 | 串口数据可解析并发布为 ROS2 话题 |
| 测试节点、Launch 和 RViz 迁移 | 0.5 天 | 可一键启动并显示点云 |
| Orin Nano 实机联调 | 1～2 天 | 两类雷达连续稳定运行 |

正常情况下总工期为 **3～5 天**。若缺少真实雷达，只能先完成编译、静态检查和模拟数据验证，实机验收时间另计。

## 3. 迁移范围

### 3.1 需要改造的文件

- `src/double_wedge_driver/CMakeLists.txt`
- `src/double_wedge_driver/package.xml`
- `src/double_wedge_driver/src/inno_device_lidar_node.cpp`
- `src/double_wedge_driver/src/inno_device_radar_node.cpp`
- `src/double_wedge_driver/src/test_lidar_driver.cpp`
- `src/double_wedge_driver/launch/start_lidar.launch.py`
- `src/double_wedge_driver/launch/start_radar.launch.py`
- `src/double_wedge_driver/launch/test_lidar.launch.py`
- `src/double_wedge_driver/script/run.sh`
- `src/double_wedge_driver/config/inno_lidar_rviz.rviz`

### 3.2 原则上保留的内容

- UDP 数据接收与雷达数据包解析逻辑
- 串口帧解析、校验和计算和目标数据解析逻辑
- 点云滤波和坐标变换算法
- 雷达状态码及距离、强度等字段定义
- 现有自定义 `.msg` 文件的业务字段

## 4. 分阶段实施任务

### 阶段一：建立 ROS2 工程结构

1. 备份当前 ROS1 可用版本或创建独立迁移分支。
2. 将 `CMakeLists.txt` 从 Catkin 改为 `ament_cmake`。
3. 将构建命令从 `catkin_make` 改为 `colcon build`。
4. 在 `package.xml` 中补齐并更新以下依赖：
   - `ament_cmake`
   - `rclcpp`
   - `std_msgs`
   - `sensor_msgs`
   - `pcl_conversions`
   - `libpcl-all-dev` / `libpcl-all`（PCL 头文件与运行期共享库）
   - `eigen`
   - `boost`
   - `rosidl_default_generators`
   - `rosidl_default_runtime`
   - 串口不再引入第三方依赖，改用 Linux 原生 `termios`

   > 原计划中的 `pcl_ros` 经核对当前源码后确认未被使用（三个节点只包含
   > `pcl_conversions/pcl_conversions.h` 与 `pcl/*` 头文件），因此不再列为必需依赖，
   > 避免为对齐文字而引入无用依赖。
5. 为三个 C++ 可执行节点配置安装规则。
6. 安装 `launch`、`config` 和 RViz 配置文件。

验收标准：

- `colcon list` 能识别 `double_wedge_driver`。
- CMake 配置阶段无 Catkin 相关错误。
- 安装目录中可以找到节点、消息接口和启动文件。

### 阶段二：迁移自定义消息

1. 使用 `rosidl_generate_interfaces` 生成以下消息：
   - `LidarScanBundle.msg`
   - `CustomPoint.msg`
   - `CustomMsg.msg`
   - `LidarStatus.msg`
   - `RadarStatus.msg`
   - `RadarMsg.msg`
2. 检查消息内引用的 `std_msgs`、`sensor_msgs` 类型。
3. 调整 C++ 中自动生成头文件的名称及命名空间。
4. 确保节点目标正确链接 ROS2 类型支持。

验收标准：

- `colcon build --packages-select double_wedge_driver` 能完成消息生成。
- `ros2 interface show double_wedge_driver/msg/LidarScanBundle` 能正确显示字段。
- 六种自定义消息均可被 ROS2 查询。

### 阶段三：迁移激光雷达节点

1. 将 ROS1 节点初始化改为 `rclcpp::init`。
2. 将节点封装为继承 `rclcpp::Node` 的类。
3. 将 ROS1 Publisher、Timer、Time 和日志接口替换为 ROS2 接口。
4. 为 UDP 监听端口、坐标系名称和话题名称增加 ROS2 参数。
5. 保留 Boost.Asio UDP 接收逻辑，检查线程退出和资源释放流程。
6. 将点云消息时间戳改为 ROS2 时间类型。
7. 明确点云 Publisher 的 QoS；传感器数据优先使用 `SensorDataQoS`。
8. 验证 UDP 异常、雷达断连和节点退出时不会产生死锁或崩溃。

验收标准：

- 节点可独立启动和退出。
- UDP `2368` 端口可以正常监听。
- 收到雷达数据后持续发布点云和状态话题。
- `frame_id`、时间戳和点数量正确。

### 阶段四：迁移毫米波雷达节点

1. 将节点初始化、参数、发布器和日志接口改为 ROS2。
2. 将以下配置改为 ROS2 参数：
   - `radar_serial_port`
   - `radar_baud_rate`
   - `blind_area`
   - `hight_air_mode`
3. 确认 ROS2 Humble 下串口依赖能够在 ARM64 编译。
4. 保留现有字节流和帧协议解析逻辑。
5. 检查数据长度、目标数量和数组边界，避免异常数据造成越界读取。
6. 增加串口打开失败、雷达断开和重新连接的日志或恢复策略。
7. 根据 Orin Nano 实际设备修改串口参数，避免固定使用 `/dev/ttyS9`。

验收标准：

- 指定正确串口后节点可以打开设备。
- 状态帧和目标帧均能正常解析。
- 点云、雷达消息和状态消息持续发布。
- 拔插串口或雷达断电时，节点能够明确报告错误。

### 阶段五：迁移测试节点、Launch 和 RViz

1. 将 `test_lidar_driver.cpp` 的订阅器、日志和节点生命周期迁移到 ROS2。
2. 将三个 ROS1 Launch 文件改为 ROS2 Python Launch 文件：
   - `start_lidar.launch.py`
   - `start_radar.launch.py`
   - `test_lidar.launch.py`
3. 在 Launch 中暴露网口、UDP 端口、串口和波特率参数。
4. 更新 RViz 配置中的话题和固定坐标系。
5. 更新 `run.sh`，改用 ROS2 环境和 `ros2 launch`。

验收标准：

- `ros2 launch` 可以启动每个节点。
- `ros2 topic list` 能看到预期话题。
- `ros2 topic hz` 显示稳定发布频率。
- RViz2 可以显示点云，且坐标系无持续报错。

### 阶段六：Orin Nano 部署与实机联调

1. 确认设备运行 JetPack 6 和 Ubuntu 22.04。
2. 安装 ROS2 Humble、PCL、Boost、Eigen、串口依赖和构建工具。
3. 在 Orin Nano 本机执行 ARM64 原生编译。
4. 配置雷达网口静态地址，例如 `192.168.0.3/24`。
5. 确认 UDP `2368` 未被防火墙阻止或被其他进程占用。
6. 使用 `ls -l /dev/tty*` 确认毫米波雷达对应的实际串口。
7. 将运行账户加入 `dialout` 用户组并重新登录。
8. 分别进行激光雷达、毫米波雷达和双雷达同时运行测试。
9. 进行不少于 2 小时的连续运行测试，记录 CPU、内存、丢包、发布频率和异常日志。

验收标准：

- ARM64 环境编译无错误。
- 两类雷达均能被正确识别并发布数据。
- RViz2 点云显示连续，无明显跳变或长期中断。
- 连续运行期间无崩溃、持续内存增长或线程异常退出。

## 5. 推荐实施顺序

为减少实机调试成本，按以下顺序执行：

1. 在 Ubuntu 22.04 + ROS2 Humble 环境完成工程和消息迁移。
2. 使用模拟数据或录制数据验证协议解析和话题发布。
3. 在 Orin Nano 上完成 ARM64 原生编译。
4. 先联调 UDP 激光雷达。
5. 再联调串口毫米波雷达。
6. 最后进行双雷达并行运行和稳定性测试。

## 6. 编译与检查命令

```bash
cd <ros2_workspace>
source /opt/ros/humble/setup.bash
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install --packages-select double_wedge_driver
source install/setup.bash
ros2 interface list | grep double_wedge_driver
ros2 pkg executables double_wedge_driver
```

启动和话题检查示例：

```bash
ros2 launch double_wedge_driver start_lidar.launch.py
ros2 topic list
ros2 topic hz <point_cloud_topic>
ros2 topic echo <status_topic> --once
```

## 7. 主要风险与处理方案

| 风险 | 影响 | 处理方案 |
| --- | --- | --- |
| ROS1 与 ROS2 API 差异 | 节点无法编译 | 逐节点迁移，先完成最小发布/订阅闭环 |
| 自定义消息依赖未声明完整 | 消息生成失败 | 在 CMake 和 `package.xml` 中同步声明依赖 |
| ROS2 串口库兼容性不足 | 毫米波雷达节点无法编译或打开设备 | 优先验证 ARM64 软件包，必要时替换为 Boost.Asio 或原生 termios |
| Orin Nano 串口名称不同 | 节点启动但无法接收数据 | 使用参数配置串口，不在源码中写死设备名 |
| QoS 不匹配 | RViz2 或订阅节点收不到点云 | 统一使用适合传感器数据的 QoS 配置 |
| UDP 网口或防火墙配置错误 | 激光雷达无数据 | 检查静态 IP、端口占用、抓包和防火墙规则 |
| 原始数据长度异常 | 越界访问或节点崩溃 | 在每次字段读取前校验包长与目标数量 |
| 时间戳和坐标系错误 | 点云显示跳变或 TF 报错 | 明确时间来源、`frame_id` 及 TF 关系 |

## 8. 最终交付物

- 可在 ROS2 Humble 下构建的 `double_wedge_driver` 包
- ROS2 版本的三个 C++ 节点
- 六种可用的 ROS2 自定义消息
- ROS2 Python Launch 文件
- 更新后的 RViz2 配置
- Orin Nano 安装、编译和运行说明
- 模拟数据测试记录
- 实机联调和连续运行测试记录

## 9. 完成定义

满足以下条件后，迁移任务才视为完成：

- 项目不再依赖 ROS1 Catkin 接口。
- 在 Jetson Orin Nano 的 ROS2 Humble 环境中可重复完成干净编译。
- 激光雷达和毫米波雷达均可通过参数配置并正常启动。
- 点云、状态和雷达消息内容正确，发布频率稳定。
- RViz2 能正常显示点云。
- 节点能够正常退出，并能对断网、串口异常和错误数据给出明确日志。
- 完成双雷达并行和连续运行测试。

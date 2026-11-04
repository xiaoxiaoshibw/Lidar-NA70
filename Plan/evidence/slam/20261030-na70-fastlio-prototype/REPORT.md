# NA70 → FAST-LIO 接入原型（WSL2，诊断）

- 目标：验证 `tools/export_na70_lio_scans.py` 的包能被 ROS2 发布，并由固定版本 FAST-LIO 的 NA70 预处理消费。
- 环境：WSL2 Ubuntu 22.04 / ROS2 Humble；FAST-LIO `cc3c9e8`（`/home/radar/na70_lio_ws`）。
- 补丁：`ROS2/src/na70_lio/patches/fast_lio_na70_handler.patch`，由 `tools/lio/apply_fast_lio_na70_patch.py` 幂等应用。
- 输入：
  - 5 帧包 `20261030-lio-package-smoke`（100,000 点）；
  - 50 帧包 `20261030-lio-package-5s`（1,000,000 点、约 5 s，从会话第 10 帧起，IMU 已覆盖扫描区间）。

## 已完成

- FAST-LIO 增加 `NA70` 点类型与 `na70_handler`：直接读取 PointCloud2 的 `x,y,z,intensity,time`（秒），`curvature = time*1000`（ms），不使用/伪造 ring。
- 补丁在固定源码上编译成功（`colcon build --packages-select fast_lio`）。
- 新增 ROS2 回放脚本 `tools/lio/replay_na70_package.py`：扫描 header 取帧起点，逐点 `time` 为相对帧起点秒；IMU 转 rad/s，按时间先于扫描发布；等待订阅匹配。
- 新增输出记录脚本 `tools/lio/record_na70_fastlio_output.py` 与运行脚本 `tools/lio/run_na70_fastlio_test.sh` / `run_na70_fastlio_smoke.sh`。

## 实际运行结果

### 50 帧（5 s）诊断运行

`fastlio_run_182615.log`：

- FAST-LIO 打印 `p_pre->lidar_type 5`、`IMU Initial Done`、`Initialize the map kdtree`，随后连续约 45 次 `[ mapping ]` 更新；
- `/Odometry` 记录 **27 个位姿**（`odometry_5s.csv`）；
- 轨迹范围：x 0.184 m、y 0.230 m、z 0.130 m；最大半径 **0.199 m**；起点 `(-0.160,-0.116,0.020)`，终点 `(-0.053,0.053,0.062)`；
- `/cloud_registered` 最新云保存为 `cloud_5s.ply`，**1,326 点**（该消息是稀疏配准云，不是完整地图）；`cloud_5s.png` 为同一云的三维预览，只能说明消息内容可读，不能当重建结果；
- `Log/pos_log.txt` 43 行，和 odometry 数量不同（记录时机不同，未做对账）。

### 5 帧短包

- 一次成功运行仅有 1 行轨迹；多次运行完全没有点回调，说明短包投递不稳定。

## 阻塞与不稳定

- **FastDDS 在本 WSL 环境无法完成跨进程发现**；`ROS_LOCALHOST_ONLY=0/1` 都不通，`ros2 topic list` 会挂起。改用 `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` 后 talker/listener 与 FAST-LIO 才能收到数据。
- 5 帧短包跑次间不稳定；50 帧包连续两次成功处理约 45 次更新，说明更长的包能显著降低投递/初始化失败概率，但尚未做重复性统计。
- 本包时间等级 C、无有效 `T_imu_lidar`，诊断配置使用单位外参；轨迹漂移/抖动包含振动、时间近似和未标定外参，**不能用于精度或标定结论**。
- 上游 `pcd_save` 代码块在 `laserMapping.cpp` 中被注释，未生成完整 PCD 地图；只保存了 `/cloud_registered` 的最新稀疏云。
- 场景是雷达工作振动的工程录制，不是操场移动采集；无独立轨迹真值，ATE/RPE `NOT_EVALUATED`。

## 结论与下一步

状态：**PROTOTYPE_CONSUMED_50_FRAMES / DIAGNOSTIC_TRAJECTORY_ONLY / ACCURACY_NOT_EVALUATED**。

- 证明：补丁可编译、NA70 消息可被 FAST-LIO 消费、IMU 可初始化、5 s 包可产生连续诊断轨迹和稀疏配准云。
- 未证明：轨迹精度、地图精度、外参、时间同步、回环或真实操场重建。
- 下一步：在稳定 CycloneDDS 与更长包上做重复性；补 `/cloud_registered` 累积/保存；有 `T_imu_lidar` 候选后比较诊断轨迹；真实移动数据到位后再谈 S3/S5。

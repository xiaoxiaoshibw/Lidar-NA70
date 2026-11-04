# NA70 非重复扫描激光雷达 SLAM 开源代码调研

日期：2026-10-30。状态：`SOURCE_REVIEW_ONLY / NOT_BUILT / NOT_RUN`。

后续开发更新：本调研状态描述调研完成时的快照；同日随后已完成软件准备、上游构建与公开数据冒烟，实际结果见[开发报告](../Plan/evidence/slam/20261030-development/REPORT.md)。NA70 实测门禁仍未通过。

已知条件：NA70＋USB IMU，手持/小车、操场/室外；暂无本套组合的新同步录制。本文核对公开项目、选定源码路径和输入假设，供[开发计划](../Plan/NA70_雷达IMU_SLAM开发计划.md)选型。不把 README 示例、其他型号雷达的地图或代码能够编译当成 NA70 实测结果。

## 1. 当前建议

保留 **FAST-LIO2 ROS2 作为首个局部里程计适配候选**；增加 **GLIM CPU 作为完整建图对照**，在自己开发回环后端之前先验证现有框架。Voxel-SLAM 作为完整系统研究参考，Point-LIO 留作后续高动态对照；不同时移植全部方案。

这项建议基于输入接口、现有 ROS2 支持和模块完整度，不是 NA70 精度或 Orin Nano 4GB 性能排名。此次公开仓库关键词搜索 `NA70 SLAM`、`NA70 lidar` 未找到可直接采用的 NA70 专用实现；检索范围有限，不代表此类代码一定不存在。

## 2. 候选代码清单

| 项目与一手入口 | 非重复扫描相关性 | ROS 与全局能力 | 本项目用途 |
|---|---|---|---|
| [FAST-LIO2](https://github.com/hku-mars/FAST_LIO/tree/ROS2) | 直接点到地图，提供 Livox 适配 | 上游有 ROS2 分支，文档推荐 Humble；主仓库不能直接当作已完成回环的全局系统 | 首选局部前端；专门处理 NA70 时间和来源引用 |
| [GLIM](https://github.com/koide3/glim)＋[glim_ros2](https://github.com/koide3/glim_ros2) | 文档明确列出 Livox Avia/MID360 等非重复扫描雷达；通用 PointCloud2 入口 | CPU/GPU 模块、子地图、全局优化；ROS2 CI 包含 Humble CPU 配置 | 完整建图优先对照；先验证 CPU 配置，再测 ARM 资源 |
| [Voxel-SLAM](https://github.com/hku-mars/Voxel-SLAM) | 官方含 Avia 示例 | 官方主线 ROS1/Noetic，初始化、里程计、局部 BA、回环、多会话与全局 BA | 研究完整系统组织与稀疏几何处理；移植成本另评估 |
| [Voxel-SLAM-ROS2 社区移植](https://github.com/tanakon-apit/Voxel-SLAM-ROS2) | 文档提供 Avia/MID360 入口 | README 声明 Humble，含额外 IMU 断流 LO 回退修改 | 备选移植参考，不能当成官方 ROS2 等价实现；需差异审计和故障测试 |
| [Point-LIO](https://github.com/hku-mars/Point-LIO/tree/point-lio-with-grid-map) | 逐点时间驱动状态更新，支持 Livox | 核对的官方分支文档为 Noetic；不是即接即用的 Humble 全局 SLAM | 后续高动态/不同更新策略对照，先解决采样时间 |
| [Faster-LIO](https://github.com/gaoxiang12/faster-lio) | FAST-LIO2 路线，iVox 地图，包含固态雷达示例 | 主仓库文档为 ROS1；有离线程序；不能从速度示例推断 NA70/Jetson 性能 | 仅在前端正确后研究热点优化，不首轮移植 |
| [FAST_LIO_SLAM](https://github.com/gisbi-kim/FAST_LIO_SLAM) | FAST-LIO 前端＋Scan Context/SC-PGO | ROS1；已有回环、GTSAM 图优化和优化地图导出 | 回环后端代码参考；当前核对版本较旧，有限 FOV 需单独验证 |
| [FAST-LIO-SAM-SC-QN](https://github.com/engcang/FAST-LIO-SAM-SC-QN) | FAST-LIO2＋Scan Context＋Quatro＋Nano-GICP | ROS1 集成参考 | 阅读回环候选与几何验证组织；README 标注 CC BY-NC-SA 4.0，不作为首轮直接合入方案 |
| [LIO-Livox](https://github.com/Livox-SDK/LIO-Livox) | Livox 官方，面向其固态雷达与 IMU | 核对文档为 ROS1；使用厂商消息、初始化和滑窗估计 | 输入/初始化研究参考，非 NA70 驱动替代品 |

表中 ROS 环境按本次核对的分支或文档描述，未检验所有社区 fork。Voxel-SLAM、社区移植与 LIO-Livox 本轮主要核对 README，未宣称完成其算法源码审计。

## 3. 关键源码核对结果

### 3.1 FAST-LIO2：通用 XYZI 入口不等于具备去畸变

固定版本：[cc3c9e8b](https://github.com/hku-mars/FAST_LIO/commit/cc3c9e8b1469b33b8072764d6db2348ed6dc597f)。

- [preprocess.cpp L55](https://github.com/hku-mars/FAST_LIO/blob/cc3c9e8b1469b33b8072764d6db2348ed6dc597f/src/preprocess.cpp#L55) 根据传感器类型分发，并处理时间单位；已有传感器处理不能自动等同 NA70。
- [default_handler L569](https://github.com/hku-mars/FAST_LIO/blob/cc3c9e8b1469b33b8072764d6db2348ed6dc597f/src/preprocess.cpp#L569) 只读取 XYZI，并在 L592 将用于存放时间的 `curvature` 置零。通用入口能接收点云，不表示保留了包内/帧内时序。
- [laserMapping.cpp L979](https://github.com/hku-mars/FAST_LIO/blob/cc3c9e8b1469b33b8072764d6db2348ed6dc597f/src/laserMapping.cpp#L979) 调用 IMU 处理；[L1068](https://github.com/hku-mars/FAST_LIO/blob/cc3c9e8b1469b33b8072764d6db2348ed6dc597f/src/laserMapping.cpp#L1068) 调用地图增量更新。质量门控必须覆盖内部地图更新，不能仅在最终地图导出时过滤。

因此最小适配应处理真实相对时间、单位、非有限点和原始引用，并显式区分包级近似。当前本地驱动 `offset_time = i*5000 + packet_index*1000000` 的推算值不能直接升格为实测逐点时间。

### 3.2 GLIM：通用点云、时间管理与完整后端值得验证

固定核心版本：[1a197c99](https://github.com/koide3/glim/commit/1a197c999e4865f5cab7c9cd25474128413ebbca)；ROS2 wrapper：[4d4ec524](https://github.com/koide3/glim_ros2/commit/4d4ec524ccf1b02aa09b0af2af767ecc54343798)。

- [glim_ros.cpp L282](https://github.com/koide3/glim_ros2/blob/4d4ec524ccf1b02aa09b0af2af767ecc54343798/src/glim_ros/glim_ros.cpp#L282)：PointCloud2 → 原始点提取 → 时间检查 → 预处理 → 里程计，IMU 通过单独入口进入。
- [config_sensors.json](https://github.com/koide3/glim/blob/1a197c999e4865f5cab7c9cd25474128413ebbca/config/config_sensors.json)：可配置相对/绝对时间、时间缩放及可选 ring 字段；`global_shutter_lidar` 会清零点时间并关闭去畸变，不能用它掩盖运动数据时间缺失。
- [time_keeper.cpp](https://github.com/koide3/glim/blob/1a197c999e4865f5cab7c9cd25474128413ebbca/src/glim/util/time_keeper.cpp)：有时间转换及倒退/缺口检查，但库内自动识别不是硬件同步验证。
- [global_mapping_pose_graph.cpp](https://github.com/koide3/glim/blob/1a197c999e4865f5cab7c9cd25474128413ebbca/src/glim/mapping/global_mapping_pose_graph.cpp)：已有回环候选处理、配准约束和 iSAM2 图更新，具备复用价值；是否满足本项目误回环门禁仍需验证。
- [CPU quickstart](https://github.com/koide3/glim/blob/1a197c999e4865f5cab7c9cd25474128413ebbca/docs/quickstart.md)：给出 CPU 里程计＋passthrough 子地图＋pose-graph 后端组合。首个软件试验可采用该组合，不必先引入 CUDA。

特别注意外参：本项目统一的 `T_IL` 为 LiDAR → IMU；GLIM 的 `T_lidar_imu` 为 IMU → LiDAR，所以应填 `inverse(T_IL)`，不能照抄 FAST-LIO 配置。该方向直接由固定版本传感器配置注释和参数文档确认。

GLIM 主包 MIT、FAST-LIO2 根 LICENSE 为 GPLv2；这里只记录源码许可来源，不替代最终依赖清单。GLIM 的 GTSAM/gtsam_points 版本和 JetPack 组合需要成套固定，不能把昨天规划的任意 GTSAM 版本直接混装。

### 3.3 现成回环代码的复用边界

[FAST_LIO_SLAM 的 laserPosegraphOptimization.cpp](https://github.com/gisbi-kim/FAST_LIO_SLAM/blob/10eab538db70678f41aaa24247d16831ca921374/SC-PGO/src/laserPosegraphOptimization.cpp) 已包含 Scan Context 候选、局部子图 ICP、GTSAM 因子和优化线程。这比从零写回环更适合作为阅读起点；ROS1 接口、旧前端和传感器假设仍需迁移。

操场重复围栏、有限 FOV 和相反朝向的复访会影响回环。移植时必须保存候选及拒绝理由，并验证重叠、弱方向和多帧一致性，不能只保留一次 ICP fitness 判定。这里是本项目对复用的要求，不是声称这些仓库在 NA70 上已失败。

## 4. 没有新数据时可以先推进的任务

这些是下一批软件准备任务，**本轮只做搜索和源码核对，尚未执行下表的构建、下载和回放**：

| 顺序 | 可独立推进的工作 | 验证产物与边界 |
|---|---|---|
| P0 | 按本次 commit 和传递依赖建立独立 Linux/Humble 工作目录 | 构建日志、依赖版本、补丁；保留现有 Windows 环境 |
| P1 | 编译 FAST-LIO2 基线，再验证 GLIM CPU 配置 | 可重复构建/启动，尚不代表 NA70 适配 |
| P2 | 从作者公开示例选一段已有雷达＋IMU 数据 | 优先 Avia/MID360 等非重复扫描示例；固定输入哈希，标 `PUBLIC_DATASET`；ROS1 bag 须核对转换后的类型与时间 |
| P3 | 跑上游基线，保存轨迹、地图及输入质量 | 证明环境和上游流程可用；不改成 NA70 精度或同步证据 |
| P4 | 定义 NA70 时序适配与 USB IMU 数据合同 | 明确字段、单位、坐标、设备/接收时钟、质量等级；IMU 协议未知时不编造串口解析器 |
| P5 | 最小合成契约验证 | `SYNTHETIC` 测试外参逆变换、相对时间单位、包级近似标签、缺口/倒退、无效点和 source ID 保留；不做重复实现算法的测试 |

FAST-LIO 的作者示例入口见其 [README 数据段](https://github.com/hku-mars/FAST_LIO/tree/ROS2#4-rosbag-example)；GLIM [quickstart](https://github.com/koide3/glim/blob/1a197c999e4865f5cab7c9cd25474128413ebbca/docs/quickstart.md) 有 ROS2 Ouster 示例（可测环境，不能证明非重复扫描适配）；Voxel-SLAM [Avia 示例](https://github.com/hku-mars/Voxel-SLAM#41-livox-avia) 可作后续非重复扫描对照。数据链接可访问性、文件大小、许可和校验值应在实际下载时再确认。

软件准备不需要等待新的现场录制；但 S1 同步合格、S2 外参/补偿通过、S3 NA70 移动里程计以及最终完整操场地图，仍要由本套硬件数据证明。旧 PCAP 不补造 IMU，公开数据不冒充本机采集。

## 5. 固定版本与复核范围

完整 commit 和选定源码 blob SHA 保存在 [source-manifest.json](../Plan/evidence/slam/20261030-code-survey/source-manifest.json)。分支会变化，后续实现按该记录或明确更新后的新版本复核。

| 代码库 | 本次分支 | commit 前 12 位 |
|---|---|---|
| FAST_LIO | ROS2 | `cc3c9e8b1469` |
| Point-LIO | point-lio-with-grid-map | `4b86a469eb55` |
| Faster-LIO | main | `8038fbac9528` |
| GLIM | master | `1a197c999e48` |
| glim_ros2 | master | `4d4ec524ccf1` |
| Voxel-SLAM | main | `70fc8a28d638` |
| Voxel-SLAM-ROS2 社区移植 | main | `6b2f97c94604` |
| FAST_LIO_SLAM | main | `10eab538db70` |

本轮未安装依赖、未克隆整库、未编译或运行上述系统、未下载公开数据、未验证 Jetson。源码阅读深度限于文中列明的路径；既有 SLAM 计划状态仍为 `DESIGN / NOT_RUN`。

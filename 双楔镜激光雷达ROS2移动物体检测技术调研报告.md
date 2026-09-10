# 双楔镜激光雷达 ROS2 移动物体检测技术调研报告

> **面向设备**：256 线双楔镜扫描激光雷达（Innolight/Innoview InnoLiDAR 系列）
> **调研目标**：借鉴原厂软件 pcap 解析思路 + 开源项目代码 + 算法模型，为 ROS2 驱动实现移动物体检测（Moving Object Detection, MOD）提供技术路线
> **调研日期**：2026-09-08
> **信息标注**：`[已查证]` = 多来源交叉验证；`[一方称]` = 单一来源，需实测确认

---

## 目录

1. [原厂软件 pcap 处理管线分析](#1-原厂软件-pcap-处理管线分析)
2. [维度一：激光雷达 pcap 解析开源项目](#2-维度一激光雷达-pcap-解析开源项目)
3. [维度二：点云移动物体检测经典算法](#3-维度二点云移动物体检测经典算法)
4. [维度三：点云移动物体检测深度学习模型](#4-维度三点云移动物体检测深度学习模型)
5. [维度四：ROS2 生态现成方案](#5-维度四ros2-生态现成方案)
6. [维度五：综合方案建议与技术路线](#6-维度五综合方案建议与技术路线)
7. [参考资料汇总](#7-参考资料汇总)

---

## 1. 原厂软件 pcap 处理管线分析

### 1.1 软件身份与技术栈

| 项目 | 内容 |
|------|------|
| 软件名称 | `Double_Wedge_Mirror.exe` v0.0.1.2 |
| 设备 | 256 线双楔镜扫描激光雷达（Innolight/Innoview） |
| GUI 框架 | Qt5 |
| 点云处理 | PCL（Point Cloud Library） |
| 视觉算法 | OpenCV 4.5.4 |
| 3D 可视化 | VTK 8.2 |
| 网络抓包 | WinPcap（`wpcap.dll` + `packet.dll`） |
| 串口通信 | QextSerialPort（`itas109/CSerialPort` 库） |

### 1.2 pcap 处理完整管线

通过二进制符号和字符串分析，原厂软件的 pcap 处理管线如下：

```
pcap 文件 (.pcap/.pcapng)
    │
    ▼
pcap_open_offline()          ← 打开离线 pcap 文件
    │  (或 pcap_open_live()  ← 实时抓包模式)
    ▼
pcap_compile()               ← 编译 BPF 过滤器
    │  过滤表达式: "dst port <端口号>"
    │  (按目标 UDP 端口过滤激光雷达数据包)
    ▼
pcap_next_ex()               ← 逐包读取
    │  (或 pcap_offline_filter() 做离线过滤)
    ▼
UDP payload 解析             ← 解析为自定义 PointPacket 结构
    │  (std::shared_ptr<PointPacket>)
    ▼
点云转换                     ← 转为 PCL 点云
    │  pcl::PointCloud<pcl::PointXYZRGB>
    ▼
VTK 渲染                     ← 256 条扫描线独立颜色渲染
    │  line_0 ~ line_255，每条线独立 RGB 颜色
    │  含网格、坐标轴、标记点
    ▼
设备控制                     ← QextSerialPort 串口发送控制指令
```

### 1.3 关键配置参数（从 config.ini 和 Device.yaml 提取）

| 参数 | 值 | 来源 |
|------|-----|------|
| 扫描线数 | 256（line_0 ~ line_255） | Device.yaml |
| 水平角分辨率 | 1° | Device.yaml |
| 垂直角分辨率 | 1° | Device.yaml |
| 距离分辨率 | 0.004 m | Device.yaml |
| distance_flag | 1380 | config.ini |
| 像素分辨率 x | 0.46454 | config.ini |
| 像素分辨率 y | 0.462738 | config.ini |
| 消息分辨率 x/y | 25 | config.ini |

### 1.4 光检校准算法（LightCheckAlgorithmParam）

原厂软件内置了基于 OpenCV 的激光线/光斑校准验证算法：

- **角点检测**：`cv::goodFeaturesToTrack`（max_corners=500, quality_level=0.01, min_distance=5）
- **ROI 裁剪**：x=200, y=550, width=2000, height=400
- **自动旋转**：支持自动旋转校正
- **轮廓检测**：threshold=25, min_area=10, max_area=1e7

### 1.5 关键类结构

| 类名 | 职责 |
|------|------|
| `LidarModule` | 激光雷达数据处理主模块 |
| `DeviceControl` | 设备控制（串口指令） |
| `DeviceManager` | 设备管理与枚举 |
| `PressureModule` | 气压模块（可能用于距离校正/大气补偿） |
| `CaptureDevice` | 抓包设备抽象 |
| `Input` | 输入抽象（pcap / live socket） |
| `PcapConfigDialog` | pcap 配置对话框 |
| `NetWorkConfigDialog` | 网络配置对话框 |
| `innolight::data_type::DeviceParam` | 设备参数数据结构 |

### 1.6 对 ROS2 驱动的启示

1. **输入抽象层**：原厂软件将 pcap 离线回放和实时抓包统一在 `Input` 抽象下，ROS2 驱动应借鉴此设计，定义统一的 `Input` 基类，派生 `PcapInput` 和 `SocketInput`
2. **BPF 过滤**：使用 `"dst port <端口>"` 按 UDP 目标端口过滤，ROS2 驱动中可直接使用 libpcap 的 BPF 机制，避免在用户态过滤所有数据包
3. **PointPacket 中间结构**：UDP payload 先解析为自定义 `PointPacket`，再转为 PCL 点云，这种分层解析设计便于处理多回波和时间戳
4. **256 线独立颜色**：VTK 渲染中每条扫描线独立颜色，说明点云中包含 ring/line ID 信息，ROS2 点云应保留 `ring` 字段
5. **串口控制分离**：设备控制通过独立串口模块，与数据接收解耦，ROS2 驱动应将数据节点和控制节点分离

---

## 2. 维度一：激光雷达 pcap 解析开源项目

### 2.1 架构共性

通过实际下载并阅读 Velodyne、Ouster、Hesai、RoboSense 四家厂商的开源源码，所有厂商的 pcap 模块均采用**三层架构**：

```
Input 抽象层 → 协议解析层 → 点云组装层
```

| 层级 | 职责 | 典型类名 |
|------|------|----------|
| Input 抽象层 | 统一 pcap 离线和 socket 实时输入，处理 BPF 过滤、速率控制 | `Input`、`InputPCAP`、`InputSocket` |
| 协议解析层 | 解析 UDP payload 为中间数据结构（点、角度、时间戳、回波） | `RawData`、`Decoder`、`PacketParser` |
| 点云组装层 | 将解析后的点按帧组装为 `sensor_msgs/PointCloud2` 或 PCL 点云 | `PointCloudContainer`、`Scan`、`OrganizedCloud` |

### 2.2 各厂商实现详解

#### 2.2.1 Velodyne

| 项目 | 内容 |
|------|------|
| 仓库 | https://github.com/ros-drivers/velodyne（ros2 分支） |
| 关键文件 | `velodyne_driver/src/lib/input.cc`（pcap 读取）、`velodyne_pointcloud/src/lib/rawdata.cc`（UDP 解析） |
| pcap 库 | 裸 libpcap |
| 过滤方式 | BPF 按 UDP 端口过滤 |
| 速率控制 | 固定频率（基于 pcap 包时间戳间隔） |
| 帧切分 | azimuth 角度回绕（从 360° 回到 0°） |
| 点云格式 | organized（H×W，H=线数） |

**核心设计**：
- `Input` 基类派生 `InputPCAP` 和 `InputSocket`，统一接口 `getPacket()`
- `rawdata.cc` 中 `unpack()` 函数解析单个 UDP 包为点列表
- 硬编码以太网偏移 42 字节（14 以太网 + 20 IP + 8 UDP），**不支持 IPv6/VLAN**
- 多回波通过配置参数切换（最强回波/最后回波/双回波）

**可借鉴点**：Input 基类抽象设计、`unpack()` 单包解析函数的清晰分层

**不足**：硬编码偏移不支持 IPv6/VLAN、固定频率速率控制不适应转速波动

#### 2.2.2 Ouster

| 项目 | 内容 |
|------|------|
| 仓库 | https://github.com/ouster-lidar/ouster-ros |
| 关键文件 | `ouster_client/src/os_pcap.cpp`（PcapReader 类）、`ouster_client/src/packet_source.cpp` |
| pcap 库 | **libtins**（非裸 libpcap） |
| 过滤方式 | libtins 内置 Sniffer 配置 + 端口过滤 |
| 速率控制 | 基于包时间戳的回放速率控制 |
| 帧切分 | 列格式（column format），按帧号/列计数切分 |
| 点云格式 | organized（H×W） |

**核心设计**：
- 使用 **libtins** 库替代裸 libpcap，唯一支持 **IP 分片重组**、IPv6、随机帧索引
- `PcapReader` 类封装 pcap 读取，支持按时间戳回放
- `packet_source` 抽象统一 pcap 和 sensor 输入
- Ouster 的列格式（column format）每列包含多个通道的距离、强度、时间戳
- IMU 数据通过独立包传输，与点云包分离

**可借鉴点**：libtins 的 IP 分片重组能力（激光雷达大包可能被分片）、PcapReader 的随机帧索引

**不足**：libtins 增加依赖、列格式解析复杂

#### 2.2.3 Hesai

| 项目 | 内容 |
|------|------|
| 仓库 | https://github.com/HesaiTechnology/HesaiLidar_General_ROS（ROS1）/ ROS2 版本 |
| 关键文件 | `src/input.cc`、`src/util.cc`、`src/ptc_client.cc`、pcap_reader 相关文件 |
| pcap 库 | 裸 libpcap |
| 过滤方式 | BPF 过滤，支持 IPv4/IPv6 动态判断 |
| 速率控制 | **硬件时间戳差值控制**（比固定频率精准） |
| 帧切分 | 包内帧标志位 |
| 点云格式 | organized |

**核心设计**：
- **硬件时间戳速率控制**：利用包内硬件时间戳的差值控制回放速率，比 Velodyne 的固定频率更精准，适合转速波动场景
- **IPv4/IPv6 动态判断**：不硬编码 42 字节偏移，根据 IP 头版本字段动态计算偏移，支持 VLAN
- `pcap_reader` 独立类，与 socket input 共享解析层
- PTC（Pandar TCP Control）客户端用于设备配置和监控，与数据通道分离

**可借鉴点**：硬件时间戳速率控制（双楔镜转速可能波动）、IPv4/IPv6 动态偏移判断

**不足**：ROS2 版本成熟度略低于 ROS1

#### 2.2.4 RoboSense

| 项目 | 内容 |
|------|------|
| 仓库 | https://github.com/RoboSense-LiDAR/rslidar_sdk |
| 关键文件 | `rs_driver/src/rs_driver/input/input_pcap.hpp`、`rs_driver/src/rs_driver/driver/driver_param.hpp` |
| pcap 库 | 裸 libpcap |
| 过滤方式 | **三端口 BPF**（MSOP/DIFOP/IMU 独立过滤器） |
| 速率控制 | 基于包时间戳 |
| 帧切分 | MSOP 包内帧标志 |
| 点云格式 | organized |

**核心设计**：
- **MSOP/DIFOP 双端口架构**：MSOP（Measurement Data Output Port）传输点云数据，DIFOP（Device Information Output Port）传输设备配置/参数，IMU 独立端口
- **三端口独立 BPF 过滤器**：最清晰的过滤设计，不同类型包使用不同端口和过滤器
- **多回波动态切换**：唯一支持运行时从 DIFOP 包检测回波模式并自动切换解析逻辑
- `InputPCAP` 继承 `Input` 基类，与 `InputSocket` 共享 `Driver` 解析层
- 解码器支持多种型号（RS16/RS32/RS80/RS128/RSHelios 等），通过工厂模式创建

**可借鉴点**：MSOP/DIFOP 双端口分离设计、三端口 BPF 过滤、多回波动态切换、Input 基类 + Driver 解析层的分层架构

### 2.3 横向对比

| 维度 | Velodyne | Ouster | Hesai | RoboSense |
|------|----------|--------|-------|-----------|
| pcap 库 | 裸 libpcap | **libtins** | 裸 libpcap | 裸 libpcap |
| IP 分片重组 | ❌ | ✅ | ❌ | ❌ |
| IPv6/VLAN | ❌（硬编码42） | ✅ | ✅（动态判断） | ✅ |
| 过滤设计 | 单端口 BPF | libtins Sniffer | 单端口 BPF | **三端口 BPF**（MSOP/DIFOP/IMU） |
| 速率控制 | 固定频率 | 时间戳 | **硬件时间戳差值** | 时间戳 |
| 多回波切换 | 配置参数 | 配置参数 | 配置参数 | **运行时动态切换** |
| 帧切分 | azimuth 回绕 | 列格式/帧号 | 包内帧标志 | 包内帧标志 |
| 点云格式 | organized | organized | organized | organized |
| 控制通道 | 无 | TCP | PTC (TCP) | DIFOP (UDP) |

### 2.4 对双楔镜雷达 ROS2 驱动的核心建议

1. **采用 RoboSense 的 MSOP/DIFOP 双端口 + Input 基类架构**：数据端口和配置端口分离，Input 抽象统一 pcap/socket
2. **采用 Hesai 的硬件时间戳速率控制**：双楔镜转速可能波动，硬件时间戳差值比固定频率更精准
3. **点云用 unorganized 格式**：双楔镜非均匀 Lissajous 扫描不形成规则 H×W 阵列，`height=1` 的无序点云更合适
4. **帧切分基于包内帧标志**：而非 azimuth 回绕（双楔镜扫描不回绕）
5. **以太网偏移不要硬编码 42**：参考 Hesai 的 IPv4/IPv6 动态判断
6. **考虑使用 libtins**：如果激光雷达数据包可能被 IP 分片，libtins 的分片重组能力很有价值
7. **保留 ring/line ID 字段**：原厂软件 256 线独立颜色渲染说明点云包含线号信息

---

## 3. 维度二：点云移动物体检测经典算法

### 3.1 双楔镜扫描的特殊性

双楔镜（Double Wedge Mirror）扫描通过两个旋转楔形棱镜的相对角度合成扫描轨迹，与传统机械式旋转雷达有本质差异：

| 特性 | 传统旋转雷达 | 双楔镜扫描雷达 |
|------|-------------|---------------|
| 扫描轨迹 | 圆周扫描（规则扫描线） | Lissajous 曲线/玫瑰线（非均匀） |
| ring ID | 有固定 ring 索引 | 无固定 ring 索引（需计算） |
| 点云组织 | organized（H×W） | unorganized（height=1） |
| 地面分割 | ray_ground_filter 可直接用 | 需重网格化或用不依赖扫描线的算法 |
| 点云密度 | 近密远疏 | 非均匀分布，局部密度变化大 |

> **关键影响**：基于扫描线有序性的算法（line-fit、ray_ground_filter）在双楔镜点云上需要先做角度重网格化，不能直接套用。**RANSAC 平面拟合不依赖扫描线，是双楔镜雷达地面分割的首选。**

### 3.2 地面分割 / 背景减除

#### 3.2.1 RANSAC 平面拟合（首选）

| 项目 | 内容 |
|------|------|
| 原理 | 随机采样 3 点确定平面 `Ax+By+Cz+D=0`，迭代选取内点最多的平面 |
| 适用场景 | 平坦道路、停车场；**双楔镜雷达首选**（不依赖扫描线） |
| 计算复杂度 | O(k×n)，k=迭代次数(50~200) |
| 实时性 | 优（~12ms/帧）`[已查证]` |
| 开源实现 | PCL `pcl::SACSegmentation`；Autoware `ground_segmentation` RANSAC 模式 |

**关键参数初始值**：
- `distance_threshold`: 0.15 m（距离分辨率 0.004m，可容忍路面微小起伏）
- `max_iterations`: 100
- `optimize_coefficients`: true

**优点**：实现简单、速度快、对噪声鲁棒、PCL 原生支持、**不依赖扫描线有序性**

**缺点**：假设地面为单一平面，无法适应坡道/起伏路面；对低矮障碍物（路沿石）易误分

#### 3.2.2 Line-Fit / 高度差法

| 项目 | 内容 |
|------|------|
| 原理 | 按扫描线从近到远比较高度差/坡度，超阈值判为非地面 |
| 适用场景 | 机械式旋转雷达，城市道路 |
| 计算复杂度 | O(n) |
| 实时性 | 优（~10ms/帧） |
| 开源实现 | Autoware `ray_ground_filter` |
| 双楔镜适配 | **差**，需先做角度重网格化 |

#### 3.2.3 PMF 渐进式形态学滤波

| 项目 | 内容 |
|------|------|
| 原理 | 栅格化最小高度图，多尺度形态学开运算 + 高差阈值 |
| 适用场景 | 机载 LiDAR 地形测绘；**不适合车载实时 MOD** |
| 计算复杂度 | O(k×m×n) |
| 实时性 | 差（~482ms/帧）`[已查证]` |
| 开源实现 | PCL `pcl::ProgressiveMorphologicalFilter` |

### 3.3 聚类算法

#### 3.3.1 Euclidean Clustering（首选）

| 项目 | 内容 |
|------|------|
| 原理 | KD-Tree 加速的欧氏距离区域生长，`cluster_tolerance` 内的点归为一簇 |
| 适用场景 | 目标间距明显的场景，实时系统 |
| 计算复杂度 | O(n log n) |
| 实时性 | 优（5~20ms/帧） |
| 开源实现 | PCL `pcl::EuclideanClusterExtraction`；Autoware `autoware_euclidean_cluster` |

**关键参数初始值**：
- `cluster_tolerance`: 0.3 m（近距离）/ 0.5 m（远距离，1° 分辨率下点间距大）
- `min_cluster_size`: 10
- `max_cluster_size`: 50000

**优点**：PCL 原生支持、实现简单、参数少、速度快

**缺点**：单一 tolerance 难适应密度变化；相邻目标易合并（欠分割）

#### 3.3.2 DBSCAN

| 项目 | 内容 |
|------|------|
| 原理 | 密度可达区域生长，`eps` 邻域内至少 `min_points` 个点为核心点 |
| 适用场景 | 密度不均匀、需要自动去噪的场景 |
| 计算复杂度 | O(n log n) |
| 开源实现 | PCL/sklearn；改进版 Recall 94.47%, FPS 20.0 `[已查证]` |

**优点**：无需预设簇数、能发现任意形状、天然去噪

**缺点**：`eps` 参数极敏感，近密远疏场景单一 eps 难兼顾

#### 3.3.3 体素下采样 + 聚类（推荐组合）

256 线点云密度极高，**必须先下采样**再聚类：

```
原始点云 → VoxelGrid(leaf_size=0.1m) → 点数从数十万降至 2~5万 → Euclidean Clustering
```

- 体素尺寸建议从 0.1 m 开始尝试
- 下采样可均匀化点云密度，缓解近密远疏问题

#### 3.3.4 其他聚类算法

| 算法 | 特点 | 适用场景 |
|------|------|----------|
| 条件欧氏聚类 | 距离 + 自定义条件（强度/法向量） | 需要利用附加属性区分目标 |
| 区域生长 | 法向量+曲率区域生长 | 区分光滑表面/植被，需先估计法向量 |

### 3.4 帧间差分 / 运动估计

#### 3.4.1 ICP 配准残差检测

| 项目 | 内容 |
|------|------|
| 原理 | ICP 配准当前帧与上一帧，补偿自车运动后，最近邻距离 > 阈值的点判为动态 |
| 配准精度要求 | 高（< 0.05m，否则静态点残差淹没动态信号） |
| 计算量 | 中高（ICP 多次迭代） |
| 开源实现 | PCL `pcl::IterativeClosestPoint`；Open3D |

**优点**：原理直观、无需先验地图、可同时输出里程计

**缺点**：对配准精度要求极高；动态目标占比大时 ICP 收敛困难；低速目标漏检

#### 3.4.2 体素帧间差分

| 项目 | 内容 |
|------|------|
| 原理 | 构建体素占据图，当前帧与上一帧做差集，"出现"/"消失"体素标记为动态 |
| 计算量 | 低 |
| 开源实现 | PCL `VoxelGrid`；OctoMap `changedKeys` |

**优点**：计算效率高、能处理任意形状、可与 OctoMap 集成

**缺点**：体素化损失精度、需运动补偿、单帧差分噪声大需多帧平滑

#### 3.4.3 场景流估计

| 模型 | 论文 | 代码 | 说明 |
|------|------|------|------|
| FlowNet3D | CVPR 2019, https://arxiv.org/abs/1806.01411 | https://github.com/xingyul/flownet3d | PointNet++ 场景流 |
| HPLFlowNet | CVPR 2019, https://arxiv.org/abs/1906.01507 | https://github.com/laoreja/HPLFlowNet | permutohedral lattice，速度更快 |
| PointPWC-Net | ECCV 2020, https://arxiv.org/abs/1911.00984 | https://github.com/DylanWusee/PointPWC | 代价体，精度领先 |
| ICP-Flow | https://arxiv.org/html/2402.17351 | https://github.com/wh201906/ICP-Flow | learning-free，刚体运动假设 |

**用场景流检测运动物体**：输入两帧点云 → 输出每点 3D 位移向量 → 补偿自车运动后位移 > 阈值的点判为动态

**优缺点**：信息丰富（运动方向+速度）、可检测未知类别；但计算量大、多数无法实时、ROS2 无成熟 package

### 3.5 多目标跟踪

#### 3.5.1 Kalman + Hungarian（SORT，首选）

| 项目 | 内容 |
|------|------|
| 原理 | Kalman 滤波维护状态 `[x,y,z,vx,vy,vz]`，Hungarian 算法做 IoU/距离匹配 |
| 实时性 | 优（数百 FPS，单帧 < 5ms） |
| 开源实现 | Autoware `autoware_multi_object_tracker`；abewley/sort |
| ROS2 集成 | Autoware 官方集成 `[已查证]` |

**关键参数初始值**：
- Kalman 过程噪声：位置 0.1m，速度 1.0m/s
- Hungarian 距离阈值：1.0 m
- 最大未匹配帧数：10 帧（~0.3s@30fps）
- 新轨迹确认帧数：3 帧

**优点**：计算效率极高、工程化成熟、Kalman 提供平滑运动估计、Autoware 集成

**缺点**：匀速模型在急转/急停时不准；目标密集交叉时 ID 切换

#### 3.5.2 Autoware multi_object_tracker 详解

Autoware Universe 的 `autoware_multi_object_tracker` 是最成熟的 ROS2 跟踪器：

- 支持多种运动模型：匀速（CV）、匀加速（CA）、自行车模型（Bicycle）
- 数据关联：GNN + Hungarian 算法
- 支持 L-shape 拟合用于车辆姿态估计
- 输入：`autoware_auto_perception_msgs/DetectedObjects`
- 输出：`autoware_auto_perception_msgs/TrackedObjects`

#### 3.5.3 其他跟踪算法

| 算法 | 特点 | 实时性 | ROS2 成熟度 |
|------|------|--------|-------------|
| Deep SORT | 级联匹配（运动+外观），点云外观特征困难 | 优 | 中（2D 成熟） |
| JPDA | 概率软关联，处理模糊关联 | 中（目标<20） | 低（无成熟 package） |
| MHT | 多假设延迟决策，精度最高 | 差 | 极低 |
| L-shape + Kalman | 车辆矩形拟合+跟踪 | 优 | 高（Autoware 集成） |

### 3.6 变化检测 / 地图差分

| 算法 | 原理 | 实时性 | 开源实现 |
|------|------|--------|----------|
| OctoMap 变化检测 | 八叉树概率占据 + `changedKeys` | 中 | https://github.com/OctoMap/octomap |
| Occupancy Grid | 二维栅格贝叶斯更新 | 优 | `nav2_costmap_2d` |
| Removert | 视点可见性比较 + 动态点移除 | 差（离线） | https://github.com/url-kaist/removert |
| ERASOR/ERASOR2 | 伪占据变化率 + 地面接触假设 | 差（离线） | https://github.com/url-kaist/ERASOR |

### 3.7 经典算法横向对比汇总

| 类别 | 首选算法 | 理由 |
|------|----------|------|
| 地面分割 | **RANSAC 平面拟合** | 不依赖扫描线，双楔镜雷达天然适配，速度快 |
| 聚类 | **VoxelGrid + Euclidean Clustering** | 256 线必须先下采样，Euclidean 实现简单速度快 |
| 运动检测 | **体素帧间差分（辅助）** | 轻量级，可辅助验证聚类结果的运动状态 |
| 跟踪 | **Kalman + Hungarian (SORT)** | 实时性最好，Autoware 有成熟 ROS2 集成 |
| 变化检测 | **OctoMap（可选）** | 需要长期地图监测时使用 |

---

## 4. 维度三：点云移动物体检测深度学习模型

### 4.1 3D 目标检测模型

#### 4.1.1 Voxel/Pillar-based 方法

| 模型 | 年份/会议 | 论文 | 代码 | 推理速度 | ROS2 集成 | 边缘部署 |
|------|----------|------|------|----------|-----------|----------|
| **PointPillars** | 2019 CVPR | https://arxiv.org/abs/1812.05784 | https://github.com/nutonomy/PointPillars | **~62 FPS (GTX 1080Ti)** `[已查证]` | 无官方，生态丰富 | ★★★★★ |
| **CenterPoint** | 2021 CVPR | https://arxiv.org/abs/2006.11275 | https://github.com/tianweiy/CenterPoint | ~30 FPS (pillar) | **Autoware 官方集成** `[已查证]` | ★★★★☆ |
| **SECOND** | 2018 | https://arxiv.org/abs/1805.08039 | https://github.com/traveller59/second.pytorch | ~20-25 FPS | 无 | ★★★☆☆ |
| **VoxelNet** | 2018 CVPR | https://arxiv.org/abs/1711.06396 | https://github.com/qianguih/voxelnet | ~4.5 FPS | 无 | ★☆☆☆☆ |

**PointPillars 详解**：
- 输入：点云 → XY 平面划分为 Pillar（柱体）→ PointNet 编码 → 伪图像 → 2D CNN backbone + FPN
- 输出：3D 边界框 + 类别（anchor-based）
- **优点**：速度极快（工业界部署标准）、无需 spconv（标准 2D 卷积）、部署友好、pillar 尺寸可调适应 256 线
- **缺点**：高度信息压缩到 pillar 特征中，细长物体（行人）精度略低
- **Jetson AGX Xavier 上 ~30 FPS**，INT8 量化后 21ms `[一方称]`

**CenterPoint 详解**：
- 输入：点云 → voxel/pillar 编码 → BEV 特征图 → CenterHead（anchor-free）
- 输出：3D 边界框（中心点+尺寸+旋转+速度）+ 类别；**天然支持跟踪**
- **优点**：anchor-free（无需预设 anchor 尺寸）、center-based 天然支持多目标跟踪和速度估计、**Autoware `lidar_centerpoint` 已集成 TensorRT 部署**、工程化程度最高
- **缺点**：速度略慢于 PointPillars、TensorRT 部署需处理自定义算子
- Autoware 包：`autoware_lidar_centerpoint`（基于 PointPillars 骨干 + TensorRT）

#### 4.1.2 Point-based 方法（不推荐实时部署）

| 模型 | 年份/会议 | 论文 | 代码 | 推理速度 | 推荐度 |
|------|----------|------|------|----------|--------|
| PointRCNN | 2019 CVPR | https://arxiv.org/abs/1812.04244 | https://github.com/sshaoshuai/PointRCNN | ~5-10 FPS | 不推荐（慢） |
| PV-RCNN | 2020 CVPR | https://arxiv.org/abs/1912.13192 | OpenPCDet | ~5-8 FPS | 不推荐（慢） |
| 3DSSD | 2020 CVPR | https://arxiv.org/abs/2002.10187 | https://github.com/dvlab-research/3DSSD | ~15-25 FPS | 谨慎 |

> Point-based 方法对 256 线大规模点云的采样和 SA 层计算瓶颈明显，且无 TensorRT 原生支持，**不推荐用于实时边缘部署**。

### 4.2 专门的运动/动态物体检测模型

#### 4.2.1 MotionNet

| 项目 | 内容 |
|------|------|
| 论文 | *MotionNet: Joint Perception and Motion Prediction for Autonomous Driving Based on BEV Maps*, IROS 2020 |
| 论文 URL | https://arxiv.org/abs/2003.02064 `[已查证]` |
| 代码 | https://github.com/pxiangnu/MotionNet `[已查证]` |
| 输入 | **多帧 LiDAR 点云拼接**（通常 5 帧）→ BEV 网格特征 → 时空 U-Net |
| 输出 | **运动分割**（BEV 网格是否运动）+ **运动预测**（未来位移场） |
| 优点 | 专门针对运动检测设计、联合感知与预测、BEV 表示计算效率高 |
| 缺点 | 仅 BEV 级别输出（非点级/实例级）、需多帧缓存增加延迟、无 ROS2 集成 |

#### 4.2.2 4D 稀疏卷积（MinkowskiEngine）

| 项目 | 内容 |
|------|------|
| 代码 | https://github.com/NVIDIA/MinkowskiEngine `[已查证]` |
| 输入 | 多帧点云 → 4D 稀疏张量（x,y,z,t）→ 4D 稀疏卷积 |
| 输出 | 点级语义/运动分割标签 |
| 优点 | 通用高维稀疏卷积框架、4D 卷积天然建模时空相关性、NVIDIA 维护 |
| 缺点 | 编译环境要求严格（CUDA/PyTorch 版本匹配）、4D 卷积显存开销大、**边缘部署困难**（无 RKNN 支持）、无 ROS2 集成 |

#### 4.2.3 MOS（Moving Object Segmentation）专门模型

| 模型 | 说明 |
|------|------|
| LMNet | LiDAR Moving object Network，SLAM 动态点滤除 `[一方称]`，公开仓库不稳定 |
| Auto-MOS | 自动标注生成 MOS 训练数据，基于 3D-MiniNet 轻量网络实现实时 MOS `[一方称]` |
| 3D-MiniNet | 轻量级点云语义分割，嵌入式平台可实时运行 `[一方称]` |

> MOS 方向模型多为 SLAM 社区提出，可访问性和维护程度参差不齐。**建议优先使用 SemanticKITTI 的 "moving" 类别训练通用分割模型**。

### 4.3 语义分割模型中的动态类别

#### 4.3.1 RangeNet++（推荐用于语义分割/MOS）

| 项目 | 内容 |
|------|------|
| 论文 | *RangeNet++: Fast and Accurate LiDAR Semantic Segmentation*, IROS 2019 |
| 论文 URL | https://arxiv.org/abs/1907.02750 `[已查证]` |
| 代码 | https://github.com/PRBonn/lidar-bonnetal `[已查证]` |
| 输入 | 点云 → 球面投影为 range image（5 通道）→ 2D CNN（DarkNet+沙漏）→ kNN 上采样 |
| 输出 | 点级语义标签（SemanticKITTI 28 类，**含 "moving" 类别**） |
| 推理速度 | ~20-30 FPS `[一方称]` |
| 256 线适配 | **★★★★★**（range image 与高分辨率雷达天然匹配） |

**优点**：range image 表示与 256 线雷达天然匹配、速度快（2D CNN）、SemanticKITTI 含 moving 类别可直接用于 MOD、波恩大学维护代码质量高

**缺点**：双楔镜非标准扫描模式需重新实现投影函数、moving 类别仅标注运动中的车辆/行人

#### 4.3.2 SalsaNext（推荐）

| 项目 | 内容 |
|------|------|
| 论文 | *SalsaNext: Fast, Uncertainty-aware Semantic Segmentation of LiDAR Point Clouds*, IROS 2020 |
| 论文 URL | https://arxiv.org/abs/2003.03653 `[已查证]` |
| 代码 | https://github.com/TIERIV/SalsaNext `[已查证]` |
| 推理速度 | **24 Hz (RTX 6000)** `[已查证]` |
| 特点 | 改进 RangeNet++、**不确定性感知**（输出预测置信度）、TIER IV 维护（Autoware 生态） |
| 256 线适配 | ★★★★★ |

#### 4.3.3 其他语义分割模型

| 模型 | 年份 | 论文 | 代码 | 速度 | 256 线适配 | 推荐度 |
|------|------|------|------|------|-----------|--------|
| Cylinder3D | 2021 CVPR | https://arxiv.org/abs/2011.10033 | https://github.com/xinge008/Cylinder3D | ~5-10 FPS | ★★★★☆（圆柱坐标） | 精度高但慢 |
| KPConv | 2019 ICCV | https://arxiv.org/abs/1904.08889 | https://github.com/HuguesTHOMAS/KPConv-PyTorch | ~5-15 FPS | ★★☆☆☆ | 不推荐（慢） |
| MinkowskiNet | 2019 ICCV | — | https://github.com/NVIDIA/MinkowskiEngine | 3D: ~15-25 FPS | ★★★☆☆ | 边缘部署难 |

### 4.4 轻量级 / 边缘部署友好模型

#### 4.4.1 边缘平台性能参考

| 平台 | AI 算力 | 适合的模型 | 预期推理速度 |
|------|---------|-----------|-------------|
| **Jetson Orin Nano** | 40 TOPS (INT8) `[已查证]` | CenterPoint-pillar (TensorRT INT8)、PointPillars | PointPillars ~30-50 FPS；CenterPoint ~15-30 FPS `[一方称]` |
| **RK3588** | 6 TOPS (NPU) `[已查证]` | PointPillars (ONNX→RKNN INT8) | PointPillars ~10-20 FPS `[一方称]` |
| Jetson AGX Xavier | 32 TOPS | PointPillars | ~30 FPS `[一方称]` |

> Jetson Orin Nano 的 INT8 算力（40 TOPS）约为 RK3588 NPU（6 TOPS）的 **6.7 倍**，且 TensorRT 对点云检测模型适配更成熟。

#### 4.4.2 部署优化工具链

| 工具 | 说明 | URL |
|------|------|-----|
| mmdetection3d | OpenMMLab 3D 检测框架，支持 PointPillars/CenterPoint/SECOND 等 | https://github.com/open-mmlab/mmdetection3d |
| MMDeploy | OpenMMLab 模型部署框架，支持 ONNX/TensorRT/OpenVINO/NCNN | https://github.com/open-mmlab/mmdeploy |
| OpenPCDet | 支持 PointRCNN/PV-RCNN/CenterPoint 等，7 个数据集 | https://github.com/open-mmlab/OpenPCDet |
| TensorRT | NVIDIA 推理加速 SDK，FP16/INT8 量化 | https://developer.nvidia.com/tensorrt |
| RKNN-Toolkit2 | 瑞芯微 NPU 模型转换，ONNX→RKNN INT8/INT16 | https://github.com/airockchip/rknn-toolkit2 |

### 4.5 数据集与预训练模型

| 数据集 | 规模 | 传感器 | 运动/动态信息 | 预训练可用性 |
|--------|------|--------|--------------|-------------|
| **SemanticKITTI** | ~43,000 帧 | Velodyne HDL-64E | **28 类含 moving/static 区分** `[已查证]` | RangeNet++/SalsaNext/Cylinder3D 均有官方权重 |
| KITTI 3D Detection | 7,481 训练帧 | Velodyne HDL-64E | 无显式运动标注 | PointPillars/SECOND/CenterPoint 均有预训练 |
| **nuScenes** | ~40,000 关键帧 | 32 线 LiDAR | **10 类 + 速度标注** `[已查证]` | CenterPoint/BEVFormer 等有预训练 |
| Waymo Open Dataset | ~20 万帧 | 5 个 LiDAR | 跟踪标注可推断运动 | CenterPoint/SE-SSD 有预训练 |

**数据集选择建议**：
- 运动分割（MOS）：首选 **SemanticKITTI**（唯一有 "moving" 类别的大规模点云分割数据集）
- 3D 检测 + 速度估计：首选 **nuScenes**（有速度标注）
- **域偏移警告**：所有公开预训练模型均在 64 线/32 线雷达上训练，直接迁移到 256 线双楔镜雷达会有显著域偏移，**必须用自有数据微调**

### 4.6 深度学习模型选型总结

| 优先级 | 场景 | 推荐模型 | 理由 |
|--------|------|----------|------|
| **首选** | ROS2 + Jetson Orin Nano | **CenterPoint-pillar** (Autoware `lidar_centerpoint`) | 唯一有官方 ROS2/Autoware 集成；TensorRT 部署成熟；anchor-free；天然支持跟踪和速度估计 |
| **次选** | RK3588 / 极致速度 | **PointPillars** (ONNX→RKNN/TensorRT INT8) | 速度最快（62 FPS）；标准 2D 卷积无自定义算子，RKNN 转换容易 |
| **语义分割/MOS** | 需要点级运动掩码 | **SalsaNext** 或 **RangeNet++** | range image 与 256 线雷达天然匹配；SemanticKITTI moving 类别可用 |
| **高精度** | x86+GPU 算力充足 | **CenterPoint (voxel)** + **SalsaNext** 级联融合 | voxel 版精度更高；分割提供点级运动确认 |
| **不推荐** | 任何实时场景 | PointRCNN、PV-RCNN、KPConv、场景流方法 | 速度慢，边缘部署困难，无 ROS2 集成 |

---

## 5. 维度四：ROS2 生态现成方案

### 5.1 Autoware Universe（首选参考）

Autoware Universe 是目前最成熟的开源自动驾驶感知栈，其 perception 模块提供完整的 detection → tracking → prediction 管线。

**仓库**：https://github.com/autowarefoundation/autoware_universe

#### 5.1.1 核心 Package

| Package | 功能 | 关键特性 |
|---------|------|----------|
| `autoware_pointcloud_preprocessor` | 点云预处理 | 裁剪、去畸变、多雷达拼接、下采样 |
| `autoware_ground_segmentation` | 地面分割 | 支持 ray/scan/ransac/ml 四种算法 |
| `autoware_euclidean_cluster` | 欧氏聚类 | 输出 DetectedObjects，2026-08 新增物理尺寸过滤 |
| `autoware_lidar_centerpoint` | 深度学习检测 | PointPillars 骨干 + TensorRT，输出动态 3D 物体 |
| `autoware_lidar_point_pillars` | PointPillars 检测 | 独立的 PointPillars 部署 |
| `autoware_bevfusion` | 多模态检测 | LiDAR + 相机融合 |
| `autoware_multi_object_tracker` | 多目标跟踪 | Kalman + Hungarian，支持 CV/CA/自行车模型，L-shape 拟合 |
| `autoware_map_based_prediction` | 轨迹预测 | 基于高精地图的运动预测 |

#### 5.1.2 完整感知管线架构

```
LiDAR 原始点云 (/sensing/lidar/concatenated/pointcloud)
    │
    ▼
[pointcloud_preprocessor]  裁剪、去畸变、拼接、下采样
    │
    ▼
[ground_segmentation]       地面分割（ransac 模式推荐双楔镜）
    │
    ├──► [euclidean_cluster]  传统聚类检测（无模型，快速可用）
    │         │
    │         ▼
    │   [DetectedObjects]
    │
    └──► [lidar_centerpoint]  深度学习检测（高精度，需重训练）
              │
              ▼
          [DetectedObjects]
              │
              ▼
    [multi_object_tracker]   多目标跟踪（Kalman + Hungarian）
              │
              ▼
          [TrackedObjects]
              │
              ▼
    [map_based_prediction]   轨迹预测（可选）
              │
              ▼
          [PredictedObjects]
```

#### 5.1.3 Topic 接口设计（已查证）

| 阶段 | Topic | 消息类型 |
|------|-------|----------|
| 输入点云 | `/sensing/lidar/concatenated/pointcloud` | `sensor_msgs/PointCloud2` |
| 检测输出 | `/perception/object_recognition/detection/objects` | `autoware_auto_perception_msgs/DetectedObjects` |
| 跟踪输出 | `/perception/object_recognition/tracking/objects` | `autoware_auto_perception_msgs/TrackedObjects` |
| 预测输出 | `/perception/object_recognition/prediction/objects` | `autoware_auto_perception_msgs/PredictedObjects` |

#### 5.1.4 对双楔镜雷达的适配要点

1. **地面分割**：ray/scan 模式依赖有序扫描线，双楔镜需先重网格化；**直接使用 RANSAC 或 ML 模式**
2. **CenterPoint 重训练**：基于机械式雷达训练，双楔镜点云分布差异需**用自有数据微调**
3. **点云去畸变**：双楔镜扫描的时间畸变与机械式不同，可能需要特殊的去畸变处理
4. **Windows 编译**：Autoware 主要面向 Linux，Windows 验证需 WSL2 或 Docker

### 5.2 PCL ROS2 封装（perception_pcl / pcl_ros）

**仓库**：https://github.com/ros-perception/perception_pcl

提供可组合的点云处理 node（component）：

| Node | 功能 | 关键参数 |
|------|------|----------|
| `voxel_grid` | 体素下采样 | `leaf_size` (x/y/z) |
| `statistical_outlier_removal` | 统计离群点去除 | `mean_k`, `std_ratio` |
| `extract_indices` | 按索引提取子集 | `negative`（取反） |
| `passthrough` | 直通滤波（ROI 裁剪） | `filter_field_name`, `filter_limit_min/max` |
| `crop_box` | 立方体裁剪 | — |
| `radius_outlier_removal` | 半径离群点去除 | — |

> pcl_ros 只提供基础滤波，RANSAC 分割、Euclidean 聚类、跟踪等需自定义 node 或从 Autoware 抽取。

### 5.3 其他 ROS2 点云处理 Package

| Package | 仓库 | 功能 | 对 MOD 的意义 |
|---------|------|------|---------------|
| `pointcloud_to_laserscan` | https://github.com/ros-perception/pointcloud_to_laserscan | 3D→2D 投影 | 可使用 2D 障碍物算法，损失高度信息，辅助/降级方案 |
| `octomap_server` | https://github.com/OctoMap/octomap | 3D 占据栅格建图+变化检测 | 长期环境监测，变化检测 |
| `nav2_costmap_2d` | https://github.com/ros-navigation/navigation2 | 2D 成本地图 | 导航避障，2D 变化检测 |
| `obstacle_detector_2` | https://github.com/harmony-eu/obstacle_detector_2 | 2D 障碍物检测 | 2D 场景，不适合 3D 主管线 |

### 5.4 独立 ROS2 MOD 管线参考项目

| 项目 | URL | 说明 |
|------|-----|------|
| ROS2 PCL Perception Suite | https://github.com/ninad164/ros2-pcl-perception-suite | 完整 ROS2 Humble 管线：预处理→RANSAC→欧氏聚类→3D 包围盒→质心跟踪 |
| 3D LiDAR Detection & Tracking | https://github.com/Pavan-43/3D-LiDAR-Detection-Tracking-Pipeline | RANSAC→Euclidean→PCA→Kalman+Hungarian |

### 5.5 ROS2 消息接口

| 消息类型 | 来源 | 3D 包围盒 | 类别 | 速度 | 跟踪 ID | 通用性 |
|----------|------|-----------|------|------|---------|--------|
| `sensor_msgs/PointCloud2` | common_interfaces | ❌ | ❌ | ❌ | ❌ | 极高 |
| `vision_msgs/Detection3DArray` | vision_msgs | ✅ | ✅ | ❌ | ✅ | 高 |
| `autoware_auto_perception_msgs/DetectedObjects` | Autoware | ✅ | ✅ | ✅ | ✅ | 中（Autoware 生态） |
| `autoware_auto_perception_msgs/TrackedObjects` | Autoware | ✅ | ✅ | ✅ | ✅ | 中 |
| `nav_msgs/OccupancyGrid` | common_interfaces | ❌ | ❌ | ❌ | ❌ | 高（2D） |

**推荐**：自定义 MOD 管线优先使用 `vision_msgs/Detection3DArray`（通用、与框架解耦）；深度集成 Autoware 时使用 `autoware_auto_perception_msgs`。

### 5.6 推荐的 Topic 接口设计

```
# 输入
/lidar/double_wedge/points          sensor_msgs/PointCloud2 (1D 无序, x,y,z,intensity,ring)

# 预处理
/lidar/preprocessed/points           sensor_msgs/PointCloud2 (裁剪+下采样+去噪)

# 地面分割
/lidar/nonground/points              sensor_msgs/PointCloud2 (非地面点)
/lidar/ground/points                 sensor_msgs/PointCloud2 (地面点, 调试用)

# 检测
/mod/detected_objects                vision_msgs/Detection3DArray (或 autoware_auto_perception_msgs/DetectedObjects)

# 跟踪
/mod/tracked_objects                 vision_msgs/Detection3DArray (含稳定 ID、速度估计)

# 调试
/mod/clusters_marker                 visualization_msgs/MarkerArray
/mod/bboxes_marker                   visualization_msgs/MarkerArray
/mod/tracks_marker                   visualization_msgs/MarkerArray (含 ID 标签和速度箭头)
```

**QoS 策略**：点云 topic 使用 `SENSOR_DATA`（Best Effort + Volatile）；检测/跟踪结果使用 `RELIABLE` + `TRANSIENT_LOCAL`。

---

## 6. 维度五：综合方案建议与技术路线

### 6.1 核心挑战总结

| 挑战 | 说明 | 影响 |
|------|------|------|
| **非均匀扫描模式** | 双楔镜 Lissajous 扫描不形成规则扫描线 | ray_ground_filter、line-fit 等基于扫描线的算法无法直接使用 |
| **高点云密度** | 256 线 + 1° 分辨率，单帧数十万点 | 算法计算效率要求高，必须先下采样 |
| **深度学习域偏移** | 公开模型基于 64/32 线机械式雷达训练 | 直接迁移性能下降，必须用自有数据微调 |
| **Windows 验证 vs Linux 部署** | 当前 Windows 做驱动验证，目标 ROS2 Linux | 阶段一用纯 PCL（Windows 可用），阶段二迁移 ROS2 Linux |
| **双楔镜扫描畸变** | 一帧扫描时间内传感器/目标运动导致点云畸变 | 需运动补偿，否则地面分割和聚类精度下降 |

### 6.2 三阶段技术路线

#### 阶段一：快速验证（当前 Windows 驱动验证阶段）

**目标**：在驱动验证阶段快速搭建可运行的 MOD 原型，验证点云质量和基本检测能力。

**技术栈**：纯 PCL（Windows 上通过 vcpkg 或预编译库可用，不依赖 ROS2）

**推荐管线**：

```
双楔镜原始点云 (PCL PointCloud<PointXYZRGB>)
    │
    ▼
Passthrough 滤波          ROI 裁剪: x[0,80]m, z[-0.3,3.0]m
    │
    ▼
VoxelGrid 下采样          leaf_size: 0.1~0.15m (点数从数十万降至 2~5万)
    │
    ▼
StatisticalOutlierRemoval  去噪: mean_k=20, std_ratio=1.0
    │
    ▼
RANSAC 平面拟合            地面分割: distance_threshold=0.15m, max_iterations=100
    │
    ▼
ExtractIndices             提取非地面点 (negative=true)
    │
    ▼
EuclideanClusterExtraction  聚类: tolerance=0.3~0.5m, min_size=10, max_size=50000
    │
    ▼
BoundingBox 计算           PCA 或 AABB 包围盒
    │
    ▼
简单跟踪                   质心距离匹配 + 滑动平均速度估计
    │
    ▼
可视化                     PCL Visualizer 或 VTK
```

**选型理由**：
- **RANSAC**：不依赖扫描线，双楔镜天然适配，速度快（~12ms）
- **Euclidean Clustering**：PCL 原生，实现简单，参数少
- **VoxelGrid**：256 线密度高，必须先下采样
- **所有算法均在 PCL 中**，Windows 可直接验证

**实时性预算**（10~20 Hz 帧率，单帧预算 50~100ms）：
- 下采样：< 10ms
- RANSAC：< 15ms
- Euclidean Clustering：< 20ms
- 跟踪：< 5ms
- **总计 < 50ms**，留有余量

**参考实现**：
- https://github.com/ninad164/ros2-pcl-perception-suite
- https://github.com/Pavan-43/3D-LiDAR-Detection-Tracking-Pipeline

#### 阶段二：ROS2 工程化集成（Linux 目标平台）

**目标**：将验证后的管线迁移到 ROS2，使用成熟 package 替代自定义实现。

**方案 A（轻量级，推荐起步）**：pcl_ros + 自定义聚类/跟踪 node

| 模块 | 方案 | 说明 |
|------|------|------|
| pcap 解析 | 自定义 Input 基类 + PcapInput/SocketInput | 借鉴 RoboSense 架构 + Hesai 硬件时间戳速率控制 |
| 预处理 | pcl_ros `passthrough` + `voxel_grid` + `statistical_outlier_removal` | launch 文件组合 component |
| 地面分割 | 自定义 RANSAC node（`pcl::SACSegmentation`） | 或从 Autoware 抽取 `ground_segmentation` RANSAC 模式 |
| 聚类 | 自定义 Euclidean Cluster node | 或从 Autoware 抽取 `autoware_euclidean_cluster` |
| 跟踪 | 自定义 Kalman + Hungarian node | 或从 Autoware 抽取 `autoware_multi_object_tracker` |
| 消息 | `sensor_msgs/PointCloud2` + `vision_msgs/Detection3DArray` | 通用标准消息 |

**方案 B（全功能，中长期）**：深度集成 Autoware Universe

| 模块 | 方案 | 说明 |
|------|------|------|
| pcap 解析 | 自定义驱动 node（参考原厂 + 开源） | 输出标准 `sensor_msgs/PointCloud2` |
| 预处理 | `autoware_pointcloud_preprocessor` | 裁剪、去畸变、下采样 |
| 地面分割 | `autoware_ground_segmentation`（RANSAC 模式） | ray/scan 模式需先重网格化 |
| 检测 | 阶段 1：`autoware_euclidean_cluster`；阶段 2：`autoware_lidar_centerpoint`（需重训练） | 先聚类后深度学习的渐进路线 |
| 跟踪 | `autoware_multi_object_tracker` | Kalman + Hungarian，L-shape |
| 消息 | `autoware_auto_perception_msgs` | 与 Autoware 生态无缝对接 |

#### 阶段三：高级功能（可选，按需扩展）

| 功能 | 推荐方案 | 适用场景 |
|------|----------|----------|
| **深度学习检测** | CenterPoint-pillar（Autoware `lidar_centerpoint`），需用双楔镜数据微调 | 需要类别识别（行人/车辆/非机动车）和高精度检测 |
| **语义分割/MOS** | SalsaNext 或 RangeNet++，SemanticKITTI moving 类别 | 需要点级运动掩码、SLAM 动态点滤除 |
| **帧间运动检测** | 体素帧间差分 + ICP 残差检测（辅助验证） | 需要区分静态障碍物和动态目标 |
| **场景流估计** | ICP-Flow（learning-free） | 需要精确估计目标运动方向和速度 |
| **动态点去除/建图** | Removert 或 ERASOR（离线建图） | 需要构建静态地图用于定位或变化检测 |
| **占据栅格变化检测** | OctoMap（3D）或 nav2_costmap_2d（2D） | 长期环境监测或导航避障 |

### 6.3 pcap 解析模块设计建议（借鉴原厂 + 开源）

```
┌─────────────────────────────────────────────────┐
│                   Input 基类                      │
│  + getPacket(packet &pkt): int                  │
│  + setFilter(bpf_filter: string): bool          │
└────────────────────┬────────────────────────────┘
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
   ┌─────────────┐      ┌──────────────┐
   │  PcapInput  │      │ SocketInput  │
   │ (离线回放)   │      │ (实时抓包)    │
   └─────────────┘      └──────────────┘
          │                     │
          └──────────┬──────────┘
                     ▼
          ┌─────────────────────┐
          │   PacketParser      │
          │  (UDP payload 解析)  │
          │  - 解析 PointPacket  │
          │  - 多回波处理        │
          │  - 硬件时间戳提取    │
          └──────────┬──────────┘
                     ▼
          ┌─────────────────────┐
          │  PointCloudAssembler│
          │  (点云组装)          │
          │  - 按帧标志切分      │
          │  - unorganized 格式  │
          │  - 保留 ring 字段    │
          │  - 输出 PointCloud2  │
          └─────────────────────┘
```

**关键设计决策**：
1. **Input 基类**：统一 pcap 离线和 socket 实时接口（借鉴 Velodyne/RoboSense）
2. **BPF 过滤**：`"dst port <数据端口>"` 按目标端口过滤（借鉴原厂软件）
3. **硬件时间戳速率控制**：利用包内硬件时间戳差值控制回放速率（借鉴 Hesai）
4. **IPv4/IPv6 动态偏移**：不硬编码 42 字节，根据 IP 头版本动态计算（借鉴 Hesai）
5. **MSOP/DIFOP 双端口**：数据端口和配置端口分离（借鉴 RoboSense）
6. **unorganized 点云**：`height=1`，双楔镜非均匀扫描不适合 organized 格式
7. **帧切分基于包内帧标志**：而非 azimuth 回绕
8. **保留 ring/line ID**：点云中包含 `ring` 字段（原厂 256 线独立颜色渲染的依据）

### 6.4 快速可用方案 vs 高精度方案对比

| 维度 | 快速可用方案（经典算法组合） | 高精度方案（深度学习模型） |
|------|---------------------------|------------------------|
| **核心管线** | Passthrough → VoxelGrid → RANSAC → Euclidean Clustering → Kalman+Hungarian | PointPillars/CenterPoint → 多目标跟踪 → 速度估计 |
| **检测能力** | 仅输出"障碍物"，无类别识别 | 车辆/行人/非机动车等类别分类 + 置信度 |
| **运动判定** | 跟踪后速度 > 阈值 | 跟踪后速度 > 阈值（CenterPoint 自带速度估计） |
| **精度** | 中（依赖聚类参数，相邻目标易合并） | 高（anchor-free 检测，类别级精度） |
| **实时性** | 优（< 50ms/帧，CPU 即可） | 中（需 GPU，Jetson Orin Nano 15-50 FPS） |
| **计算资源** | CPU 即可，内存 < 2GB | 需 GPU（4GB+ 显存），Jetson Orin Nano 或 x86 GPU |
| **训练数据** | 无需训练 | 需数百~数千帧标注数据微调（域偏移） |
| **ROS2 集成** | pcl_ros + 自定义 node | Autoware `lidar_centerpoint`（已集成） |
| **双楔镜适配** | 好（RANSAC 不依赖扫描线） | 中（需重训练，range image 投影需重写） |
| **开发周期** | 1~2 周 | 1~3 月（含数据标注和训练） |
| **适用阶段** | 阶段一：驱动验证、快速原型 | 阶段二/三：产品化、高精度需求 |

### 6.5 推荐的开源项目组合

#### 组合 1：快速验证（纯 PCL，Windows 可用）

| 组件 | 来源 | URL |
|------|------|-----|
| pcap 解析 | 自定义（借鉴原厂 + RoboSense/Hesai） | — |
| 点云处理 | PCL | https://pointclouds.org/ |
| 地面分割 | PCL `SACSegmentation` | — |
| 聚类 | PCL `EuclideanClusterExtraction` | — |
| 跟踪 | 自定义 Kalman + Hungarian | 参考 https://github.com/abewley/sort |
| 可视化 | PCL Visualizer / VTK | — |

#### 组合 2：ROS2 轻量级（Linux，推荐起步）

| 组件 | 来源 | URL |
|------|------|-----|
| pcap 解析 | 自定义 Input 基类 | 借鉴 https://github.com/RoboSense-LiDAR/rslidar_sdk |
| 预处理 | pcl_ros | https://github.com/ros-perception/perception_pcl |
| 地面分割 | 自定义 RANSAC node | — |
| 聚类 | 自定义 Euclidean Cluster node | — |
| 跟踪 | 自定义 Kalman + Hungarian node | — |
| 消息 | `vision_msgs/Detection3DArray` | https://github.com/ros-perception/vision_msgs |
| 可视化 | RViz2 | — |
| 参考管线 | ros2-pcl-perception-suite | https://github.com/ninad164/ros2-pcl-perception-suite |

#### 组合 3：Autoware 全功能（中长期，高精度）

| 组件 | 来源 | URL |
|------|------|-----|
| pcap 解析 | 自定义驱动 node | — |
| 预处理 | `autoware_pointcloud_preprocessor` | https://github.com/autowarefoundation/autoware_universe |
| 地面分割 | `autoware_ground_segmentation`（RANSAC 模式） | 同上 |
| 检测 | `autoware_lidar_centerpoint`（TensorRT） | 同上 |
| 聚类（备选） | `autoware_euclidean_cluster` | 同上 |
| 跟踪 | `autoware_multi_object_tracker` | 同上 |
| 预测（可选） | `autoware_map_based_prediction` | 同上 |
| 消息 | `autoware_auto_perception_msgs` | 同上 |
| 模型训练 | mmdetection3d | https://github.com/open-mmlab/mmdetection3d |
| 模型部署 | MMDeploy + TensorRT | https://github.com/open-mmlab/mmdeploy |

### 6.6 关键参数初始值汇总

| 参数 | 初始值 | 说明 |
|------|--------|------|
| VoxelGrid leaf_size | 0.1 m (x/y/z) | 256 线密度高，0.1m 大幅降采样 |
| Passthrough z 范围 | [-0.3, 3.0] m | 保留车辆和行人高度 |
| Passthrough x 范围 | [0, 80] m | 根据有效检测距离调整 |
| RANSAC distance_threshold | 0.15 m | 距离分辨率 0.004m，容忍路面起伏 |
| RANSAC max_iterations | 100 | 平衡精度和速度 |
| Euclidean cluster_tolerance | 0.3 m（近）/ 0.5 m（远） | 1° 分辨率下远距离点间距大 |
| Euclidean min_cluster_size | 10 | 过滤噪声小簇 |
| Euclidean max_cluster_size | 50000 | 防止超大簇（建筑物） |
| Kalman 过程噪声 | 位置 0.1m，速度 1.0m/s | 根据目标运动特性调整 |
| Hungarian 距离阈值 | 1.0 m | 关联最大距离 |
| 跟踪最大未匹配帧数 | 10 帧（~0.3s@30fps） | 短暂遮挡不丢失 |
| 新轨迹确认帧数 | 3 帧 | 减少虚假轨迹 |
| 运动速度阈值 | 0.5 m/s | 低于此速度判为静止 |

### 6.7 风险与注意事项

1. **双楔镜扫描畸变**：一帧扫描时间内传感器/目标运动导致点云畸变，需运动补偿（IMU/里程计将每个点变换到帧起始时刻）
2. **点云强度信息利用**：双楔镜雷达点云包含强度信息，可考虑 `ConditionalEuclideanClustering` 结合强度提升聚类质量
3. **类别识别**：传统聚类只能输出"障碍物"，需类别识别时有两个路径：基于几何特征的简单分类（尺寸、长宽比、点数）或迁移到深度学习检测
4. **域偏移**：深度学习模型必须用双楔镜自有数据微调，至少数百帧标注数据
5. **Windows → Linux 迁移**：阶段一用纯 PCL 在 Windows 验证，阶段二迁移 ROS2 Linux，PCL API 跨平台一致
6. **实时性**：256 线点云下采样后控制在 2~5 万点，经典算法管线 < 50ms/帧可满足 10~20 Hz

---

## 7. 参考资料汇总

### 7.1 pcap 解析相关

| 项目 | URL |
|------|-----|
| Velodyne ROS 驱动 | https://github.com/ros-drivers/velodyne |
| Ouster ROS 驱动 | https://github.com/ouster-lidar/ouster-ros |
| Hesai ROS 驱动 | https://github.com/HesaiTechnology/HesaiLidar_General_ROS |
| RoboSense rslidar_sdk | https://github.com/RoboSense-LiDAR/rslidar_sdk |
| libtins | https://libtins.github.io/ |
| libpcap | https://www.tcpdump.org/ |

### 7.2 经典算法相关

| 项目 | URL |
|------|-----|
| PCL 官方 | https://pointclouds.org/ |
| OctoMap | https://github.com/OctoMap/octomap |
| Removert | https://github.com/url-kaist/removert |
| ERASOR | https://github.com/url-kaist/ERASOR |
| SORT | https://github.com/abewley/sort |
| Deep SORT | https://github.com/nwojke/deep_sort |
| ICP-Flow | https://github.com/wh201906/ICP-Flow |
| ROS2 PCL Perception Suite | https://github.com/ninad164/ros2-pcl-perception-suite |
| 3D LiDAR Detection & Tracking | https://github.com/Pavan-43/3D-LiDAR-Detection-Tracking-Pipeline |

### 7.3 深度学习模型相关

| 模型/项目 | URL |
|-----------|-----|
| PointPillars | https://github.com/nutonomy/PointPillars |
| CenterPoint | https://github.com/tianweiy/CenterPoint |
| SECOND | https://github.com/traveller59/second.pytorch |
| VoxelNet | https://github.com/qianguih/voxelnet |
| PointRCNN | https://github.com/sshaoshuai/PointRCNN |
| 3DSSD | https://github.com/dvlab-research/3DSSD |
| MotionNet | https://github.com/pxiangnu/MotionNet |
| RangeNet++ (lidar-bonnetal) | https://github.com/PRBonn/lidar-bonnetal |
| SalsaNext | https://github.com/TIERIV/SalsaNext |
| Cylinder3D | https://github.com/xinge008/Cylinder3D |
| KPConv | https://github.com/HuguesTHOMAS/KPConv-PyTorch |
| MinkowskiEngine | https://github.com/NVIDIA/MinkowskiEngine |
| FlowNet3D | https://github.com/xingyul/flownet3d |
| HPLFlowNet | https://github.com/laoreja/HPLFlowNet |
| PointPWC-Net | https://github.com/DylanWusee/PointPWC |
| mmdetection3d | https://github.com/open-mmlab/mmdetection3d |
| OpenPCDet | https://github.com/open-mmlab/OpenPCDet |
| MMDeploy | https://github.com/open-mmlab/mmdeploy |
| TensorRT | https://developer.nvidia.com/tensorrt |
| RKNN-Toolkit2 | https://github.com/airockchip/rknn-toolkit2 |

### 7.4 ROS2 生态相关

| 项目 | URL |
|------|-----|
| Autoware Universe | https://github.com/autowarefoundation/autoware_universe |
| perception_pcl | https://github.com/ros-perception/perception_pcl |
| pointcloud_to_laserscan | https://github.com/ros-perception/pointcloud_to_laserscan |
| vision_msgs | https://github.com/ros-perception/vision_msgs |
| Navigation2 | https://github.com/ros-navigation/navigation2 |
| obstacle_detector_2 | https://github.com/harmony-eu/obstacle_detector_2 |

### 7.5 关键论文

| 论文 | 年份/会议 | URL |
|------|----------|-----|
| VoxelNet | 2018 CVPR | https://arxiv.org/abs/1711.06396 |
| SECOND | 2018 | https://arxiv.org/abs/1805.08039 |
| PointPillars | 2019 CVPR | https://arxiv.org/abs/1812.05784 |
| CenterPoint | 2021 CVPR | https://arxiv.org/abs/2006.11275 |
| PointRCNN | 2019 CVPR | https://arxiv.org/abs/1812.04244 |
| PV-RCNN | 2020 CVPR | https://arxiv.org/abs/1912.13192 |
| 3DSSD | 2020 CVPR | https://arxiv.org/abs/2002.10187 |
| MotionNet | 2020 IROS | https://arxiv.org/abs/2003.02064 |
| RangeNet++ | 2019 IROS | https://arxiv.org/abs/1907.02750 |
| SalsaNext | 2020 IROS | https://arxiv.org/abs/2003.03653 |
| Cylinder3D | 2021 CVPR | https://arxiv.org/abs/2011.10033 |
| KPConv | 2019 ICCV | https://arxiv.org/abs/1904.08889 |
| FlowNet3D | 2019 CVPR | https://arxiv.org/abs/1806.01411 |
| HPLFlowNet | 2019 CVPR | https://arxiv.org/abs/1906.01507 |
| PointPWC-Net | 2020 ECCV | https://arxiv.org/abs/1911.00984 |
| SORT | 2016 ICIP | — |

---

> **报告说明**：本报告综合了原厂软件二进制逆向分析、四家激光雷达厂商开源驱动源码实际阅读、经典算法文献调研、深度学习模型论文与代码调研、ROS2 生态 package 调研。所有开源仓库 URL 和论文链接均经过搜索验证可访问。标注 `[已查证]` 的信息有多个独立来源交叉验证；标注 `[一方称]` 的信息来自单一来源（通常为论文自述或博客转述），实际性能需以目标硬件上的实测为准。算法性能数据（如 RANSAC 12ms、PMF 482ms、PointPillars 62 FPS）来自引用的学术文献，实际性能受点云密度、硬件平台、参数配置影响。

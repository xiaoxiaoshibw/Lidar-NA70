# NA70 现状架构分析（Goal Tracking Demo — Phase 0 产出）

> 生成日期：2026-09-17。本文档是 `/init` 任务 Phase 0 的产出：在不修改任何代码的前提下，
> 描述当前真实数据流，标出可复用 / 需新增 / 不应修改的模块，并给出风险清单。
> 配套实施计划见 `docs/GOAL_TRACKING_IMPLEMENTATION_PLAN.md`。

## 1. 当前真实数据流

```text
                    ┌─ Windows 本机（开发 / 离线回放，当前主线）─────────────────┐
                    │                                                            │
  NA70 LiDAR        │  经典 PCAP (UDP 2368, 1410B 载荷 = 200 点 + 尾部)            │
    │               │      │ POST /api/captures （原始字节流上传）                  │
    │               │      ▼                                                      │
    │  UDP 包        │  backend/decoder.py::scan_pcap                              │
    │  (设备直发)    │      索引：每 100 有效包 = 1 帧（≈10 Hz，20000 射线/帧）        │
    │               │      frames.json + meta.json 落盘 web/data/<uuid>/           │
    │               │      点云不落盘，按需 mmap 重读解码                            │
    │               │      ▼                                                      │
    │               │  GET /api/captures/{id}/frames/{index}                      │
    │               │      帧二进制 v1：u32 头长 + JSON 头 + XYZI float32 (N,4)     │
    │               │      ▼                                                      │
    │               │  前端 React 19 + Three.js（无路由库，单页 App）                │
    │               │      RAW / ODOMETRY / MAP 三视图；全部 HTTP 轮询，无 WebSocket │
    │               │                                                            │
    │               │  离线任务线：POST /api/mapping/jobs → mapping.py              │
    │               │      (GICP/NDT 配准 + VoxelMap 体素融合)                      │
    └────────────────────────────────────────────────────────────────────────────┘

                    ┌─ Jetson Orin Nano 4GB（部署目标，验证环境 WSL2 Humble）──────┐
                    │  ROS2/src/double_wedge_driver (C++)                         │
                    │    订 UDP 2368 → /lidar_data (LidarScanBundle, 100 小云)     │
                    │  test_lidar_driver.cpp 合帧节点                              │
                    │    /lidar_data → /inno_lidar_cloud (PointCloud2, 20000 点)   │
                    │  ROS2/src/na70_motion_detector (Python, 已退出主流程)         │
                    │    订 /inno_lidar_cloud → 运动点 mask（背景差分）              │
                    └────────────────────────────────────────────────────────────┘
```

两条链路（Windows Web 后端 / ROS2）**互不依赖、无共享代码**。解码协议的唯一权威参考是
`ROS2/src/double_wedge_driver/src/inno_device_lidar_node.cpp` 与合帧节点 `test_lidar_driver.cpp`。

## 2. 数据实测（2026-09-14 操场右 PCAP）

`radar_capture_data/2/9.14_caochangyou.pcap`（170 MB）探针结果：

| 指标 | 值 |
|---|---|
| 帧数 / 时长 | 1211 帧 / 121.0 s（≈10 Hz） |
| 单帧射线 | 20000（100 包 × 200 点），有效回波 1.4k–7.4k 点 |
| 远距离带 80–150 m | 前 10 帧累计 18692 点（单帧 76–4563 点，增强 3–10 倍以上） |
| 100–120 m 档 | 单帧 6–1308 点 |
| 球门候选结构 | 正前方 x≈115 m，y∈[-2, 4]，z∈[-1.5, ~6] m 的致密宽幅结构（含网/围栏回波）；x≈133 m 另有一处次级结构 |

结论：**10 帧叠加对 100 m 级稀疏结构的增强是实质性的**，球门框架特征（立柱 + 横梁）
可从叠加云中提取；但 115 m 处结构混有围栏/看台回波，检测算法必须容忍环境杂波，
阈值全部进 YAML 可调。

## 3. 各能力现状核对（对照任务清单第 3 节）

| 能力 | 现状 | 位置 |
|---|---|---|
| 点云缓存 | 有（pcap 落盘 + 帧索引 JSON + 64 帧/采集 LRU，点云按需解码） | `app.py:107-123`、`web/data/<uuid>/` |
| 多帧叠加 | **前端 RAW 视图有纯叠加（无运动补偿，1–500 帧窗口）**；后端无独立叠加模块 | `main.tsx`、`CloudView.tsx` |
| voxel filter | 有（体素质心降采样 + 统计离群剔除） | `mapping.py:232-279` |
| ROI | 有（mapping config `roi` 字段，默认 null） | `mapping.py:225-228` |
| 聚类 | 有 DBSCAN（历史运动分析用，体素化加权） | `motion.py:205`、`spatial.py` |
| MapMOS / 运动检测 | 已退出主流程，链路保留、入口移除 | `motion.py`、`bands.py` |
| Tracking | 历史运动分析有匈牙利关联 + 常速 Kalman（目标级跟踪在 mapping 主线无） | `motion.py:208-252` |
| 配准 | point-to-point/point-to-plane ICP、SE(2) GICP、NDT（无 IMU，扫描间配准） | `mapping.py:334-385`、`registration_se2.py` |
| VoxelMap 体素融合 | 有（体素哈希，质心/强度/观测计数） | `mapping.py:472-638` |
| WebSocket | **无**。前后端全部 HTTP 轮询 + 二进制帧 | — |
| `/demo/` 路由 | **无**。前端无路由库，单页 App + 静态挂载 `web/frontend/dist` | `app.py:752-754` |
| ROS2 多帧叠加 | 仅 100 包合 1 帧；无跨帧叠加/体素滤波/跟踪 | — |
| 配置管理 | 各模块自读同目录 `*.yaml`（JSON 语法子集），API 白名单覆盖，无统一加载器 | `mapping.py:59-150` 等 |

## 4. 可以复用的模块

| 模块 | 复用方式 |
|---|---|
| `backend/decoder.py` | 直接使用：按帧索引解码 XYZI 点云，是叠加与检测的数据入口 |
| `mapping.py::downsample / clean / preprocess` | 直接调用：体素降采样、ROI 裁剪、离群剔除（只 import 函数，不动 mapping 主线） |
| `mapping.py::VoxelMap` | 参考实现；若叠加需要累积语义可借用，第一版滑动窗口叠加用更简单的 merge + downsample |
| `motion.py` 的 DBSCAN、匈牙利 + Kalman 跟踪模式 | 作为 goal tracker 的算法参考（新实现保持独立、参数进 YAML，不 import motion 主线以免带出历史包袱） |
| 帧二进制 v1 格式 + `app.py` 帧缓存模式 | Demo 的点云下发沿用同一格式，前端 `api.ts::readFrame` 直接复用 |
| 前端 `CloudView.tsx` Three.js 管线 | Demo 页面复用其点云渲染/着色器/相机方案（提取或引用，不改动现有导出） |
| `test_decoder.py` 合成 pcap 构造、`test_mapping.py::room/observe` | 单测造数据模式 |
| ROS2 `motion_core.py` 的"算法核心与 ROS 封装分离"模式 | 新模块遵循同样纪律：核心 numpy-only，可离线单测，ROS2 封装后置 |

## 5. 需要新增的模块

1. **10 帧滑动窗口叠加器**（FrameBuffer + Accumulator）：后端核心模块，窗口大小/体素尺寸参数化，静止雷达用简单合并 + 体素降采样，预留配准钩子。
2. **goal_detection 包**（独立目录）：球门候选检测（立柱/横梁几何匹配）、状态机（SEARCHING→CANDIDATE→LOCKING→LOCKED→TRACKING→LOST→REACQUIRE）、goal tracker（质心 + 几何匹配，可选 Kalman）。
3. **Demo 后端 API**：叠加/检测/跟踪状态查询接口 + 叠加点云下发（复用 v1 二进制格式），接入现有 FastAPI app，不建独立服务器。
4. **Demo 前端页面 `/demo/`**：独立入口（服务端按路径返回 demo 页，或前端按 hash 路由），Three.js 点云 + 标注 + 状态面板 + 单帧/叠加切换 + `?debug=1` 调试图层。
5. **配套测试**：`test_frame_buffer`、`test_accumulation`、`test_goal_candidate`、`test_goal_geometry`、`test_goal_lock`、`test_goal_tracking`、`test_goal_lost`、`test_goal_reacquire`。

## 6. 不应该修改的模块

- `backend/decoder.py`：协议权威实现，只调用不改。
- `backend/mapping.py` / `mapping_jobs.py`：R7 主线（点云里程计 + 体素地图融合），goal demo 只 import 不修改。
- `backend/motion.py` / `bands.py` / `spatial.py`：历史运动分析，已退出主流程，保持原样。
- `ROS2/src/double_wedge_driver/**`：驱动与协议参考，禁止改动。
- `backend/mapping.yaml`、`preflight.yaml`：主线阈值文件，demo 阈值放新文件。
- 现有 RAW/ODOMETRY/MAP 视图与播放器行为：`/demo/` 是新增入口，不动现有页面。

## 7. 潜在风险

1. **「实时」语义**：当前后端是纯离线导入模型（无 UDP、无 WebSocket、无 ROS 桥）。Demo 的"实时"在本阶段定义为 **PCAP 按真实帧率（≈10 Hz）回放驱动的在线管线**；真正实时接入（UDP 订阅 / ROS2 节点 / WebSocket 推送）属于后续阶段。若直接照搬任务书假设的 WebSocket 链路会凭空造出一套重复服务器，违反复用原则。
2. **回放计算预算**：每帧解码 20000 射线 + 叠加 + 检测在 Python/numpy 下约几十 ms，10 Hz 可行；但叠加窗口体素降采样、检测聚类、前端渲染三者叠加需在 Phase 8 用 benchmark 实测（`tests/benchmark.py` 已有基准模式可扩展）。
3. **115 m 结构含围栏/看台杂波**：密集网状回波可能淹没立柱特征；检测必须以"两立柱 + 横梁 + 尺度"的几何联合打分而非单纯点密度，且 `range.min/max`、柱间距、高度容差全部 YAML 化以便按场地调参。
4. **拖影**：`9.14_caochangyou` 系列录制中雷达存在轻微时变 yaw（README 已记录左/右文件待补偿）。叠加会产生拖影，立柱会被拉斜——检测器对柱宽（半径容差）要放宽，后续阶段可挂 mapping 的 SE(2)/yaw 配准做补偿（`temporal_yaw.py` 已有估计器）。
5. **内存**：10 帧 × 20000 点 float32 仅约 3.2 MB，安全；风险在"整包模式"式的无界缓存——叠加器必须是固定环形窗口，检测输出只留目标级状态，不缓存原始点历史。
6. **前端重复渲染管线**：`CloudView.tsx` 与 `App` 耦合（视图模式、失败帧、建图 job 逻辑），抽公共组件有回归风险。Demo 第一版倾向独立轻量 viewer（复用着色器与相机参数），不重构现有组件。
7. **与主计划的关系**：本项目 `Plan/MASTER_开发与验收总计划.md` 主线是 R0–R8 里程计 + 建图；goal tracking demo 是用户新增的独立功能线，不得阻塞或改写 R7 证据纪律（真实数据证据不加 SYNTHETIC 标记、失败事实保留）。

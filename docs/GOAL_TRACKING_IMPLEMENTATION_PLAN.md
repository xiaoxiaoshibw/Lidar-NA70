# NA70 无 IMU 10 帧叠加 + 足球球门识别锁定跟踪 — 实施计划

> 2026-09-21 最新路线决策：近期主线恢复为 [足球门识别主线开发计划](../Plan/足球门识别主线开发计划.md)。按 G0 可信真值、G1 高度基准、G2 人工 ROI 含网面几何、G3 全场搜索、G4 跟踪、G5 冻结验收推进；第一轮只执行 G0–G1。细线 W1 工具和证据保留但当前暂停。

> [GOAL P3 真实球门检测修复执行计划](../Plan/GOAL_P3_真实球门检测修复执行计划.md)保留根因细节与旧实验矩阵。本文后续设计与完成度记录属于历史背景，不覆盖当前真实 FAIL 状态。

> 2026-09-21 该计划与[交接提示词](../Plan/GOAL_P3_下一窗口交接提示词.md)已补完，算法仍待执行。首轮核实静止片段并做单帧/0.5/1/2/5 秒、原始/过滤/体素对照；先验收真实部件几何，再处理运动和跟踪。下文旧“窗口密度与长度无关”、默认尺寸、配准钩子与耗时预算均非当前验证结论；不得按历史架构图恢复无 IMU 里程计主线或 WebSocket 路线。

> 2026-09-19 审查更新：下一阶段以 [完整开发计划](../Plan/球门跟踪下一阶段完整开发计划.md) 为执行入口。本文保留历史设计与第一轮实现记录；逐 Stage 诊断目前含后置推算，关联展示分数未用于真实决策，LOST 与评分器语义存在已复现缺陷。第 12 节所列接口已存在，不表示可观察性和真实跟踪验收完成。20 帧优于 10 帧仅限既有状态计数，尚非精度结论。

> 生成日期：2026-09-17。Phase 0 产出（现状分析）见 `docs/GOAL_TRACKING_CURRENT_ARCHITECTURE.md`。
> 本计划遵循任务书开发顺序 Phase 0–9；每阶段验收标准见文末。
> 约束重申：LiDAR-only / No IMU；不破坏 driver、ROS2 topic、mapping 主线、现有 WebUI；
> 所有阈值进 YAML；新增功能模块化、可独立测试。

## 1. 目标架构

```text
                         Windows 本机 Demo 链路（本阶段）
┌────────────────────────────────────────────────────────────────────────────┐
│  PCAP 回放（按真实帧率 ≈10 Hz 驱动）                                          │
│      │  backend/decoder.py（复用，不改）                                      │
│      ▼                                                                      │
│  FrameBuffer（环形窗口，WINDOW_SIZE 可配，默认 10）                            │
│      │  每新帧入队，超窗淘汰                                                    │
│      ▼                                                                      │
│  Accumulator                                                                 │
│      ├─ 静止模式：窗口内合并 → ROI 裁剪 → 体素降采样                            │
│      └─ 配准钩子：预留 scan-to-scan 补偿接口（Phase 6 可挂 mapping.py 配准）     │
│      ▼                                                                      │
│  ┌───────────────┐        ┌──────────────────────────────┐                  │
│  │ 单帧快速链路    │        │ 10 帧增强链路                  │                  │
│  │ GoalDetector   │        │ AccumulatedCloud → GoalDetector│                  │
│  │ (低延迟候选)    │        │ (结构增强，确认/修正 bbox)       │                  │
│  └───────┬───────┘        └───────────────┬──────────────┘                  │
│          ▼                                ▼                                 │
│      GoalStateMachine（SEARCHING→CANDIDATE→LOCKING→LOCKED→TRACKING→LOST→REACQUIRE）
│          ▼                                                                  │
│      GoalTracker（质心/几何匹配，可选 Kalman；Goal ID 稳定）                    │
│          ▼                                                                  │
│  Demo 状态 API（HTTP 轮询，复用 FastAPI；点云沿用帧二进制 v1）                   │
│          ▼                                                                  │
│  WebUI /demo/（Three.js 点云 + 3D bbox + Goal ID/距离/置信度/状态面板）          │
└────────────────────────────────────────────────────────────────────────────┘
```

**双时间尺度**：单帧链路每帧更新（跟踪低延迟），滑动窗口链路每帧刷新
（结构增强、确认与 bbox 修正）。跟踪不等待窗口凑满。

**「实时」的定义（本阶段）**：Windows 端为 PCAP 回放驱动的在线管线（离线数据、在线算法）。
真正 UDP/ROS2 实时接入与 WebSocket 推送为后续阶段（见 §9 风险与 §10 阶段表 Phase 8 之后）。

## 2. 新增目录与文件

```text
web/backend/goal_detection/          # 独立包：算法核心，numpy/scipy only，不依赖 FastAPI
    __init__.py
    config.py            # 加载 goal_detection.yaml + 白名单覆盖 + configuration_version 哈希
    buffer.py            # FrameBuffer：固定容量环形帧缓冲（存点云 + 帧元数据）
    accumulation.py      # Accumulator：滑动窗口合并 + ROI + 体素降采样 + 配准钩子
    bev.py               # 纯 XY 的球门区域提议（staged 策略第 1 级）
    geometry.py          # 立柱/横梁/矩形框架几何评分工具
    detector.py          # GoalDetector：单帧与叠加云的球门候选提取与几何打分
    focus.py             # 候选 FOV 裁剪与 z 轴包围盒修正
    pipeline.py          # Accumulator + Detector + Tracker 的离线组合管线
    tracker.py           # GoalStateMachine + GoalTracker（关联、Kalman、丢失/重获）
web/backend/goal_detection.yaml   # 所有阈值（唯一来源，禁止散落硬编码）
web/backend/demo_api.py           # Demo 路由（挂到现有 app.py，前缀 /api/demo）
web/frontend/src/DemoApp.tsx      # Demo 页面（独立入口，见 §7）
web/frontend/src/AnnotateApp.tsx  # 人工标注页面
tools/run_goal_demo_offline.py    # 离线整片运行器：PCAP → 叠加+检测+跟踪 → JSONL 日志（测试证据）
web/tests/test_goal_*.py          # 见 §8 测试方案
```

**修改的现有文件（仅挂接，最小侵入）**：
- `web/backend/app.py`：`create_demo_router(store)` 挂接 + `/demo/`、`/annotate/` 静态页路由。
- `web/frontend/src/main.tsx` 或 `index.html`：不改动现有页面逻辑；demo 入口通过 URL 直达（`/demo/` 返回独立 HTML）。
- `web/README.md`：新增 demo 使用说明。

## 3. ROS2 Topic 设计（后续 Jetson 阶段，Windows 阶段不实现）

```text
/inno_lidar_cloud            (已有) PointCloud2 20000 点/帧 ≈10 Hz   —— 输入，不改
/goal_demo/accumulated_cloud (新)  PointCloud2  叠加降采样后点云
/goal_demo/goal_state        (新)   自定义 msg：stamp, frame_id, goal_id, state(uint8),
                                    confidence(float32), center xyz, bbox 尺寸 + 朝向,
                                    distance, accumulated_frames
```

实现时遵循 `na70_motion_detector` 的分层：`goal_detection/` 核心不动，新增
`goal_detector_node.py` 只做 PointCloud2 读写与参数声明。字段按名称和 datatype 查找。

## 4. 数据结构

```python
# buffer.py
@dataclass
class StampedCloud:
    points: np.ndarray        # (N,4) float32 x,y,z,intensity（decoder.decode_frame 输出）
    frame_index: int          # PCAP 帧号
    device_time: float | None # 帧 device_time（s），缺失为 None
    complete: bool

class FrameBuffer:            # 固定容量，thread-safe 由调用方保证
    def push(cloud: StampedCloud) -> None      # 超容量淘汰最旧
    def window() -> list[StampedCloud]         # 按时间序
    def __len__; last; is_full
```

```python
# tracker.py
class GoalTrack:
    goal_id: int
    state: GoalState            # SEARCHING/CANDIDATE/LOCKING/LOCKED/TRACKING/LOST/REACQUIRE
    center: np.ndarray          # (3,) EMA 平滑
    size: tuple[float, float, float]   # bbox (dx, dy, dz)
    yaw: float                  # 框架朝向（立柱连线方向）
    confidence: float           # 0–1，由几何分 + 时间一致性合成
    age: int                    # 锁定后帧数
    last_seen_index: int
    history: list[tuple[int, np.ndarray]]  # 中心轨迹（仅目标级，非点云）
```

WebSocket/HTTP 消息格式（`GET /api/demo/goal/state?frame_index=` 响应 JSON，字段与任务书一致）：

```json
{
  "timestamp": 123456789.0, "frame_index": 3201, "accumulated_frames": 10,
  "imu": false,
  "lidar_online": true, "fps": 9.8, "point_count": 34211,
  "goal": {
    "detected": true, "id": 1, "state": "TRACKING",
    "distance": 98.7, "confidence": 0.91,
    "center": [98.3, 2.1, 1.2],
    "bbox": {"size": [1.2, 7.4, 2.5], "yaw": 3.1, "corners": [[...8×3]]}
  },
  "debug": null
}
```

叠加/单帧点云沿用现有帧二进制 v1（`u32 头长 + JSON 头 + XYZI float32`），前端 `readFrame` 可直接解析；
debug 图层点（ROI、立柱、横梁、bbox 角点）另走 `GET /api/demo/goal/debug?frame_index=`（JSON，少量点）。

## 5. 10 帧缓存与叠加方案

- **缓存**：`FrameBuffer(capacity=window_size)` 固定环形窗口，容量随可调窗口线性变化且有上限，运行中不无界增长。
- **静止模式（情况 A，默认）**：窗口内 `np.vstack` → 距离/ROI 裁剪（`range.min/max`）→ 体素降采样（复用 `mapping.downsample` 思路独立实现，避免 import 主线配置）→ 输出叠加云。逐帧增量更新：窗口滑动时只重算降采样（约 1–2 ms 级），不做增量式累积以免体素计数与窗口不一致。
- **配准钩子（情况 B，预留）**：`Accumulator(compensator=None)`；Phase 6 接入 `mapping.py` 的 SE(2)/yaw 补偿或 `temporal_yaw.py` 估计值补偿时变 yaw 拖影。本阶段不实现 SLAM。
- **参数**（goal_detection.yaml，窗口/体素/ROI 全可配）：

```yaml
version: 1
accumulation:
  enabled: true
  window_size: 20          # 真实 PCAP 对照后的开发默认，WebUI 可调
  voxel_size: 0.5          # 远距离叠加 0.5 m 起步，Phase 8 调优
  roi: null                # [xmin,xmax,ymin,ymax,zmin,zmax] 或 null
  range:
    min: 50.0
    max: 130.0
goal_detection:
  enabled: true
  ground_z: [-2.0, 1.0]    # 地面/低矮杂波 z 窗（相对雷达安装高度）
  pole:
    height: [1.8, 4.0]     # 立柱可见高度范围 (m)
    min_points: 8          # 叠加云中成柱最少点数
    max_radius: 1.2        # 柱径容差（拖影放宽）
  frame:
    width: [5.0, 9.5]      # 两立柱间距（球门 7.32 m ± 容差）
    crossbar_z: [1.8, 3.2] # 横梁高度窗
  scoring:
    min_confidence: 0.5    # 进入 CANDIDATE
    lock_confidence: 0.7   # LOCKING→LOCKED
locking:
  min_frames: 3            # 多帧一致性确认帧数
tracking:
  lost_frame_threshold: 5
  reacquire: true
  max_center_jump: 3.0     # 关联门限 (m)
  kalman: true
webui:
  enable: true
  fps_target: 10
```

## 6. Goal Detection / Tracking 算法

**检测（对单帧与叠加云同一实现，参数可用 `min_points` 区分）**：
1. 距离门 `[range.min, range.max]` + 地面 z 窗过滤。
2. 连通域聚类（体素邻接 + scipy `cKDTree`），得到簇集合。
3. 对每簇做垂直结构提取：沿 z 直方图找"立柱剖面"（z 向延展 ≥ `pole.height`、水平半径 ≤ `max_radius` 的细高子簇）。
4. 立柱配对：两柱高度差 ≤ 容差、间距 ∈ `frame.width`、地面接触 z 相近 → 框架候选。
5. 横梁验证：柱顶连线 z ∈ `frame.crossbar_z` 且连线附近存在跨接点 → 加分。
6. 矩形度综合打分 → `confidence`（0–1）。

**状态机与跟踪**：
- SEARCHING：无候选。检测到 confidence ≥ `min_confidence` → CANDIDATE。
- CANDIDATE：连续 `locking.min_frames` 帧几何一致（中心跳变 ≤ 门限）→ LOCKING；断续回 SEARCHING。
- LOCKING→LOCKED：confidence ≥ `lock_confidence` → 分配 `goal_id`，进入 TRACKING。
- TRACKING：以单帧/叠加云检测结果做最近邻 + 几何匹配关联（门限 `max_center_jump`，尺寸/朝向相似度加权）；可选常速 Kalman 平滑中心。检测缺席 ≤ `lost_frame_threshold` 帧内保持 TRACKING（外推），超限 → LOST。
- LOST→REACQUIRE：`reacquire=true` 时在 LOST 后持续用 SEARCHING 逻辑匹配历史位置附近候选，成功则恢复原 `goal_id`（不新建）。
- **绝不每帧新建目标**：`goal_id` 只在 LOCKED 时分配，LOST→REACQUIRE 保持 ID。

**参数全部来自 §5 的 YAML**，`config.py` 提供白名单覆盖与 `configuration_version` 哈希（随 demo 运行日志记录）。

## 7. WebUI `/demo/` 设计

- 后端：`GET /demo/` 返回独立 `demo.html`（Vite 多页构建 `frontend/demo.html` 入口，与现有 `index.html` 并列，互不影响）。
- 页面布局按任务书 §13：上方 Three.js 点云视口（复用 CloudView 的着色器/正交相机/轨道控制实现，独立实例），球门 3D bbox（Box3Helper 风格）+ Goal ID 浮标；下方左"System Status"（LiDAR ONLINE、IMU DISABLED、Accumulation n/10、FPS、点数）右"Goal Tracking"（ID、Distance、Confidence、State、Age）。
- 视图切换：`[单帧] [N帧叠加]`（N 随当前参数显示）；叠加图层切换 `[Raw] [Goal ROI] [Tracking]`。
- 数据获取：HTTP 轮询 `GET /api/demo/goal/state`（每帧）+ 二进制叠加云接口；与现有前端轮询模式一致，不新建 WebSocket 服务器（见 §9）。
- `?debug=1`：追加渲染 ROI 框、簇中心、立柱线段、横梁线段、bbox 角点、跟踪轨迹、帧号/叠加帧数角标。正常模式不显示。

## 8. 测试方案

单测（`web/tests/`，合成数据，SYNTHETIC 标注）：
`test_goal_frame_buffer`（环形淘汰/容量/乱序时间）、`test_goal_accumulation`（窗口、体素、ROI、空输入/NaN）、
`test_goal_candidate`、`test_goal_geometry`（合成两柱+横梁框架应得分≥阈值，缺横梁/歪柱应降分）、
`test_goal_lock`（前 N 帧锁定时机）、`test_goal_tracking`（ID 稳定、平滑）、
`test_goal_lost`（超阈值转 LOST）、`test_goal_reacquire`（恢复原 ID）、`test_demo_api`（路由与轮询契约）。

真实数据（`9.14_caochangyou.pcap`，标记 `REAL_PCAP_WINDOWS`）：
`tools/run_goal_demo_offline.py` 整片运行，输出 JSONL 逐帧日志
（frame, timestamp, goal_id, distance, confidence, state, accumulated_frames, point_count, processing_time_ms）
到 `Plan/evidence/goal-demo/<run>/`，并统计锁定帧数、跟踪连续性、单帧 vs 10 帧点数与置信度对比。

## 9. 性能风险与预算（Jetson Orin Nano 4GB 目标）

- 禁止无界缓存：固定环形窗口；检测只留目标级状态；前端渲染上限沿用现有预算机制。
- 每帧预算 ≈100 ms（10 Hz）：解码 ~10 ms（mmap 块读）、叠加+降采样 ~10–20 ms、检测聚类 ~20–50 ms、跟踪 <1 ms、HTTP 序列化叠加云 ~10 ms。Phase 8 用 `tests/benchmark.py` 扩展实测，超标先降 voxel/点数上限。
- WebSocket 带宽：叠加云 0.5 m 体素下约 1–4 万点 ≈ 160–640 KB/帧，轮询模式下走本机回环可承受；Jetson 阶段评估 WebSocket 增量推送。

## 10. 阶段验收标准

| Phase | 内容 | 验收标准 |
|---|---|---|
| 0（已完成） | 现状分析 | 本文档 + 架构分析文档；未改任何现有代码 |
| 1 | 10 帧叠加 | `FrameBuffer`+`Accumulator` 单测通过；真实 PCAP 整片离线跑完 1211 帧无内存增长、逐帧耗时稳定（记录日志）；输出叠加云可检查 |
| 2 | 离线球门检测 | `goal_detection/` 独立包 + 几何单测通过；在真实 PCAP 叠加云上产出候选候选日志（发现与不发现都如实记录） |
| 3 | 检测接入管线 | 单帧+叠加双链路检测在 100 m 附近运行，日志含置信度对比 |
| 4 | 状态机锁定 | SEARCHING→…→LOCKED 全状态单测；真实数据锁定帧数有据可查 |
| 5 | 跟踪 | Goal ID / bbox / 距离稳定性指标从日志统计；LOST/REACQUIRE 用例通过 |
| 6 | 叠加作为检测增强源 | 双时间尺度接线；单帧 vs 叠加检测置信度/点数对比出报告 |
| 7 | WebUI `/demo/` | 浏览器打开 127.0.0.1:8765/demo/，点云+球门框+ID+距离+置信度+状态+叠加状态实时可见；现有页面回归不破坏 |
| 8 | 性能优化 | 10 Hz 稳定；渲染点数预算生效；内存恒定；基准数据入库 |
| 9 | 分级检测（BEV→FOV→Z） | BEV 采集/聚焦/z 修正合成测试通过；20 帧窗口全片真实数据单一 goal_id 跟踪、FOV 内单候选、耗时 p50 < 100 ms；对照旧策略多候选失效事实入库 |

> 诚实性约束沿用项目纪律：每阶段证据存 `Plan/evidence/goal-demo/`；合成结果标 `SYNTHETIC`；
> 真实数据结果标 `REAL_PCAP_WINDOWS`，不能替代 Jetson/ARM 证据；区分「已运行/已输出/指标达标」。

## 11. 分级检测策略（staged，2026-09-18）

20 帧叠加下时变 yaw 使立柱在 XY 平面拖尾，三维柱判据（格内 XY 紧致）失效，
旧策略（`strategy.mode: "poles"`，保留为对照）在全片 180/180 帧同时产生
20–36 个柱对候选（`20260918-w20-poles-baseline`）。staged 策略按用户思路分三级：

1. **BEV 采集（纯 XY）** `bev.py`：叠加云投影 0.8 m 地面格网，密度按窗口帧数
   归一（阈值与窗口长度无关）+ 绝对点数下限防小窗口泊松噪声。致密格 8 邻域
   连通成 blob，超过 `max_blob_cells` 的分量（墙体/围栏线）整体丢弃；blob 对
   间距落在门宽区间且「跨度密度对比」（立柱峰值密度 / 两柱之间格密度中位数，
   墙体沿跨度均匀 → 对比 ≈1，球门 ≥~2）达标才提议。只取 top-3 区域做确认。
2. **FOV 收缩** `focus.py`：跟踪器一旦持有中心（CANDIDATE 起，LOST 后重新
   全图搜索），全部检测只在候选周围 `half_size`（默认 6 m）方形 FOV 内进行
   ——同一时间最多一个球门假设，拖影大部分被排除在视野外。
3. **z 轴分析**：FOV 内 z 直方图众数估地面（叠加拖影抬高立柱 base/top 散布，
   地面众数更稳），结合立柱顶得到球门高度，修正包围盒 z 中心与高度。

防拖影绘位：聚焦后若当前单帧在 FOV 内检出（置信度 ≥ `focus.single_min_confidence`），
位置优先用当前帧（叠加云只做确认），跟踪器 Kalman 吸收抖动——绘制框跟随
当前球门而不是 20 帧拖影平均。

新配置段：`strategy` / `bev` / `focus` / `zrefine`（阈值唯一来源仍是
goal_detection.yaml）；新图层：demo 页 FOV 地面框、BEV 区域标记、
System Status 的「策略」「FOV」项。

## 12. Observable Pipeline 与开发接口（2026-09-19）

现有检测/跟踪链不重写，新增 `goal_detection/observability.py` 把每一帧映射为
RAW→FOV→XY ROI→Z/HEIGHT→CLUSTER→GOAL STRUCTURE→CANDIDATE SCORE→
BOUNDING BOX→ASSOCIATION→TRACKING 十级诊断。每一级输出输入/保留/排除点数、
参数、候选数与拒绝原因，同时为源点生成 `classification`：0=Rejected、
1=Active、2=Supporting。Rejected 仅淡化、不从 WebGL 点云移除。

候选对象保留检测器实际使用的 `supporting_point_indices`（立柱格成员与横梁带成员），
并输出 bbox、距离、方位、宽高与几何/横梁分数。跟踪器另外输出 association_score
及距离、框尺寸、方向、历史分量；它与 detection_score 语义分离。

开发接口：

- `/api/demo/sessions/{id}/pipeline`：逐级统计；
- `/api/demo/sessions/{id}/debug/cloud`：完整点云与逐点分类；
- `/api/demo/sessions/{id}/logs`：按模块/级别过滤的有界日志；
- `/api/demo/sessions/{id}/metrics`：状态帧计数及处理耗时 mean/p50/p95；
- `GET/PUT /api/demo/sessions/{id}/config`：读取配置及白名单校验后的热更新；
- `/api/demo/sessions/{id}/snapshot`：供 Codex/AI 一次读取 Pipeline、候选、关联、
  日志、指标和配置版本。

当前运动处理仍是短时传感器坐标关联：轻微平移/转动由宽门限、当前单帧优先、
Kalman 与 LOST/REACQUIRE 吸收；不把移动传感器下的中心变化解释成目标世界运动。
随机合成场景加入树木、高墙、平移和 yaw，并验证锁定后 ID 保持为 1。真实 PCAP
仅用于后续标注对账，未有人工真值前不宣称准确率通过。

# NA70 导线调试、真值与验收契约（设计稿）

版本：`wire-debug-acceptance-v1-draft`  
日期：2026-09-25  
范围：真实细线回波、ROI 检测、全视场检索、机载运动和 Jetson 性能的统一证据口径。本文是拟实施契约，不表示相应页面、API 或门禁已通过。既有[W1 原始契约](THIN_WIRE_W1_CONTRACT.md)继续有效；球门的[调试契约](GOAL_DEBUG_CONTRACT.md)只借鉴来源、状态和评分纪律。

## 1. 三类事实分别保存

| 类型 | 定义 | 允许用途 |
|---|---|---|
| `RAW_OBSERVATION` | PCAP/ROS2 解码出的原始码、XYZ/intensity、有效掩码、包时间和原始 ID | 回波和算法支撑证据；任何展示抽样都需回链原始数据 |
| `INDEPENDENT_TRUTH` | 现场独立测量、人工复核的线位置、存在/移除状态、可见区间与不确定度 | W1 归属复核、W3/W4/W5 评分；不得传给检测器生成结果 |
| `SYNTHETIC` | S0 几何、假定线径/外参与扫描方向交会、构造边界样本 | 代码和场景逻辑测试；不得进入真实 TP、量程、检出率分母 |

`UNKNOWN` 表示真值不足，不等于目标不存在。`NOT_EVALUATED` 表示缺少可评分证据，不等于检测失败。真实评分只取 reviewed 且可见性满足契约的完整录制或预先界定窗口；未复核和合成材料单列。

## 2. 身份、真值和分组

原始观测的全局身份为 `(capture_sha256, frame_index, packet_index, point_index)`。`frame_index`、`packet_index`、`point_index` 沿用 W1 的含义；任何过滤、拟合或显示抽样都保留到该身份的反向映射。一个 `window_id` 由捕获身份、连续设备时间段、终点包和请求时长确定；同一窗口的多种界面视图共用同一 ID。`run_id` 绑定输入列表、代码/依赖/配置版本和运行命令。

拟议 `wire-truth-v1` JSON 至少含：

```json
{
  "schema_version": "wire-truth-v1",
  "truth_set_id": "...",
  "capture_sha256": "...",
  "capture_role": "target_present",
  "review_status": "reviewed",
  "reviewer": "...",
  "time_or_frame_interval": {"first_frame": 0, "last_frame": 100},
  "visibility": "visible",
  "target": {
    "geometry_kind": "measured_segment",
    "frame_id": "lidar_link",
    "endpoints_m": [[0, 0, 0], [1, 0, 0]],
    "position_uncertainty_m": null,
    "survey_method": "...",
    "wire_diameter_mm": null,
    "wire_material": null
  },
  "acquisition": {
    "sensor_mount": "...",
    "install_direction": 1,
    "static_evidence": "...",
    "background_pair_capture_sha256": null,
    "weather": null,
    "sensor_settings": "..."
  }
}
```

字段值只是格式示例，不是实际端点、线径或测量结果。`target_absent` 时 `target` 可以为 null，但必须有同位置、同设置且目标确已移除或经独立复核的依据；相邻空区域只能标辅助对照。真实高压设施若不可移除，用可控替代细线建立 W1 对照，再单列真实导线域迁移；替代线通过不自动证明真实高压导线通过。只有照片但没有可信三维位置时保留 `unreviewed/unknown_geometry`。人工 ROI 和参考走廊必须在查看算法结果前由独立测量确定；W1 的“可归属回波”结论需人工复核和重复录制，不能由计数自动升级。

开发集、校准集、冻结验证集按完整 `capture_sha256`、场景/日期/目标批次分组；同一录制的重叠窗口和相邻复用包不得跨组。负例覆盖无目标、树枝、围栏横杆、杆件、建筑边缘和其他线结构。对同一目标正例，分层保留距离、线径、材料、朝向、天气、遮挡及静止/运动条件，不用总平均值掩盖困难子集。

## 3. 运行记录和 StageTrace

`wire-run-v1` 的每个窗口输出一条结果，包含 `run_id`、`capture_sha256`、`window_id`、请求/实际时长、时间质量、坐标系、运动质量、原始/有效/方向可恢复数量、候选、当前测量、预测、状态、阶段 trace 和产物引用。完整运行落盘，WebUI 通过固定 `run_id/window_id` 查询；只存在内存中的“最新 snapshot”不作为验收来源。

每个**真实执行**阶段写入 `wire-stage-trace-v1`：

| 字段 | 语义 |
|---|---|
| `stage` / `status` | 固定阶段名；`completed`、`skipped`、`rejected`、`failed`。未执行的耗时为 null，不写 0。 |
| `entity_kind` / `count_semantics` | `ray`、`return`、`candidate`、`line` 等；过滤、聚合、分支各自说明计数口径。仅过滤阶段要求输入=接受+拒绝。 |
| `input_count` / `output_count` / `reject_counts` | 实际执行处记录；按固定原因代码统计，不从最终结果反推执行过程。 |
| `input_refs` / `output_refs` | 原始 ID 或确定性的引用文件/摘要；候选上限导致截断时写明完整总数和抽样口径。 |
| `parameters` / `source_hash` | 本阶段生效阈值及其配置版本；拟合器版本、随机种子、样本策略。 |
| `latency_ms` / `source_age_s` | 计算耗时和最新原始支撑的年龄；时钟不可比较时为 null 并记录原因。 |

第一版固定阶段：`decode`、`time_quality`、`direction_quality`、`valid_return`、`window_quality`、`motion_gate`、`roi_or_proposal`、`fit`、`finite_support`、`background_check`、`freshness`、`association`、`output`。未走到的阶段写 `skipped` 及原因，不伪装已经执行。提议阶段的重叠区域/分支可以重复引用原始点，须报告唯一原始来源数，不把多个候选的点数相加当总点数。

拒绝原因使用固定枚举并允许附上下文：`INVALID_PACKET`、`INVALID_TIME`、`TIME_GAP`、`UNKNOWN_DIRECTION`、`NO_VALID_RETURN`、`PARTIAL_WINDOW`、`RESOURCE_LIMIT`、`MOTION_UNCOMPENSATED`、`NO_PROPOSAL`、`INSUFFICIENT_SUPPORT`、`DEGENERATE_SAMPLE`、`FIT_REJECTED`、`LOW_COVERAGE`、`LARGE_GAP`、`BACKGROUND_AMBIGUOUS`、`STALE_MEASUREMENT`、`MISSING_PROVENANCE`、`AMBIGUOUS_LINE`。任何新增原因需升契约版本或在兼容枚举中注册。

当前测量、预测、历史和不可观测必须分桶。`MEASURED_CURRENT` 必须有当前原始支撑引用及 `newest_source_device_time`；`PREDICTED_RECENT` 只用于搜索/可视化，`EXPIRED_HISTORY` 只用于回看；时间或位姿不足时是 `UNOBSERVABLE`，不能算作可信阴性。同一原始支撑集合形成稳定 `observation_signature`，连续轮询或重叠窗口不得重复计确认次数。

## 4. 可视化工作台

导线工作台以持久化运行包为权威数据源，提供五个同步区：

1. **场景**：正视、侧视、俯视与可旋转三维视图显示全量有效原始点、人工真值及误差带、候选/接受/拒绝线段。颜色语义固定；显示抽样与算法支撑分离，避免抽样后误以为没有点。
2. **扫描与回波**：方向可恢复/未知、有效/零距离计数和已知方向的覆盖图。方向未知处呈“未知”，不涂成“扫过但无回波”。S0 假设姿态的几何机会单独标 `SYNTHETIC`。
3. **时间轴**：逐包有效回波、ROI/线候选支撑、时间段断点、窗口首末时间、最新观测年龄、首次发现/失锁/重获；拖动后所有视图须核对同一 `run_id/window_id` 再显示。
4. **阶段流水线**：每阶段输入、接受、拒绝原因、耗时和实际参数；点击阶段查看前后点集，点击候选查看有限线段、原始内点及每个拒绝理由。
5. **验收对比**：按相同捕获和窗口并列两个 `run_id`，显示配置/源码差异、候选差异、TP/FN/FP、几何误差和逐级失败首发点；提供直达失败样本的索引。

标注页支持独立线段/可见区间、存在/移除状态、误差和来源说明；在 reviewed 前不把标注写入真实评分集。保存时固定当前 PCAP、窗口、坐标系与真值版本。UI 截图只是辅助证据，评分与原始来源以落盘 JSON/NPZ 为准。

## 5. 指标与评价规则

| 门禁 | 必报指标 | 判读纪律 |
|---|---|---|
| W1 原始回波 | 目标一致候选/独立包/时间段、位置残差、目标与同机位背景对照、重复录制结论 | ROI 点数不是确认线回波；采样方向分母不完整时不报传感器检出概率 |
| W2 工作包络 | 实际距离×线径/材料/朝向×单帧/0.5/1/2/5 秒的有支撑窗口比例、最近来源年龄、失败窗口 | 说明窗口是否重叠；至少按独立录制汇总，不把静态长窗推成实时距离 |
| W3 人工 ROI | reviewed 正/负例、接受与拒绝数、有限段误差、正交 RMS/P95、沿线占据比例、最大内部缺口、独立来源数 | 误差只在命中子集算，同时列 FN/无输出；真值端点不得作为拟合输入 |
| W4 全视场 | TP/FN/FP、precision、recall、每分钟误报、定位/方向/最近距离误差、困难负例分层 | 同一窗口多线一对一匹配；未复核区域输出单列 `UNKNOWN`，不判为 FP |
| W5 运动实时 | 首次发现/重获、最长缺测、当前测量覆盖时间、来源年龄、端到端延迟 p50/p95/p99/max、丢包/积压、峰值内存 | 测量与预测分别计；Windows/WSL2 不能代替 Jetson Orin Nano/Humble 性能结论 |

对于 reviewed 可见正例，`recall = TP/(TP+FN)`；对于已经匹配/归属的正输出，`precision = TP/(TP+FP)`。`FP/min` 的时间分母只取 reviewed、确认目标缺失的可评分背景录制时长；无有效背景时为 null。定位误差只对已匹配的 TP 计算并与 FN 数并列。具体命中空间容差、可见性、时间窗口和一对一匹配规则在 W3/W4 独立验证前由开发集冻结；没有分母时状态 `NOT_EVALUATED`，指标值为 null，不能填 0。

## 6. 运行产物与复核

每次运行输出到新目录，禁止覆盖旧失败记录：

```text
Plan/evidence/thin-wire/<run-id>/
  manifest.json             # 输入/源码/依赖/配置/真值/评分契约哈希、命令、环境、阶段状态
  truth_reference.json      # 引用的独立 reviewed 真值版本和来源，不存算法反推真值
  windows.jsonl             # 每窗状态、测量/预测、时间与候选摘要
  stages.jsonl              # 实际执行 StageTrace、拒绝原因、时间与来源摘要
  candidates.jsonl          # 接受和拒绝候选、有限几何及原始引用
  raw_refs/                # 必需的点/包引用或可复查 NPZ，写清是否完整/抽样
  figures/                 # 三视图、扫描方向、逐包时间、典型失败
  validation.json          # reviewed-only 分层评分、样本数、null/NOT_EVALUATED
  failures.jsonl           # 每个 FN/FP/超时/时间异常的窗口与首个阻断阶段
  REPORT.md                # 本次已运行、指标、失败、未测条件和下一步
```

`manifest.json` 的产物列表记录每文件哈希。完整原始 PCAP 留在 `radar_capture_data/`，由 SHA 与运行包关联；运行目录只存复查所需引用及派生物。每项性能数值写明输入规模、设备、Python/NumPy/拟合器版本和测量口径。相同输入和配置重跑时比较原始来源、候选、状态及评分摘要；不一致必须进入失败索引。

## 7. 测试与冻结门禁

1. **协议/迁移回归**：字节解码、工厂偏移、零距离与角码、设备时间、100 包逻辑帧和来源 ID 与现有 `test_decoder.py`、ROS2 驱动对齐；旧 W1 产物/入口可继续读取。
2. **契约和资源测试**：空窗、无效时间/倒退/大跳、包间隔、未知方向、NaN/Inf、重复点、两端孤立团块、超预算、取消/恢复/重启后读取、字段缺失均有明确状态；阶段计数和原始引用能对账。
3. **合成算法测试**：真线、无目标、多平行/交叉线、垂度、遮挡、树枝/围栏、运动展宽；全部标 `SYNTHETIC`，只证明实现行为。
4. **真实离线验收**：W1 真实回波复核→W2 距离/时长包络→W3 人工 ROI→W4 全场独立录制。按完整录制分组；阈值和真值版本在验证前冻结，未达标保留失败而不是调参重写结果。
5. **UI 与接口回归**：固定 `run_id/window_id` 的点云、trace、原始点选、真值和评分一致；切换/拖动/异步加载不会显示错窗数据。导线 API 不破坏球门、转台现有页面。
6. **W5 部署**：Windows 正确性→WSL2/Humble 集成→Jetson Orin Nano/Humble 实际输入性能；记录不同阶段耗时、等待、来源年龄、队列和长跑资源。飞行告警能力需另以任务速度和允许提前距离定义预算。

门禁通过必须有独立真实证据、冻结版本、明确分母和失败列表。只完成代码、输出图片或通过合成测试，均不等于真实导线识别通过。

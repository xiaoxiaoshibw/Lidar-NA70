# GOAL P3：球门检测与无 IMU 跟踪修复计划

> 2026-09-21 最新路线：当前从 [细线回波计划](细线回波验证与逐级开发计划.md) W1 开始；本文及以下球门修复入口保留为调试分支，不作为下一轮默认执行任务。原真实失败不变。

> 2026-09-20 根因排查后的剩余任务，统一按 [真实球门检测修复执行计划](GOAL_P3_真实球门检测修复执行计划.md) R0–R5 执行，第一轮为真值/窗口和高度基准。本文保留已完成工作、失败基线与回归要求；不再以“继续局部 3D fallback”作为下一步的默认顺序。P3 仍 IN_PROGRESS，新执行计划 PLANNED。

> 2026-09-21 执行计划已补完。下文历史“真实不低于基线”等退出项不构成新的 PASS 门禁；以新计划 R5 的真实留出、误报和框误差要求为准。下一轮交接见 [当前提示词](GOAL_P3_下一窗口交接提示词.md)，首轮 R0–R1 尚未执行。

日期：2026-09-20  
状态：IN_PROGRESS  
前置：P0 PASS、P1 PASS、P2 工程门禁 PASS；P2 算法指标 FAIL 作为本阶段输入。

## 1. 目标与边界

本阶段只修复 GOAL 检测、时间关联、FOV、失锁和重捕获。输入仍是 Windows 本机 NA70 PCAP，输出仍是当前传感器坐标中的短时球门 Track。P3 不恢复世界坐标、不引入完整 SLAM、不接 UDP/ROS2，不把“TRACKING 帧数增加”作为正确率。

P3 的成功定义是：同一套冻结 Synthetic 真值能够区分“正确检测、漏检、误锁、预测、失锁和重捕获”，真实 PCAP 能够定位失败责任模块；算法指标达到门禁后才进入 P4 持久化任务。

## 2. P2 失败基线

当前失败事实必须保留：

| 现象 | 证据 | 初步责任模块 |
|---|---|---|
| static 场景 seed 间 recall 差异很大 | `20260920-p2-synthetic-release` | 体素重复观测、BEV 密度、柱网格边界 |
| translation+yaw 验证 seed recall 仅 0.625 或更低 | 同上 | 柱提取、时间关联和累积拖影 |
| density_drop / missing_crossbar 大量漏检 | 同上 | 候选确认过度依赖双柱/横梁完整结构 |
| disappear_reenter 存在不可见期间错误确认 | 同上 | LOST/预测语义 |
| 真实标注区间仍零命中 | `20260920-p3-grid-real-full` | 坐标链、候选测量和标注待复核 |

上一轮“用原始观测次数直接修 BEV 密度”已在真实 PCAP 上否决，不能重新启用。P3 每次实验必须同时跑 Synthetic 和真实短回归。

## 3. 执行顺序

### GOAL-P3.1：固定坐标和测量框契约

修改：`web/backend/goal_detection/geometry.py`、`detector.py`、`pipeline.py`、`tools/score_goal_detection.py`。

工作内容：

- 固定 `size=[depth,width,height]`、yaw 方向、传感器坐标轴和角点顺序。
- 检测测量框、Z 修正框、跟踪估计框分别输出 `box_role`、`source_cloud_id`、`measurement_frame_index`。
- 评分器始终使用测量候选评价 detection，使用 track estimate 评价 prediction；LOST 不进入命中分母。
- 验证真实标注使用的 width/yaw 与当前框定义一致。

PASS 条件：旋转角点、AABB、前端框和评分器在 0°/30°/90°/负角场景逐项一致；同一个候选在 JSON、Three.js 和评分器中的中心/宽度/yaw 一致。

### GOAL-P3.2：柱结构提取修复

修改：`web/backend/goal_detection/geometry.py`、`detector.py`、`goal_detection.yaml`。

执行顺序：

1. 先按 z 高度层分离横梁和立柱证据；横梁不能把左右柱连成一个宽组件。
2. 在固定网格失败时使用有限 XY 偏移网格；偏移数量、邻域半径和预算全部在 YAML。
3. 只有两根柱都满足高度、半径和底部一致性，才进入标准柱对；单柱和缺横梁保留为低置信度候选，不直接当成完整球门。
4. 输出每个柱的 cell/邻域来源、z 跨度、半径、拒绝原因和是否经过 fallback。

PASS 条件：

- static、translation、yaw、translation+yaw 的常规 Synthetic case 中，柱对不会因 cell 边界随机消失；
- 横梁与两柱不会被合并成宽组件；
- tree/wall/fence 负样本不会稳定生成高置信度球门；
- 诊断关闭时检测输出与开启时完全相同。

### GOAL-P3.3：候选评分和关联统一

修改：`tracker.py`、`pipeline.py`、`goal_detection.yaml`、相关测试。

工作内容：

- 唯一关联函数同时用于候选选择和 Inspector 展示；禁止“显示分数”和“实际选择公式”两套逻辑。
- 使用 `device_time` 计算 dt；时间缺失时使用明确的 frame fallback 并记录 `time_quality`。
- 门控组合中心距离、bearing、宽高、无向 yaw、检测分数和预测不确定度。
- 新候选、新轨迹段和重初始化清空旧 Kalman 速度、协方差、miss 计数和历史段。
- 输出所有候选关联边、gate、score、接受/拒绝原因和 selected_candidate_id。

PASS 条件：Synthetic 多候选、相似目标、平移、yaw、时间跳变场景中，ID switch、错误关联和旧轨迹拉回均可被测试捕获；关联 JSON 与实际 Track 选择一致。

### GOAL-P3.4：LOST、预测和 REACQUIRE 语义修复

修改：`tracker.py`、`demo_api.py`、`tools/score_goal_detection.py`、`DemoApp.tsx`。

固定语义：

| 状态 | 当帧测量 | 是否算 detection | 是否进入测量 recall |
|---|---|---|---|
| LOCKED/TRACKING 且有候选 | 有 | 是 | 是 |
| LOCKED/TRACKING 缺测 | 无，可能有预测 | 否；可显示预测框 | 不以测量命中计 |
| REACQUIRE | 无或弱候选 | 否；单独统计 prediction | 不进入 measurement |
| LOST | 无 | 否；历史框灰/红显示 | 不进入任何命中分母 |

工作内容：

- 预测框和最后一次测量框分开保存；lost 框不能继续冒充当前检测。
- 目标消失超过配置阈值后进入 LOST；重新出现需要多帧几何确认才恢复。
- FOV 随预测不确定度扩大，超出上限回到全局搜索；全局搜索不能被固定 demo ROI 静默限制。
- 前端轨迹由后端按 `track_segment_id` 和 frame_index 读取，缺测不重复追加同一个点。

PASS 条件：disappear/reenter、遮挡、完全无目标、错误候选重入场景中，错误确认帧为 0 或被明确标记为失败；Track ID、轨迹段、测量/预测/LOST 桶可复算。

### GOAL-P3.5：冻结 Synthetic 回归

工具：`tools/run_goal_synthetic_suite.py`、`web/tests/test_goal_synthetic.py`。

先使用开发 seeds 0–19 调参，再冻结参数运行 validation seeds 1000–1049。每场景 200 帧；验证 seed 只报告结果，不用于继续调阈值。每次结果保存 config_version、源码 hash、seed、场景 manifest、逐帧 rows 和 summary。

必须报告：precision、recall、中心误差 P95、宽高误差 P95、ID switches、最长连续命中、错误确认帧、失锁耗时和重捕获耗时。

临时门禁：常规可观测场景 precision/recall ≥95%、中心 P95≤0.5 m、宽高 P95≤0.3 m、ID switches=0；困难场景逐项报告 envelope，不能用平均值掩盖失败。

### GOAL-P3.6：真实 PCAP 归因回归

数据：`9.14_caochangyou.pcap` 为主，`caochangzuo`、`caochangyidong` 作对照；沿用 P0 capture SHA 和 PENDING_REVIEW 标注，不把未复核标注写成真值。

每次对照至少保存：

- single / accumulated / window 10 / window 20；
- 有先验 ROI / 全局搜索；
- 首次锁定、正确测量候选、TRACKING、REACQUIRE、LOST；
- Stage 拒绝原因分布、候选中心/宽高/yaw、p50/p95/p99/max；
- 失败帧 Snapshot 和输入/源码/config hash。

真实标注区间仍为零命中时，P3 不得 PASS；必须说明是坐标链、柱提取、BEV、关联还是标注复核问题。

## 4. P3 代码文件与测试门禁

主要修改：

- `web/backend/goal_detection/geometry.py`
- `web/backend/goal_detection/detector.py`
- `web/backend/goal_detection/tracker.py`
- `web/backend/goal_detection/pipeline.py`
- `web/backend/goal_detection.yaml`
- `web/backend/demo_api.py`
- `web/frontend/src/DemoApp.tsx`
- `tools/score_goal_detection.py`
- `tools/run_goal_demo_offline.py`

新增/扩展测试：

- `test_goal_geometry.py`：z 分层、柱合并、横梁分离、旋转框；
- `test_goal_candidate.py`：拒绝原因、分数一致性、fallback 等价性；
- `test_goal_lock.py`：dt、预测、LOST、REACQUIRE 和新段重置；
- `test_goal_pipeline_staged.py`：多分支执行 trace；
- `test_goal_synthetic.py`：固定 seeds、困难场景和真值评价；
- `test_demo_api.py`：同帧 provenance、generation、测量/预测/LOST；
- `demo-observability.spec.ts`：Stage 分支、失锁显示和完整云层分类。

P3 每个子步骤都必须通过后端全量测试、前端构建和必要的浏览器验收；失败证据追加到 `Plan/evidence/goal-demo/`，不覆盖旧基线。

## 5. P3 退出条件

满足以下条件才进入 P4：

1. P2 冻结 Synthetic 常规场景临时门禁通过；
2. LOST/预测/测量语义通过契约测试；
3. 真实 PCAP 不低于当前基线，且失败原因可以从 Stage trace 复现；
4. 运行行、点来源、配置和源码哈希完整；
5. 全量测试和 WebUI Inspector 回归通过。

当前 P3 状态：有限网格偏移继续保留；2026-09-20 已将局部 fallback 改为横梁高度层分离、柱身种子连通与 XY 高层回挂，指定 static/translation_yaw 四个 seed case 均达到 100% recall；关联分数、device_time、测量/预测/LOST/REACQUIRE 与当前帧 provenance 新鲜度已进入代码和测试。真实主 PCAP 的严格当前帧新鲜度回归没有锁定，11 个待复核标注帧仍为零命中，因此 P3 尚未退出，P4 不得开始。

2026-09-20 时间证据契约增量：累积体素 provenance 现保留原始
`device_time`，候选逐帧输出 `evidence_role`、来源年龄、不同原始观测帧数、稳定
`observation_signature` 和拒绝原因；同一 signature 不再重复增加确认帧。
`PREDICTED_RECENT` 只用于搜索/FOV/预测连续性，`EXPIRED_HISTORY` 只保留诊断，
`MEASURED_CURRENT` 才能进入 measurement。新增 11 项契约测试，主回归 268 项通过。
Synthetic 双 seed 的常规四项仍为 100% precision/recall，disappear_reenter 不可见
区间错误 measurement=0 且 ID switch=0；pitch_roll 继续 FAIL。新的真实全量回放仍为
`SEARCHING=1105/CANDIDATE=106`、11 个 `PENDING_REVIEW` 标注帧零命中，且只读时间审计
显示标注 frame 0/74–83 与候选出现 frame 595 之后存在错位迹象，标签保持待复核。
证据目录：`Plan/evidence/goal-demo/20260920-p3-time-evidence-synthetic-release/`
与 `20260920-p3-time-evidence-real-full/`。
同目录新增 `annotation-time-audit.json`：全片原始点只读扫描显示两个标注框中心在
标注 frame_range 之外多次出现，进一步支持“叠加窗口/回放帧归属不一致”的复核建议；
标签仍保持 `PENDING_REVIEW`，未被自动改写。
新增 `annotation-window-audit.json` 显示 `[74,83]` 的 10/20 帧累积窗口在标注中心
附近只有右柱局部支撑、左柱邻域为 0 点，因此该标注目前不足以作为可见双柱球门真值。

同日补充地面平面归一化：低分位地面点拟合 `z=ax+by+c`，只在斜率超过配置激活
并将检测输入 z 归一化，输出候选恢复到原始 sensor 坐标。该改动不改变阈值和
provenance；`pitch_roll` 双 seed recall 从 0.545/0.585 提升到 1.000/1.000，
常规 static、translation_yaw、density_drop、missing_crossbar 与 disappear_reenter
门禁保持不变。正式证据目录：`Plan/evidence/goal-demo/20260920-p3-z-leveling-release/`。

### 2026-09-20 真实数据根因复核与局部修复

详见 [根因报告](evidence/goal-demo/20260920-root-cause-audit-final/REPORT.md)。前段原始点存在约 5 米 Y 向投影宽的球门状网面；绝对 Z>0.3 将边缘约 2.1 米跨度截成 0.44～0.54 米，无法通过 1.8 米柱高条件；0.5 米体素后的代表点数再除以窗口帧数，导致所查 20/200 帧窗口无法通过 BEV 密度；网面也会把柱身层连成半径过大的组件。

旧 7.32 米宽框来自 UI 默认值，不是实测尺寸；旧左右柱邻域审计依赖该尺寸，不能单独证明缺柱或帧错位。标签仍 PENDING_REVIEW，旧证据保留。

已修复 `_estimate_ground_level`：在 finite/range/ROI/地面带筛选后判断不足并回退已有累积云，trace 记录来源；阈值不变。真实 1211 帧归一化启用 13→470 帧，选中当前测量 24→6 帧，锁定仍 0，待复核标注仍 0/11。候选减少不等于准确率提升。后端 270 项、前端构建通过，7 场景×2 seed×200 帧 SYNTHETIC 指标与上轮一致。P3 未退出，P4 不开始。

下一轮先复核真实静止窗口和地面高度基准，再处理 BEV 统计与含球网的边界/竖线提取；不以放宽锁定阈值替代检测修复。

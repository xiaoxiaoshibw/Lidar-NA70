# GOAL_DEBUG_CONTRACT — 球门跟踪调试与评价契约 v1

日期：2026-09-19。状态：P0 冻结。本文档是 `Plan/球门跟踪下一阶段完整开发计划.md` P0 阶段的交付物，
冻结标注、运行记录、评价三方的数据契约。任何一方变更字段语义必须先改本文档并升版本号。

## 1. 标注契约 v1（annotation）

标注文件为 JSON（`web/data/annotations/<capture_id>.json`、`Plan/evidence/goal-demo/annotations/`、
`Plan/evidence/goal-demo/annotations-reviewed/`）：

```jsonc
{
  "version": 1,
  "method": "MANUAL",            // MANUAL | SYNTHETIC | ALGORITHM
  "labeler": "名字或工位",
  "capture_id": "<uuid>",
  "capture_sha256": "<导入时 PCAP 的 sha256>",
  "labels": [ /* 见下 */ ]
}
```

单条 label：

| 字段 | 类型 | 说明 |
|---|---|---|
| `capture_id` | string | 必填。标注所属的 capture。 |
| `type` | string | `goal`（球门）或 `non_goal_structure`（负样本结构）。 |
| `frame_range` | [int, int] | 该标注覆盖的帧区间（闭区间），按 capture 内 frame_index。 |
| `box` | object? | `{center:[x,y,z], size:[厚度, 宽度, 高度], yaw}`。宽度 = `size[1]`，为两门柱中心距；yaw 为门线在 XY 平面的朝向（弧度，right_base − left_base 的 atan2）。与 UI 保存格式一致。 |
| `points` | object? | 5 点标注 `{left_post_base, left_post_top, right_post_base, right_post_top, crossbar_mid}`，每个为 `[x,y,z]`。 |
| `visibility` | string? | `visible`（默认）/ `partially_occluded` / `invisible` / `uncertain`。`invisible` 与 `uncertain` 不进入准确率分母，只进入分层统计。缺省视为 `visible`，但记为未复核。 |
| `review_status` | string? | `reviewed` / `PENDING_REVIEW` / `UNREVIEWED_LEGACY`。文件级缺省视为 `UNREVIEWED_LEGACY`。评价器使用未复核标注时必须原样列出并在报告中警告。 |
| `estimated_error_m` | number? | 标注者估计的框中心误差（米）。缺省 null。 |
| `note` | string? | 自由备注。 |
| `annotation_context` | object? | 标注时的累积云上下文：`frame_index`、`device_time`、`accumulated_frames`、`window_size`、`voxel_size`、`range`、`source=demo_accumulated_cloud`。用于复核窗口语义，不改变 `frame_range` 评价规则。 |

规则：

- `box` 与 `points` 至少给一个；两者都给时以 `box` 为准，`points` 仅作溯源。
- 标注工作台使用固定 XY 俯视投影：视图只允许平移/缩放，框体在 XY 平面拖动或旋转，
  不允许相机绕点云旋转；云响应的 `meta.index` 必须与时间轴当前帧一致后才可渲染和保存。
- 新标注保存 `annotation_context`，记录累积窗口和设备时间，避免把旧窗口云误保存到新帧。
- 真值推导统一走 `label_truth()`（tools/score_goal_detection.py）：box 优先，否则 5 点派生。
- **capture/帧区间隔离**：标注只对相同 `capture_id` 且 `frame_index ∈ frame_range` 的帧生效。
  不在任何标注区间内的帧状态为 **UNKNOWN**，绝不计入误检或准确率分母。
- 同一 (capture_id, type, frame_range) 的重复标注按来源优先级去重：
  `annotations-reviewed/` > `Plan/evidence/goal-demo/annotations/` > `web/data/annotations/`。
- 移动场景的标注不得把单个 sensor-frame 框复制到整段（P6 复核规则）。

### 1.1 标注工作台 v2 增补（2026-09-21，向后兼容）

- `annotation_context.voxel_size` 允许 `0`：表示叠加云为**原始全量点**（无体素化、无距离/ROI
  过滤），`range` 记 `[0, 2000]` 全量哨兵。标注工作台 `/annotate/` 默认此模式，展示的点集
  就是标注所指的点集，不允许任何筛选。`voxel_size > 0` 语义不变。
- goal 标注允许**仅带 `strokes`**（无 box、无 points）：`strokes` 为画笔折线列表
  `[{points: [[x,y],…], brush_radius}, …]`（世界 XY 坐标，绑定 `frame_range` 窗口）。
  此类标注是「画笔区域真值」：`label_truth()` 返回 None，评分器按既有 `no_truth` /
  `no_geometry_truth` 路径跳过几何评价，不得为其编造框。带 box 的标注仍可同时携带
  `strokes` 与 `analysis`（画笔几何分析输出，仅溯源，评价只用 box）。
- 每条保存的 label 由后端分配唯一 `label_id`（12 位十六进制）。标注工作台提供
  `DELETE /api/demo/annotations?capture_id=…&label_id=…` 删除单条；该删除只作用于
  `web/data/annotations/` 工作台存储，不触及 `Plan/evidence/` 及 annotations-reviewed 副本。
  无 `label_id` 的历史标注不可经此端点删除。

## 2. 运行记录契约 v1（goal_run.jsonl 行）

P1 向后兼容扩展 `goal-run-v1.1`：新增 `schema_version`、`selected_measurement`、
`selected_measurement_source`、`accumulated_cloud_id` 与 `execution_trace`。旧 v1
字段语义保持不变；评分器仍可读取 v1。`execution_trace` 仅记录实际执行节点，
未执行节点的 `latency_ms` 为 null，禁止用 0 冒充执行耗时。

P3 时间证据扩展：每个 `single_*`、`accumulated_*` 和
`selected_measurement` 候选同时携带以下字段（缺少原始来源时也必须显式给出
`EXPIRED_HISTORY/missing_provenance`，不能静默当作当前测量）：

| 字段 | 说明 |
|---|---|
| `evidence_role` | `MEASURED_CURRENT`（当前帧来源）、`PREDICTED_RECENT`（仅近期历史）或 `EXPIRED_HISTORY`（超时/无来源）。 |
| `newest_source_frame` / `newest_source_device_time` | 由实际原始点 provenance 求得的最新来源。 |
| `source_age_frames` / `source_age_seconds` | 相对于处理帧/设备时间的年龄；设备时间不可用时秒级字段为 null。 |
| `unique_observation_frames` | 来源集合中的不同原始帧数。 |
| `observation_signature` | 对实际 `(frame_index, point_index)` 来源集合的稳定摘要；同一摘要不得重复贡献确认帧。 |
| `freshness_reject_reason` | `not_current_frame`、`source_age_exceeded`、`missing_provenance` 等明确拒绝原因。 |
| `time_quality` | `device_time` 或明确的 `frame_fallback_*` 原因。 |

`MEASURED_CURRENT` 才能设置 `has_measurement=true`、刷新
`last_measurement`、进入 measurement 评价和 `selected_measurement`；
`PREDICTED_RECENT` 只能维持搜索/FOV/预测连续性；`EXPIRED_HISTORY` 只进入
历史调试显示。确认和 `REACQUIRE` 计数以不同 `observation_signature` 为单位，
不能用同一累积窗口在连续处理帧中的重复输出增加 `min_frames`。年龄阈值和签名
历史上限来自 `goal_detection.yaml` 的 `tracking.evidence`。

P3 几何预处理还可在 `goal_detection.z_leveling` 启用受限地面平面归一化：仅对
detector 输入使用有限、范围/ROI 内的低分位点拟合 `z=ax+by+c`，斜率或残差超限时
明确跳过；x/y、原始点索引和输出 sensor 坐标保持不变。Stage trace 必须记录
`ground_leveling` 的系数、残差或跳过原因，不能把该归一化写成放宽高度阈值。

Web 调试云 v3 的逐点分类：0=rejected、1=active、2=supporting、3=context_raw；
独立 `highlight` 字段：0=none、1=selected_candidate_support。
`context_raw` 是为保持完整场景而叠加的当前原始帧，不表示该点被算法拒绝；
supporting 是独立高亮语义，查询特定候选时不得改变 Stage 的接受/拒绝事实。

`tools/run_goal_demo_offline.py` 每帧输出一行，字段：

| 字段 | 说明 |
|---|---|
| `capture_id` | 必填。离线运行用 `--capture-id` 声明（与导入 capture 的 uuid 对应，使标注可连接）；未声明时为 `pcap-<输入sha256前16位>`。历史行缺此字段 = 旧格式，capture 归属不明。 |
| `capture_sha256` | 输入 PCAP 文件 sha256。 |
| `config_version` | `configuration_version(cfg)` 内容哈希。 |
| `frame_index` / `device_time` | capture 内帧序号 / 设备时间。 |
| `motion_status` | runner 对本帧的雷达运动声明：`unknown`、`moving` 或 `confirmed_static`；`--motion-status moving` 可为移动录制显式标注，`--confirmed-static-until` 只覆盖已由现场确认的起始静止区间。 |
| `accumulated_best` | 累积窗最优候选（可能为 null，也可能只有 `PREDICTED_RECENT`/`EXPIRED_HISTORY` 证据）；是否为当前测量需检查 `evidence_role` 与 `selected_measurement`。 |
| `motion_diagnostic_candidates` | 已声明移动且未经位姿补偿的 facade 多帧门形候选，保留原始支持引用供复核；不是当帧检测输出。 |
| `single_best` | 当帧单帧检测最优候选（测量层，对照用）。 |
| `goal` | 跟踪层输出：`{id, state, confidence, center, size, yaw, age, last_seen_index}`（`detected` 字段由 runner 剥离，见 R04）。 |
| `strategy` / `focus_active` / `bev_regions` / `elapsed_ms` 等 | 执行上下文。 |

多 PCAP 运行：每个 PCAP 使用**独立的新 pipeline 实例**（tracker/accumulator 不跨文件复用），
逐帧记录 capture 身份，保证多文件隔离。

summary.json 额外携带：`command`、`env`（python/platform）、`source_hashes`（goal_detection 包、
runner、scorer、goal_detection.yaml 的 sha256）、`frames_per_capture`（每 PCAP 路径+sha256+帧数+时长），
以及 `motion_status_outside_confirmed_static`。运动声明不构成位姿测量，移动窗口的未补偿叠加云不得解释为刚性球门。

人工球门 ROI 与地面参考范围分别配置。`goal_detection.z_leveling.reference_roi`
为 `detection`（沿用检测 ROI）或 `full_field`（单帧地面拟合使用整个有效范围）。
`full_field` 只解决窄 ROI 内地面点不足，不能对齐不同雷达位姿。
已声明 `moving` 且来源超过一帧的 facade 几何框进入
`accumulated_rejected_candidates`，`reject_reason=uncompensated_moving_window`、
`evidence_role=UNCOMPENSATED_MOTION`、`coordinate_frame=unaligned_sensor_window`；
`motion_window_gate` 记录拒绝数。它不参与 tracker 确认；可信的当前单帧候选仍可参与。

## 3. 评价契约 v1（eval config）

`tools/goal_eval_config.yaml`，版本化（`version` 字段 + 内容哈希写入报告）。
当前 `eval-v1`：

| 键 | 默认 | 说明 |
|---|---|---|
| `hit_radius_m` | 1.5 | 命中半径（XY 平面中心距）。 |
| `min_confidence` | 0.5 | 候选/跟踪输出低于此置信度不计为正输出。 |
| `measured_states` | [LOCKED, TRACKING] | 跟踪框视为基于测量的状态。 |
| `predicted_states` | [REACQUIRE] | 跟踪框为预测（重获搜索）的状态，单独计预测指标。 |
| `lost_states` | [LOST] | 失锁历史框：不计入任何准确率分母，单独计数上报（R04 语义修复前不得给失锁框记命中）。 |
| `counted_visibility` | [visible, partially_occluded] | 进入准确率分母的可见性。 |

评价分层（`validation.json` 必须分开给出，禁止合并成一个"命中率"）：

1. **measurement（检测/测量层）**：以 `accumulated_best` 为输出。逐标注帧判定 hit/miss/no_output；
   precision = hits / 正输出帧；recall = hits / 有效标注帧；中心误差只在命中子集统计，
   同时报告未命中率，禁止只报命中子集的小误差。
2. **prediction（跟踪预测层）**：以 `goal` 框为输出，按状态分为 measured / predicted / lost
   三个桶分别统计，互不混入。
3. **UNKNOWN 桶**：不在任何标注区间内的帧数及其正输出数（潜在误检，不判罪）。

无可用标注（无 label、全部被可见性排除或 capture 无法归属）时，报告 `status: NOT_EVALUATED`，
分母为 0 一律输出 null 而不是 0。

## 4. 基线与失败事实纪律

- 历史证据目录与标注**原样保留**，不改写；其局限在
  `Plan/evidence/goal-demo/baseline-inventory.md` 逐条标明。
- 已知失败事实（R01–R09）在修复前作为基线引用，修复后以测试锁定，禁止从文档删除失败记录、
  只追加修复结果。
- 指标数值在 P0 定义、P2 开发集校准、冻结后改动必须写明变更理由并升版本，不得按失败结果反向放宽。

# 下一窗口 GOAL P3 交接提示词

> 2026-09-21 最新交接：用户已将近期主线恢复为 [足球门识别主线开发计划](足球门识别主线开发计划.md)，目标是先识别真实足球门并验证完整感知链。下一轮从 G0–G1 开始：建立可信窗口真值，再统一地面相对高度；G2 才做人工 ROI 内含球网几何，G3 之后恢复全场搜索与跟踪。细线 W1 工具和证据保留但当前暂停。

更新：2026-09-21。本次已重新制定足球门主线；算法阶段 G0–G5 尚未完成。继续开发时以新主线计划为准，本文其余旧 R0–R5 内容仅作根因与历史实施参考。

## 当前任务

继续 `D:\Code\radar` 的足球门识别主线，首轮完成 G0 真实窗口与评价基础、G1 高度语义。先取得“真实静止窗口的部件支撑没有被错误删除”的可复查证据，再进入人工 ROI 内含球网几何；本轮不修改 BEV、tracker 或细线算法。

依次读取：

1. `AGENTS.md`、[Plan/README](README.md)、[开发执行规范](05_开发执行规范.md)。
2. [真实球门检测修复执行计划](GOAL_P3_真实球门检测修复执行计划.md)，尤其第 3、4、9 节。
3. [真实失败根因报告](evidence/goal-demo/20260920-root-cause-audit-final/REPORT.md)。
4. [调试契约](../docs/GOAL_DEBUG_CONTRACT.md)与[完整开发计划](球门跟踪下一阶段完整开发计划.md)的 P4–P7 范围。

检查实时工作树并保留无关改动。不要依据历史交接中的固定行号猜实现；不自动提交已有删除或重写旧证据。

## 当前基线与结论

| 项目 | 历史已验证记录，开发前核对当前差异 |
|---|---|
| 阶段 | P0/P1 PASS；P2 工程 PASS、算法边界保留；P3 FAIL / IN_PROGRESS；R0–R5 和 P4–P7 未执行 |
| 最近真实回放 | `Plan/evidence/goal-demo/20260920-root-cause-ground-fix/`，1211 帧，SEARCHING=1180、CANDIDATE=31、LOCKED/TRACKING=0 |
| 真实指标限制 | 当前测量 6 帧、待复核标注 0/11；均不能解释为 reviewed 准确率 |
| 工程与合成 | 270 项后端测试、前端构建通过；7 场景×2 seed×200 帧 SYNTHETIC 与上轮一致；这是既有结果，本次未重跑 |
| 已修问题 | 地面估计在当前帧 ROI/低分位过滤后不足时回退累积云；归一化启用 13→470 帧 |
| 尚存问题 | 高度基准、体素后 BEV 统计、网面连接双柱；真实窗口/尺寸待复核、固定 ROI 与运动污染 |

主输入：`radar_capture_data/2/9.14_caochangyou.pcap`。

SHA-256：`ceca9fc08758f3c35f7b8b1c024bdab90e65f49ab65e39102cdd58dc0c5292db`。

capture_id：`c4cc19fb-3020-4ccc-8407-1521f71b7212`（绑定上述输入，不能跨 PCAP 复用）。

7.32×2.44 米是 UI 默认值，z+1.3 米是诊断干预，均非现场标定。旧“左柱 0 点”依赖错误尺寸假定，不能单独证明缺柱/时间错位。原始点中有球门状结构，但不等于已确认每个单帧有完整结构。

## 首轮按此执行

1. 核对工作树、源码与配置哈希，冻结既有失败基线；已有变化单列，不重复覆盖旧证据。
2. 检查 0–199、64–83、308–327 等诊断窗口的原始三视图、尺寸、地面和真实动作；不能按 `you/zuo` 文件名认定静止。整理至少 5 个 reviewed 完整正窗口、5 个 reviewed 背景窗口，部分结构另列；同一片段的变体不重复充样本数。缺复核或静止片段时记录缺口，继续独立工具和测试工作。
3. 先在调试契约中版本化单帧/累积窗口标注，明确评估单位、原始点引用和按片段分组；再实现兼容读写和评分。不能将 200 帧云的框复制为 200 帧真值；不可见期的误测量/误确认必须计数。
4. 在现有审计器补单帧及 0.5/1/2/5 秒因果窗、实际时间跨度、无降采样对照和逐部件计数。当前 `--window/--windows` 是帧数，`--voxel 0` 是默认值；不要传入虚构的按秒或关闭体素 CLI 参数。保留 XYZ/intensity、设备时间和 `(frame_index, point_index)`。
5. 先写能复现 R1 的失败测试，再修地面相对高度、柱身观测跨度和原始坐标恢复；覆盖低坡度安装偏置、无可信地面、退化、过期来源和旧窗口的裁剪损失。阈值与实验矩阵放 YAML。
6. 输出同一窗口 R1 前后：地面来源/可信度、两侧及上沿保留点、柱身跨度和拒绝原因；测试真实短对照与相关 SYNTHETIC，再跑阶段全量。原始回波/真值不足时按新计划第 10 节分流，不强造完整框。

首轮证据放 `Plan/evidence/goal-demo/<新的R0-R1目录>/`：manifest、版本化标注、window_comparison、三视图、测试日志及 REPORT，具体字段见执行计划第 9 节。这些文件目前待生成。

## 验证与交付纪律

针对当前改动跑相关后端测试；阶段变更集完成后在 `web` 运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

涉及前端时在 `web/frontend` 构建并运行相应用例；浏览器证据需后台及测试采集文件。现有帧窗口诊断命令见新执行计划第 9 节，真实完整回放见根因报告第 6 节；每次新建输出目录。新增工具参数实现后应将实跑命令记入本轮 manifest。

R1 修复不等于 P3 通过：随后按 R2 BEV → R3 含网面几何（M1）→ R4 全场/运动 → R5 冻结验收（M2）推进。独立计量观测等待、计算耗时、显示延迟和跟踪稳定性；无可信姿态/时间依据不声称已做运动补偿。P4–P7 在 P3 通过后执行，Jetson/Humble 再单独验收部署性能。细线实验可另立计划，球门结果不外推到 200 米导线。

每个阶段更新执行计划状态、`Plan/README.md` 与 `web/VALIDATION.md`，明确已运行、已输出、指标达标和阶段通过；没有新实测时保留当前真实 FAIL。

<details>
<summary>历史交接原文（已被上述入口替代，不按此选择下一步）</summary>

以下锁定帧、250 项测试、旧失败列表及“下一步”均属历史快照，不代表当前待办。

请继续 `D:\Code\radar` 中的 GOAL 无 IMU 球门检测与跟踪开发，不要重新从零设计 WebUI，也不要回退当前工作区已有改动。

## 必须先读

1. `AGENTS.md`
2. `Plan/README.md`
3. `Plan/05_开发执行规范.md`
4. `Plan/球门跟踪下一阶段完整开发计划.md`
5. `Plan/GOAL_P3_球门检测与跟踪修复计划.md`
6. `docs/GOAL_DEBUG_CONTRACT.md`
7. `Plan/evidence/goal-demo/20260920-p2-synthetic-release/REPORT.md`
8. `Plan/evidence/goal-demo/20260920-p3-grid-real-full/REPORT.md`

读取后先检查实时工作区和 `git status --short`，保留用户已有未提交改动，不使用 `git reset --hard` 或 `git checkout --`。

## 当前阶段状态

- P0：PASS。评分、box/5点标注、capture 隔离、UNKNOWN 桶、测量/预测/LOST 分层已冻结。
- P1：PASS。真实执行 Stage trace、原始点溯源、体素 provenance、旋转框角点/AABB、v3 调试云已完成。
- P2：工程门禁 PASS，算法指标 FAIL。Synthetic 生成器、真值和评价器已完成，但当前算法在 static、density_drop、missing_crossbar、pitch_roll、disappear_reenter 等场景失败。
- P3：IN_PROGRESS。已加入有限 XY 网格偏移 fallback 和局部 3D fallback，但 P3 尚未通过。
- P4–P7：NOT_RUN。

当前全量后端回归：`250 passed`（后续新增测试后以实际结果为准）。前端生产构建和 `demo-observability` 浏览器测试已通过。

## 已知真实证据

主真实输入：`radar_capture_data/2/9.14_caochangyou.pcap`。

输入 SHA-256：

`ceca9fc08758f3c35f7b8b1c024bdab90e65f49ab65e39102cdd58dc0c5292db`

P1 真实回归：

- 首次锁定帧：597；
- 20 帧窗口；
- p50 约 35.55 ms，p95 约 42.67 ms；
- 状态计数大致为 SEARCHING 595、CANDIDATE 2、LOCKED 1、TRACKING 189、REACQUIRE 281、LOST 143；
- 标注区间仍然零命中，不能宣称真实准确率通过。

P3 证据：

- `Plan/evidence/goal-demo/20260920-p3-grid-real-full/summary.json`
- `Plan/evidence/goal-demo/20260920-p3-grid-real-full/REPORT.md`

P2 Synthetic 证据：

- `Plan/evidence/goal-demo/20260920-p2-synthetic-release/summary.json`
- `Plan/evidence/goal-demo/20260920-p2-synthetic-release/REPORT.md`

## 当前最重要的算法事实

Stage trace 已定位出：

```text
BEV proposal
↓
FOV crop
↓
Z height filter
↓
固定 XY cell 的 pole extraction
↓
第二根柱被拆散或高度不足
↓
没有有效 pole pair
↓
没有 goal candidate
```

局部 3D fallback 目前也没有完全解决问题，因为横梁可能和两根柱连接成一个水平半径过大的组件。下一步应先做“按高度层分离横梁，再做局部 3D 柱合并”，不要继续盲目扩大邻域半径。

P2 暴露的其他问题：

- static 场景不同 seed recall 差异很大；
- translation+yaw 验证 seed recall 只有约 0.625；
- density_drop 和 missing_crossbar 大量漏检；
- pitch_roll 高度误差约 0.9–1.0 m；
- disappear_reenter 仍有目标不可见期间的错误确认；
- LOST、预测框和当前测量框语义还需要 P3 修复。

上一轮“直接用原始观测次数修 BEV 密度”的实验已经在真实 PCAP 上否决，不能直接重新启用。失败证据目录：

`Plan/evidence/goal-demo/20260920-p3-bev-observation-real/`

## 下一步必须做的工作

### P3.1：柱结构提取

修改重点：

- `web/backend/goal_detection/geometry.py`
- `web/backend/goal_detection/detector.py`
- `web/backend/goal_detection.yaml`
- `web/tests/test_goal_geometry.py`
- `web/tests/test_goal_candidate.py`
- `web/tests/test_goal_pipeline_staged.py`

实现顺序：

1. 先按 z 高度层识别横梁带，避免横梁连接左右柱形成一个宽组件；
2. 对剩余立柱证据做局部 3D 邻域合并；
3. 保持原始 point index/provenance；
4. 输出 `height_too_short`、`radius_too_large`、`horizontal_bridge`、`insufficient_vertical_support` 等明确原因；
5. 在没有两根有效柱时保留 rejected candidate，而不是静默返回空。

### P3.2：关联与状态机

修改重点：

- `web/backend/goal_detection/tracker.py`
- `web/backend/goal_detection/pipeline.py`
- `web/backend/goal_detection.yaml`
- `web/tests/test_goal_lock.py`

要求：

- 使用 `device_time` 计算 dt；
- 实际选择分数和 Inspector 显示分数使用同一个函数；
- yaw 按球门无向柱线处理 π 对称；
- LOST 不再返回“当前 detected=true”；
- measured / predicted / LOST 分开输出；
- 重捕获必须多帧确认；
- 新轨迹段清空旧 Kalman 速度、协方差和历史；
- FOV 随预测不确定度扩大，超时回到全域搜索。

### P3.3：验证顺序

每次代码修改后先运行：

```powershell
cd D:\Code\radar\web
.\.venv\Scripts\python.exe -m pytest tests\test_goal_geometry.py tests\test_goal_candidate.py tests\test_goal_pipeline_staged.py tests\test_goal_lock.py tests\test_goal_synthetic.py -q
```

然后运行 Synthetic 对照：

```powershell
cd D:\Code\radar
.\web\.venv\Scripts\python.exe tools\run_goal_synthetic_suite.py `
  --output Plan\evidence\goal-demo\<run-name> `
  --scenario static --scenario translation_yaw `
  --seed 0 --seed 1000 --frames 200
```

通过局部测试后，再运行真实 PCAP：

```powershell
cd D:\Code\radar
.\web\.venv\Scripts\python.exe tools\run_goal_demo_offline.py `
  --pcap radar_capture_data\2\9.14_caochangyou.pcap `
  --capture-id c4cc19fb-3020-4ccc-8407-1521f71b7212 `
  --run <run-name>
```

## P3 不能宣称通过的条件

- 只看到 TRACKING 帧数增加；
- 只有 Synthetic 通过、真实标注仍零命中；
- Stage trace 仍是后置推算或 skipped 写 0 ms；
- LOST 框仍计为当前测量；
- 坐标链和角点没有通过测试；
- 只通过放宽阈值得到结果。

P3 通过后才能进入 P4 持久化 GOAL job；P4 之前不要重写前端框架，也不要接 Jetson 实时设备。

</details>

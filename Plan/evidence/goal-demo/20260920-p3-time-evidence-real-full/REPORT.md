# P3 真实 PCAP 时间证据回归

日期：2026-09-20。状态：FAIL / P3 IN_PROGRESS；不得进入 P4。

输入：`radar_capture_data/2/9.14_caochangyou.pcap`，capture ID
`c4cc19fb-3020-4ccc-8407-1521f71b7212`，SHA-256
`ceca9fc08758f3c35f7b8b1c024bdab90e65f49ab65e39102cdd58dc0c5292db`。
完整配置、源码/runner/scorer/YAML 哈希、逐帧 Stage trace 和 validation.json 均在
本目录。

运行命令：

```powershell
.\web\.venv\Scripts\python.exe tools\run_goal_demo_offline.py `
  --pcap radar_capture_data\2\9.14_caochangyou.pcap `
  --capture-id c4cc19fb-3020-4ccc-8407-1521f71b7212 `
  --run 20260920-p3-time-evidence-real-full
.\web\.venv\Scripts\python.exe tools\score_goal_detection.py `
  --run Plan\evidence\goal-demo\20260920-p3-time-evidence-real-full `
  --capture c4cc19fb-3020-4ccc-8407-1521f71b7212 `
  --output Plan\evidence\goal-demo\20260920-p3-time-evidence-real-full
```

## 结果

- 1211 帧，`SEARCHING=1105`、`CANDIDATE=106`，没有 LOCKED/TRACKING/REACQUIRE/LOST。
- 累积候选的来源年龄：age=0 共 25 次、age=1 共 15 次、age=2 共 7 次、age=3 共 3 次。
  age=1/2/3 的候选均标为 `PREDICTED_RECENT`，未产生 measurement；本次真实候选
  没有持续到 age>3，过期桶和 `expired_source_evidence` 字段由 Synthetic 消失场景
  明确覆盖。
- 25 个 `MEASURED_CURRENT` 候选具有实际当前帧 provenance 和唯一
  `observation_signature`；相同 signature 在紧邻处理帧只标为近期历史。
- `validation.json`：11 个 `PENDING_REVIEW` 可见标注帧 hits=0、measurement
  recall=0.0；该真实零命中保持 FAIL，未改写标注或评价事实。

## 标注时间对应关系只读审计

- decoder 对该 PCAP 给出 1211 个 frame_index，帧号连续从 0 到 1210；frame 0 的
  device_time=18.944，frame 74–83 为 26.344–27.243 s。
- 标注 frame 0 中心 `[79.75, 6.70]`：原始 frame 0 最近点距离 6.403 m，3 m 内为 0 点。
- 标注 frame 74–83 中心 `[93.84, -26.53]`：frame 74 最近点 5.748 m、3 m 内 0 点；
  frame 75/76/78/82/83 才出现近邻点，帧内可见性不连续。
- 当前检测候选首次出现在 frame 595（device_time=78.451 s）之后；frame 595 的
  累积候选中心 `[99.54, -23.68]`，其原始点集合为当前帧 provenance，之后 frame
  596/597 复用同一 signature 并降为 `PREDICTED_RECENT`。

证据支持“标注帧号/时间与本次 decoder 输出存在错位或标注来自另一运行/seek 状态”的
建议，但不足以在没有人工复核的情况下确定唯一偏移。因此两个标签继续保持
`PENDING_REVIEW`，不修改 annotation JSON，也不把真实零命中改成通过。

补充只读对照：`20260920-p3-time-evidence-real-noroi-100` 在前 100 帧关闭 demo
ROI 后，frame 74–83 虽出现候选，但中心主要落在 y≈−60 至 −67 m 的另一结构，仍不
命中标注 `[93.84, -26.53]`；该结果不改变主配置，也不能作为 P3 通过证据。

全片原始点只读扫描（不经过检测器）进一步显示两个框中心并非只在标注区间出现：

- `[79.75, 6.70]` 最近点最强的帧包括 23（21.244 s）、613（80.251 s）、687
  （87.651 s）、733（92.251 s）和 1175（136.455 s）；frame 0 最近点距离为
  6.403 m。
- `[93.84, -26.53]` 最近点最强的帧包括 595（78.451 s）、599（78.851 s）、688
  （87.751 s）、650（83.951 s）和 915（110.451 s）；frame 74 最近点距离为
  5.748 m。

这说明标注框更像是某个叠加窗口/交互回放状态下的结构位置，而不是严格对应
`frame_range` 内每个 sensor frame 的当前可见测量。该结论仍需人工回看原始云确认，
因此不自动改标注、不调整评分分母。

窗口语义审计见 `annotation-window-audit.json`：UI 默认保存最近 20 帧，但标注
`[74,83]` 的长度为 10，说明当时很可能选择了 10 帧窗口。该窗口在标注中心附近有
点，但按标注 yaw 推导的左柱邻域 1.2 m 内为 0 点，右柱仅 21 点且只有 10 点高于
当前地面门限；这不足以证明存在可见的双柱球门，继续保持 `PENDING_REVIEW` 是必要的。
即使按默认 20 帧窗口 `[64,83]` 重算，左柱邻域仍为 0 点、右柱邻域为 21 点。

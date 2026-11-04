# P3 真实 PCAP 回归：分层柱提取、统一关联与严格测量新鲜度

日期：2026-09-20。状态：FAIL / P3 IN_PROGRESS。

## 输入与可复现信息

- 输入：`radar_capture_data/2/9.14_caochangyou.pcap`
- capture ID：`c4cc19fb-3020-4ccc-8407-1521f71b7212`
- 输入 SHA-256：`ceca9fc08758f3c35f7b8b1c024bdab90e65f49ab65e39102cdd58dc0c5292db`
- config version：`06a7a5765323ed77`
- 环境：Windows 11，Python 3.12.10
- `summary.json` 保存完整配置、GOAL 源码/runner/scorer/YAML 哈希和输入哈希；`goal_run.jsonl` 保存逐帧 Stage trace、明确拒绝原因、association 与测量/预测状态。

运行命令：

```powershell
.\web\.venv\Scripts\python.exe tools\run_goal_demo_offline.py `
  --pcap radar_capture_data\2\9.14_caochangyou.pcap `
  --capture-id c4cc19fb-3020-4ccc-8407-1521f71b7212 `
  --run 20260920-p3-layered-tracker-real-full
```

## 结果

- 完整运行 1211 帧，状态计数：SEARCHING 1118、CANDIDATE 93；LOCKED/TRACKING/REACQUIRE/LOST 均为 0。
- 处理耗时：p50 36.92 ms、p95 52.18 ms、max 102.55 ms。
- 结构链实际执行：pole_extract 257 次、local_3d_fallback 12 次、pair_score completed 245 次。
- 累积候选共出现在 50 帧；其最新原始支撑点年龄为 0 帧 25 次、1 帧 15 次、2 帧 7 次、3 帧 3 次。严格要求当前帧原始支撑后，28 个候选以 `no_current_frame_support` 拒绝。
- 评价：11 个 `PENDING_REVIEW` 可见标注帧 hits=0、recall=0；UNKNOWN 1200 帧，其中 44 帧存在输出。真实零命中原样保留。

## 结论与下一步

本轮没有复现旧基线的 frame 597 锁定，因此真实回归未达到“不低于当前基线”的退出条件，P3 不得 PASS，也不得进入 P4。旧基线的 TRACKING 数量不能直接视为正确，因为窗口历史点可重复充当当前测量；本轮严格 provenance 检查暴露出真实 NA70 候选只在部分帧取得新支撑。

下一步应基于 `device_time + newest_source_frame` 建立“当前测量、有限龄预测证据、过期历史”三桶，并要求确认使用不同原始观测时刻，不能简单恢复旧窗口候选或放宽几何阈值。同时需复核 frame 0 与 74–83 的待审核标注和检测实际候选出现区间 595 之后的时间对应关系。

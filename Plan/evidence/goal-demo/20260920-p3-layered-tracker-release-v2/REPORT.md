# P3 Synthetic 回归：分层柱提取与跟踪语义

日期：2026-09-20。标签：`SYNTHETIC`。状态：指定常规场景门禁 PASS；不代表 P3 总体通过。

## 修改与证据口径

- 局部 3D fallback 先在横梁下方建立柱身种子组件，再按 XY 半径回挂高层柱点；横梁不参与左右柱连通。
- 被地面过滤与 0.5 m 体素质心截去的柱脚，仅在最低观测点接近已知地面门限时恢复到地面门限；物理柱高门限 `[1.8, 4.0] m` 未放宽。
- 每根柱继续携带 detector 输入索引，运行结果通过体素 provenance 解析到原始 `(frame_index, point_index)`。
- Tracker 使用统一 association edge/score 决策与展示，使用 `device_time` 计算 dt；输出 `has_measurement`、`predicted`、`lost`、`last_measurement`、`track_segment_id` 和 `time_quality`。
- Synthetic case 文件保存输入 SHA-256、逐帧 Stage trace、association edges、失败原因、真值和输出；`summary.json` 保存 config version、运行环境及完整 GOAL 源码哈希。

## 指定回归结果

运行命令：

```powershell
.\web\.venv\Scripts\python.exe tools\run_goal_synthetic_suite.py `
  --output Plan\evidence\goal-demo\20260920-p3-layered-tracker-release-v2 `
  --scenario static --scenario translation_yaw `
  --seed 0 --seed 1000 --frames 200
```

| 场景 / seed | precision | recall | 中心 P95 m | 宽度 P95 m | 高度 P95 m | ID switch |
|---|---:|---:|---:|---:|---:|---:|
| static / 0 | 1.000 | 1.000 | 0.0163 | 0.0466 | 0.0441 | 0 |
| static / 1000 | 1.000 | 1.000 | 0.0163 | 0.0450 | 0.0421 | 0 |
| translation_yaw / 0 | 1.000 | 1.000 | 0.0425 | 0.1599 | 0.0471 | 0 |
| translation_yaw / 1000 | 1.000 | 1.000 | 0.0469 | 0.1654 | 0.0450 | 0 |

四项均满足常规场景临时门禁。另行开发 smoke 显示 density_drop 与 missing_crossbar 两个 seed 均达到 100% recall；pitch_roll 仍只有 0.545–0.585 recall，继续作为困难场景 FAIL 保留。disappear_reenter 加入 provenance 新鲜度后，不可见区间错误测量与错误确认均为 0，重入 ID switch 为 0；seed 0 recall 0.94，仍需继续归因。

## 验证

- 指定测试：63 passed。
- Web 后端全量：257 passed，2 个第三方弃用警告。
- 本报告只关闭指定常规 Synthetic case，不关闭真实 PCAP 零命中和 pitch/roll 失败。

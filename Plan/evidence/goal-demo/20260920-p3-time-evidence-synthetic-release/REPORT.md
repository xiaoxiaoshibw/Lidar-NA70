# P3 时间证据契约 Synthetic 回归

日期：2026-09-20。状态：部分通过；P3 继续 IN_PROGRESS（pitch_roll 仍 FAIL）。

运行命令：

```powershell
.\web\.venv\Scripts\python.exe tools\run_goal_synthetic_suite.py `
  --output Plan\evidence\goal-demo\20260920-p3-time-evidence-synthetic-release `
  --scenario static --scenario translation_yaw --scenario disappear_reenter `
  --scenario density_drop --scenario missing_crossbar --scenario pitch_roll `
  --scenario tree_wall_fence --seed 0 --seed 1000 --frames 200
```

`summary.json` 和各场景 JSON 保存输入 SHA-256、配置版本、GOAL 源码哈希、逐帧
`device_time`、候选 provenance 时间字段、`observation_signature`、Stage trace、
association 与真值评价。

以下数值均为 `precision / recall`。

| 场景 | seed 0 | seed 1000 | 结论 |
|---|---:|---:|---|
| static | 1.000 / 1.000 | 1.000 / 1.000 | PASS |
| translation_yaw | 1.000 / 1.000 | 1.000 / 1.000 | PASS |
| density_drop | 1.000 / 1.000 | 1.000 / 1.000 | PASS |
| missing_crossbar | 1.000 / 1.000 | 1.000 / 1.000 | PASS |
| tree_wall_fence | 0.980 / 1.000 | 0.985 / 1.000 | PASS（保留漏检） |
| disappear_reenter | 0.933 / 1.000 | 0.987 / 1.000 | PASS（不可见区间错误 measurement=0、ID switch=0） |
| pitch_roll | 0.585 / 1.000 | 0.545 / 1.000 | FAIL，保留事实 |

其中 `disappear_reenter` 的 `absence_measurement_frames` 和
`false_confirmed_frames_while_absent` 均为 0。

本轮没有使用真值驱动检测，也没有放宽几何门限；历史重复窗口只能成为
`PREDICTED_RECENT`，不能设置 `has_measurement` 或输出 `selected_measurement`。

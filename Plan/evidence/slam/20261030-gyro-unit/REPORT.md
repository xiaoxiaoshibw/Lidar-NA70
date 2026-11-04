# 陀螺单位一致性检查（设备内部对照，结论 INCONCLUSIVE）

- 会话：`20261030-155552`（A2 浏览器 60 s 工程录制；不是 B1 静置基线）。
- 源文件：`radar_capture_data/na70_sessions/a2-browser-155552/imu_frames.jsonl`，SHA-256 `f1ac58767e76e0020f4896f0cb3f532429c551e6b05429a3c9a37223d79dd3ef`。
- 方法：用 YESENSE 自带四元数在相邻主机批次间的增量求角速度，与同区间平均陀螺对照。若陀螺为 deg/s，比例应≈1；若为 rad/s，比例应≈57.3。

```powershell
web\.venv\Scripts\python.exe tools\verify_na70_gyro_unit.py `
  --frames radar_capture_data\na70_sessions\a2-browser-155552\imu_frames.jsonl `
  --output Plan\evidence\slam\20261030-gyro-unit\gyro-unit.json

web\.venv\Scripts\python.exe tools\verify_na70_gyro_unit.py `
  --frames radar_capture_data\na70_sessions\a2-browser-155552\imu_frames.jsonl `
  --stride 5 --max-dt 2.0 `
  --output Plan\evidence\slam\20261030-gyro-unit\gyro-unit-stride5.json
```

## 结果

| 间隔 | 可用区间 | 选择约定 | 比例（quaternion deg/s ÷ recorded） | 方向余弦中位 | 判定 |
|---|---|---|---|---|---|
| 相邻批次（0.051 s） | 1,016 | body | 0.284 | 0.269 | INCONCLUSIVE |
| 每 5 批（0.255 s） | 89 | body | 0.261 | 0.479 | INCONCLUSIVE |

world 约定更差（0.097 / 0.100 与 0.192 / 0.183）。1,180 个批次、1,179 个相邻区间全部通过时间与范围检查。

## 解读

- 比例既不是 1，也不是 57.3；因此这段数据**不能确认 deg/s，也不能判为 rad/s**。
- 四元数导出的角速度约为陀螺的 1/4，且方向相关性低；拉长到 0.25 s 后相关性升到约 0.48，但比例仍约 0.26。这与“融合姿态被平滑/滤波，原始陀螺含大量振动高频”一致，而不是简单的单位错误。
- 该会话是雷达工作振动段，不是刻意的平滑转动；四元数不是原始角速度基准。

## 结论与下一步

- 陀螺单位仍为**推断 deg/s**，本次检查未升级该状态；不能把结果写成“单位已验证”。
- 工具保留，待 B5 的温和三轴平滑转动数据上重跑；若届时比例≈1 且方向一致，才可写 `DEG_S_CONFIRMED`。
- 若要现在确认单位，需要独立角速度基准（转台/高精度陀螺）或厂家书面参数，不能用设备融合姿态冒充。

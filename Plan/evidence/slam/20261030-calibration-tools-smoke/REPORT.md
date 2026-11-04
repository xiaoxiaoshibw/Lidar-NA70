# 标定工具真实会话 smoke（非标定结论）

- 会话：`20261030-155552`（A2 浏览器工程录制，60 s；不是 B1 静置基线）。
- 源文件：`radar_capture_data/na70_sessions/a2-browser-155552/imu_frames.jsonl`，3,176,739 字节，SHA-256 `f1ac58767e76e0020f4896f0cb3f532429c551e6b05429a3c9a37223d79dd3ef`。
- 命令：

```powershell
web\.venv\Scripts\python.exe tools\analyze_na70_imu_run.py `
  radar_capture_data\na70_sessions\a2-browser-155552\imu_frames.jsonl `
  --output Plan\evidence\slam\20261030-calibration-tools-smoke\imu-run.json
```

结果（`imu-run.json`）：

- 12,018 帧，1 个 segment，主机跨度 60.0415 s。
- 6 个 10 秒窗口全部 `REJECTED`，失败项均为 `accel_axis_std`、`accel_norm_std`、`gyro_axis_std`；与既有静置诊断一致。
- Allan：`NOT_EVALUATED`，原因 `segment_windows_not_static_like`。运动段不做 Allan。
- **主机接收时间批量到达**：12,018 帧中 10,838 个相邻时间戳相同；正间隔中位数 0.050907 s，最大 0.053003 s，按唯一到达时刻折算约 19.6 次到达/秒（每批约 10 帧）。`gaps_over_1_5_median = 0`。
- 该批量到达说明：主机接收时间被量化到约 50 ms 的批次，不能用于毫秒级 IMU—LiDAR 时间对齐；这不是丢帧证明，也不是设备采样周期的证明。

结论与边界：

- 本会话没有保持静止，不能作为静置标定基线；窗口拒绝不区分振动、移动或其他噪声。
- 工具链在真实 JSONL 上可运行并保留了原始数值；本次不验证同步、标定、外参或场景准确度。
- 主机时间批量到达是下一步必须解决的输入事实；在取得设备采样时间或可验证的时钟映射前，时间偏移只能停留在 `NOT_EVALUATED`。

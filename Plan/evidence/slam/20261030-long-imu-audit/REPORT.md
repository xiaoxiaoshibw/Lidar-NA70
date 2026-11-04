# 10 分钟长录 IMU 审计（工作条件，非静置基线）

- 会话：`fixed-long-160920`，仅回传 `imu_frames.jsonl`（31,706,534 字节，120,017 帧）；无 PCAP/chunks，因此本报告只覆盖 IMU。
- 命令：

```powershell
web\.venv\Scripts\python.exe tools\analyze_na70_imu_run.py `
  radar_capture_data\na70_sessions\fixed-long-160920\imu_frames.jsonl `
  --output Plan\evidence\slam\20261030-long-imu-audit\imu-run.json

web\.venv\Scripts\python.exe tools\audit_na70_imu_ranges.py `
  --frames radar_capture_data\na70_sessions\fixed-long-160920\imu_frames.jsonl `
  --output Plan\evidence\slam\20261030-long-imu-audit\ranges.json
```

## 结果

| 项目 | 数值 |
|---|---|
| 帧数 | 120,017 |
| 主机跨度 | 600.048 s |
| 唯一主机时刻 | 约 11,785 批（正间隔中位 50.9 ms） |
| 重复主机时刻 | 108,232 |
| 最大主机间隔 | 80.806 ms |
| 超过 1.5 倍中位间隔 | 2 |
| 10 s 静置窗口 | 60 个，**全部 REJECTED** |
| Allan | `NOT_EVALUATED`（segment 非静置） |
| 饱和事件 | 0 |
| 加速度最大绝对值 | z 10.202 m/s² |
| 角速度最大绝对值 | x 1.072、y 1.745、z 2.325 °/s（单位推断） |

## 解读

- 长录期间 IMU 持续落盘且可完整读取；10 分钟批次结构稳定，仍是约 20 次到达/秒、每批约 10 帧的主机时间。
- 全部静置窗口被拒绝，说明整段不是静置基线；不能据此计算偏置稳定度或 Allan。长录中出现的 2 个较大间隔（最大 80.8 ms）需结合采集事件与设备重连记录判断，不能直接当丢帧。
- 本段无饱和证据；但厂家量程未知，仍只能表述为“未观察到数值满量程/平台”。

## 边界

- 本报告不验证设备采样时间、同步、标定或场景准确度。
- 没有 PCAP/chunks，无法做原始字节对账或主机端点重叠；这些检查需完整会话目录。
- 长录只证明工具能处理 12 万帧并保留全部样本，不证明现场静止或硬件长期稳定。

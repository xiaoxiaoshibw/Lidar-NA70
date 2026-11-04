# LIO 扫描包 smoke（真实 PCAP 前 5 帧，非 SLAM 结果）

- 会话：`20261030-155552`（A2 浏览器 60 s 工程录制；不是 B1 静置基线）。
- 输入：
  - PCAP SHA-256 `f847d6aa23e706788cdeee6bb610010c19073b36c4fadcbfb8ec1eb173b0b62e`
  - IMU JSONL SHA-256 `f1ac58767e76e0020f4896f0cb3f532429c551e6b05429a3c9a37223d79dd3ef`
- 命令：

```powershell
web\.venv\Scripts\python.exe tools\export_na70_lio_scans.py `
  --pcap radar_capture_data\na70_sessions\a2-browser-155552\lidar.pcap `
  --imu-frames radar_capture_data\na70_sessions\a2-browser-155552\imu_frames.jsonl `
  --max-frames 5 `
  --output Plan\evidence\slam\20261030-lio-package-smoke
```

## 产物

| 文件 | 说明 |
|---|---|
| `scans.npz` | 100,000 点；`point_time_s` 包级相对时间；`frame_index/packet_index/point_index` 源引用；`frame_offsets` |
| `imu.npz` | 122 个窗口内 IMU 样本；主机时间、gyro/accel；`has_device_time=false` |
| `manifest.json` | 时间质量、阻塞项、文件哈希 |
| `REPORT.md` | 本说明 |

包级时间质量：5 帧、500 包、200 点/包，最大包间隔 1.147 ms，`PACKET_APPROXIMATION`。

## 阻塞项（不得绕过）

- `time_level_C`：IMU 只有主机接收时间，批次约 53 ms，不能作为设备采样时间。
- `calibration_missing`：未提供 `T_imu_lidar` 候选。
- `motion_envelope_unverified`：未声明/验证速度、角速度与距离包络。
- `imu_does_not_bracket_scan`：**IMU 首样本比本段首雷达包晚 19.204 ms**。这是真实会话的启动次序边界，不是导出器补时间；该段不能声称 IMU 全程覆盖。

## 边界与下一步

- 本包不执行去畸变、坐标变换、SLAM 或回环；`lio_ready=false`。
- 若要在 LIO 中使用，必须选择 IMU 已覆盖的帧区间（例如从 IMU 首样本之后的帧开始）、提供外参候选并验证时间等级；当前时间等级 C 仍不满足动态验收。
- 逐点时间由包时间复制，不能宣称逐点硬件时间。

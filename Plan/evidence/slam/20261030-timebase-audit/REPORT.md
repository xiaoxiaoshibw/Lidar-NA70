# IMU 时间基审计：主机批量到达（非同步结论）

- 会话：`20261030-155552`（A2 浏览器 60 s 工程录制，不是 B1 静置基线）。
- 输入：
  - `radar_capture_data/na70_sessions/a2-browser-155552/imu_frames.jsonl`
  - `radar_capture_data/na70_sessions/a2-browser-155552/imu_chunks.jsonl`
- 命令：

```powershell
web\.venv\Scripts\python.exe tools\audit_na70_imu_timebase.py `
  --frames radar_capture_data\na70_sessions\a2-browser-155552\imu_frames.jsonl `
  --chunks radar_capture_data\na70_sessions\a2-browser-155552\imu_chunks.jsonl `
  --output Plan\evidence\slam\20261030-timebase-audit\timebase.json
```

## 结果

| 项目 | 数值 |
|---|---|
| 帧数 | 12,018 |
| 唯一主机接收时刻（批次） | 1,180 |
| 每批帧数 | 10–11（中位 10） |
| 批次间隔中位 | 50.907 ms |
| 批次间隔 P95 | 51.044 ms |
| 批次间隔最大 | 53.003 ms |
| 主机跨度 | 60.0415 s |
| `sample_us` / `ready_us` 字段 | 0 / 0（不存在） |
| tid 断号事件 | 0 |
| 估计缺失帧 | 0 |
| 60000→1 边界 | 0 |
| 时间等级 | **C：仅主机接收时间** |

`timebase.json` 的 `host_time_bound.conservative_s = 0.053003`：一批 10 帧共享同一接收时刻，批内真实采样时刻可能落在“上一批到达”到“本批到达”之间。这是保守上界，不是同步误差，也不是设备采样周期。

## 对误差预算的直接含义

按 SLAM 计划的一阶估计 `e ≈ (v + r·|ω|)·δt`：

- `v=0.5 m/s, r=50 m, ω=10°/s, δt=53 ms`：`e ≈ 0.49 m`，远超 0.02 m 设计预算。
- 更温和的 `v=0.1 m/s, r=10 m, ω=5°/s`：`e ≈ 0.052 m`，仍超过 0.02 m。

因此：

- 主机接收时间不能支撑动态标定（B5）或移动 SLAM 的时间对齐。
- 静态批次（B1 试录、B2 静置、B3 六面、B4 墙面）不依赖毫秒级时间同步，可以在 A2 入口就绪后推进；但它们仍不能给出时间偏移。
- 要进入 B5，必须先取得设备采样时间、可验证的时钟映射或硬件同步；不得把 53 ms 的批量上界写成“已同步”。

## 边界

- 本审计不解析原始字节；原始字节与 JSONL 的逐项对账由 `tools/audit_na70_imu.py` 负责。
- tid 无断号只说明序号连续，不证明没有丢帧，也不证明采样时刻正确。
- 当前固件不输出 0x51/0x52；若后续固件或配置改变，本工具会把字段出现如实报告为 `OK_DEVICE_TIME_PRESENT`，但仍需独立验证时间语义后才能升级时间等级。

## 资料核对（只读，未配置设备）

- [yesense_ros_driver `analysis_data.c`](https://github.com/HViktorTsoi/yesense_ros_driver/blob/master/src/analysis_data.c) 定义 `0x51` 为 sample timestamp、`0x52` 为 data-ready timestamp，各 4 字节；这与本项目解析器一致。
- [第三方上位机说明](https://whu-robocon-location.github.io/_posts/2021-03-28-yesense/)提到 `Tool -> Configuration -> Output Configuration`，可用于波特率等配置；该文没有说明本型号能否开启 0x51，也没有给出命令帧格式。
- 因此“能否通过厂家上位机/命令开启设备时间”仍是 **未确认**。禁止按其他型号或猜测的命令帧盲发配置；需要厂家资料或用户在上位机中明确看到输出项后再验证。
- 若设备时间出现，`tools/export_na70_imu_device_csv.py` 会拒绝缺字段、倒退和非回绕情况，并生成可交给 LIO 预检的七列 CSV；字段存在仍不等于与雷达同步。

# Thin-wire W1 现场采集卡

本卡用于 [细线回波验证与逐级开发计划](../Plan/细线回波验证与逐级开发计划.md) 的 W1。目标是回答“固定雷达、已知细线位置时，原始数据中是否存在可归属、可重复的回波”。本卡不包含线拟合、自动识别或 200 米能力结论。

## 1. 推荐的首批条件

- 从现场能可靠布置和测量的近距离开始，建议约 20 米；填写实测值及误差。
- 雷达、支架和细线全程固定。先确定安装方向，再启动录制。
- 细线尽量横跨多个扫描方向，记录实际朝向；不要只写“横向”或“竖向”。
- 同一条件采集 3 次目标录制，每次至少 30 秒。
- 在不移动雷达、不改变设置的前提下移除细线，再采集 3 次同位置背景录制，每次至少 30 秒。
- 若目标不能移除，相邻空区域只能作为补充背景，必须注明“非同位置无线对照”，不能据此单独确认目标回波。

建议文件名：

```text
w1-20m-target-01.pcap
w1-20m-target-02.pcap
w1-20m-target-03.pcap
w1-20m-background-01.pcap
w1-20m-background-02.pcap
w1-20m-background-03.pcap
```

## 2. 现场必须记录

以下字段未知时写 `null` 并说明原因，不按经验补值。

| 项目 | 本次填写 |
|---|---|
| 采集日期、地点 |  |
| 雷达型号、设备/固件标识 |  |
| 安装方向 `install_direction`（1 或 2） |  |
| 雷达固定方式及未移动依据 |  |
| 目标固定方式及未移动依据 |  |
| 雷达设置、供电和网络设置 |  |
| 细线材料与表面 |  |
| 实测线径 / mm 与测量误差 |  |
| 可见线段长度 / m |  |
| 实测最近距离 / m 与误差 |  |
| 细线朝向、垂度和遮挡 |  |
| 现场天气、光照及其他变化 |  |
| 目标录制与背景录制的配对依据 |  |

保存至少一张能同时看见雷达、细线和测量参考点的现场照片。照片只说明场景，不能替代三维位置。

## 3. 独立目标位置

在 `lidar_link` 中给出可见细线段的两个端点：

```text
P0 = [x0, y0, z0] m
P1 = [x1, y1, z1] m
position_uncertainty = ... m
measurement_source = 测量工具、参考点、坐标换算过程
```

端点必须来自现场测量或独立参考点换算，不能从点云中先找线再反填。若暂时只能获得现场坐标，保留现场坐标、雷达原点及轴向测量，待转换过程复核后再将 `review_status` 改为 `reviewed`。

目标 ROI 应覆盖端点、位置误差和合理回波扩散，但不能大到包含大量无关背景。建议先按独立测量设置 ROI，再查看输出。`corridor_radius_m` 至少覆盖位置不确定度，并记录选取依据。

## 4. 录制清单

| 录制 | PCAP 路径 | 角色 | 时长≥30 s | 雷达固定 | 设置一致 | 目标状态 | 备注 |
|---|---|---|---|---|---|---|---|
| T1 |  | target_present |  |  |  | 已安装 |  |
| T2 |  | target_present |  |  |  | 已安装 |  |
| T3 |  | target_present |  |  |  | 已安装 |  |
| B1 |  | target_absent |  |  |  | 已移除 |  |
| B2 |  | target_absent |  |  |  | 已移除 |  |
| B3 |  | target_absent |  |  |  | 已移除 |  |

每个目标录制应对应一个同机位、同设置的背景录制。PCAP 不要改名覆盖原文件；保留原始文件及 SHA-256 身份。

## 5. 实验配置

复制 [thin_wire_config.yaml](../tools/thin_wire_config.yaml)，为每一对目标/背景录制保存一份配置。至少填写：

```json
{
  "install_direction": 1,
  "target_roi": [0, 1, 0, 1, 0, 1],
  "background_rois": [],
  "target_reference": {
    "endpoints_sensor_m": [[0, 0, 0], [1, 0, 0]],
    "corridor_radius_m": 0.2,
    "position_uncertainty_m": 0.1,
    "source": "填写独立测量与坐标转换",
    "review_status": "reviewed"
  },
  "acquisition": {
    "data_label": "REAL_PCAP_DIAGNOSTIC",
    "capture_role": "target_present",
    "static_status": "confirmed",
    "static_evidence": "填写固定依据",
    "target_distance_m": 20.0,
    "wire_diameter_mm": 20.0,
    "wire_material": "填写材料",
    "wire_orientation": "填写传感器坐标中的方向/现场依据",
    "sensor_settings": "填写设备设置",
    "background_pairing_evidence": "填写对应背景录制及机位/设置一致依据",
    "notes": "填写遮挡、垂度和现场变化"
  }
}
```

示例中的坐标、20 米和 20 mm 都是格式占位，必须替换为实测值，不能直接用于正式证据。

## 6. 执行命令

在仓库根目录使用 PowerShell；每次使用新的输出目录：

```powershell
& .\web\.venv\Scripts\python.exe tools\audit_thin_wire.py `
  --pcap radar_capture_data\thin-wire\w1-20m-target-01.pcap `
  --background-pcap radar_capture_data\thin-wire\w1-20m-background-01.pcap `
  --config tools\thin-wire-w1-20m-pair01.json `
  --output Plan\evidence\thin-wire\w1-20m-pair01
```

默认选择每份录制的最后一帧作为单帧及 0.5/1/2/5 秒窗口终点。若末尾发生人员遮挡、操作设备或移除目标，应使用 `--end-frame` 和 `--background-end-frame` 选择静止区间，并在记录中说明依据。

## 7. W1 复核结论

每对录制先检查时间质量、目标是否在扫描覆盖内、ROI/参考走廊的原始点和逐包出现情况，再比较背景。三次重复分别给出结果，不将重叠窗口当成独立重复。

- `可信回波已观察到`：独立位置一致、重复录制可复现，并能由同位置背景对照支持归属。
- `证据不足`：位置、扫描覆盖、静止或背景条件不充分。
- `本条件下未观察到可信回波`：核对方向、距离、设置并重复录制后仍无可归属支撑。

只有第一种结论才进入 W2 距离阶梯。三种结论都必须保留原始数据和失败项。

# W1 原始回波诊断

数据标签：REAL_PCAP_DIAGNOSTIC。

**工具已输出原始数据；细线回波归属 NOT_EVALUATED。ROI/参考走廊内点不自动认定为目标回波。**

原始 NPZ 未经过距离/高度/强度过滤或体素降采样；零距离射线保留，须结合 valid_mask 使用。

| 输入/窗口 | 请求秒数 | 实际跨度/s | 时间质量 | 有效点 | 目标ROI点 | 参考走廊点 |
|---|---:|---:|---|---:|---:|---:|
| primary-f000050-single | 单帧 | 0.099000 | ok | 6214 | 未配置 | 未配置 |
| primary-f000050-0.5s | 0.5 | 0.499000 | ok | 35329 | 未配置 | 未配置 |
| primary-f000050-1s | 1.0 | 0.999000 | ok | 69441 | 未配置 | 未配置 |
| primary-f000050-2s | 2.0 | 1.999000 | ok | 137536 | 未配置 | 未配置 |
| primary-f000050-5s | 5.0 | 4.999000 | ok | 347541 | 未配置 | 未配置 |

## 证据缺口

- target_roi_missing
- independent_target_position_not_reviewed
- fixed_sensor_and_target_not_confirmed
- target_presence_not_confirmed
- wire_diameter_missing
- same_site_background_pair_not_verified
- independent_repeat_recordings_require_review
- target_attribution_requires_review

## 来源与查看

- manifest.json：输入/源码/config 哈希、完整命令、环境、扫描质量和全部产物路径。
- window_metrics.jsonl / packet_metrics.jsonl：窗口及逐包原始统计；重叠窗不可作为独立试验累加。
- raw_windows/：完整射线、原始码、包时间、原始帧/点索引与掩码；allow_pickle=False 读取。
- figures/：全云/局部三视图与逐包计数；图形抽样不改变 NPZ 或统计。

单个 100 包帧不等于完整扫描；同包所有射线共享包时间。时间跳变会分段，缺包数未知时保留 null。
背景 PCAP 按自身设备时间独立选窗；配对条件由现场证据确认，不直接用计数差认定目标。

下一步：核实目标位置、静止及背景配对，并在重复录制上复核回波归属。未完成前保持 W1 未验收。

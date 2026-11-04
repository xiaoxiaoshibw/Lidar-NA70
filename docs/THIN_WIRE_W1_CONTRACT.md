# Thin-wire W1 原始回波证据契约 v1

日期：2026-09-21。范围：[细线计划](../Plan/细线回波验证与逐级开发计划.md) W1 的离线原始数据工具；不包含线拟合、自动识别或跟踪。

## 1. 输入与配置

入口为 `tools/audit_thin_wire.py`。每次处理一个目标/待确认 PCAP，可另带同机位背景 PCAP。正式解码使用 `backend.decoder`，仅支持现有经典 PCAP 协议；每个 PCAP 独立扫描、时间分段和统计。

`tools/thin_wire_config.yaml` 使用 JSON 兼容的 YAML 子集，复用项目现有方式，不引入额外 YAML 依赖。窗口、时间跳变、点数/图形预算均从配置读取；禁止自动套用球门配置。

- `target_roi` 与 `background_rois` 为传感器坐标轴对齐框 `[xmin,xmax,ymin,ymax,zmin,zmax]`，均以米为单位；不允许相互重叠。未提供 ROI 时可导出全原始云，但无目标区域评价。
- `target_reference` 可为 null，或 `{endpoints_sensor_m:[[x,y,z],[x,y,z]], corridor_radius_m, position_uncertainty_m, source, review_status}`。端点须不同，坐标在 sensor/lidar_link 中；`source` 说明独立测量与坐标转换，`review_status` 为 reviewed/unreviewed。走廊仅统计与给定有限参考段一致的点，不能证明这些点属于目标。
- `acquisition` 保存真实/SYNTHETIC 标签、目标存在/移除/未知、静止依据、距离/线径/材质/朝向/设置和背景配对依据。未知项为 null；声明静止必须提供依据。
- 原始数据不作距离/高度/强度过滤或体素降采样。有限值且距离码非零为有效回波；无效射线同样导出，零码不解释为目标距离。后续解码协议若增加无效码定义，须同步升级契约。

## 2. 时间窗与原始身份

终点使用显式 `--end-frame`（可重复），默认各输入最后一帧。`--background-end-frame` 可独立选择背景帧；不是按绝对设备时间配对。

单帧保留该 100 包逻辑帧（尾帧可不足）的所有射线。按秒窗口使用终点帧最后一个包的设备时间 T，选择采集顺序不晚于终点、同一连续设备时间段内的包，区间为 `(T-duration,T]`。每包 200 条射线共享包时间，不插值伪造逐点时间。

设备时间缺失、倒退或相邻有效包间隔大于 `max_packet_gap_s` 时断开时间段；相等包时间允许存在并统计，因为时间戳粒度有限。按秒窗口不跨段；设备时间无效的终点输出 `invalid_end_time` 空窗口，不回退到 PCAP 捕获时间。单帧可保留异常时间包，但标明质量问题。

`partial_window` 表示可用时间段没有覆盖请求左边界；记录请求时长与实际所选首末包跨度，二者不混用。设备时间跨度不能推导确切丢包数；该字段保持 null，另记异常记录数、时间间隔及段边界原因。重复终点拒绝，重叠窗列为同一录制的相关观测，不视为独立试验。

## 3. 输出

每次输出目录必须不存在，避免覆盖旧证据。`manifest.json` 保存 schema、状态、输入/源码/config SHA-256、完整命令、运行环境、原始配置、输入扫描质量和产物清单；失败时尽可能保留 FAILED 状态与错误。大于资源预算的窗口明确拒绝，不截断原始证据冒充完整云。

每个窗口的 `raw_windows/<window_id>.npz` 保留：

| 字段 | 含义 |
|---|---|
| `points` | N×4 float32，正式解码 XYZ/intensity，包括无效零距离射线的解码值；必须结合 valid_mask 使用 |
| `codes` | N×4 uint16，距离/俯仰/方位/强度原始码 |
| `frame_indices`, `point_indices` | PCAP 内原始帧号及该帧射线下标，不因筛选重新编号 |
| `packet_indices` | PCAP 内有效 NA70 包顺序号；不是设备包序列号 |
| `device_times`, `capture_times` | 包时间重复至该包所有射线；缺设备时间为 NaN |
| `valid_mask` | 有效回波掩码 |
| `target_roi_mask`, `background_roi_mask` | 有效点在人工目标框/任一背景框内的掩码 |
| `reference_corridor_mask` | 目标框内、距有限参考段不超过配置走廊半径的有效点；是位置一致候选，不是已确认细线回波 |

不使用 object 数组，可由 `np.load(..., allow_pickle=False)` 读取。capture 身份、坐标系、安装方向与文件 SHA 通过窗口行和 manifest 绑定；原始 PCAP 保留在原路径。

`window_metrics.jsonl` 包含包/帧索引范围、窗口时间/质量、射线数/无效数、ROI 内外数、不同来源帧/包数、首末支撑包时间、相对窗口终点的最新来源年龄、未标定强度分位数及参考段距离分位数。异常/跨段单帧的来源年龄为 null，不用其他时钟补齐。`packet_metrics.jsonl` 每个窗口逐包给出原始/ROI/走廊计数，记录采集顺序，不能把重叠窗行求和作为独立回波统计。

目标归属字段 `confirmed_target_return_count=null`、`wire_return_status=NOT_EVALUATED` 始终保留，工具不根据 ROI 有点、参考残差或背景差值自动认定细线。目标不存在/背景区域同样可有位置一致点，必须复核。背景比较只报告同配置计数和实际时长，不输出传感器探测概率。

`figures/` 输出全原始有效点与局部 ROI 的三视图、逐包时间/计数图。只允许明确标注的渲染抽样；NPZ 和统计不抽样。参考段、ROI 点与位置一致点使用不同图例。报告标注实际时长、时间质量与 SYNTHETIC/REAL 标签。

## 4. 验收边界

工具测试覆盖原始解码/索引对应、20/200 米点与低高度保留、无效码、时间跳变/缺失/边界/不满窗口、ROI/背景独立性、资源拒绝、配置非法值和独立输入隔离。工程通过不代表 W1 真实回波通过。

未提供目标位置、静止依据或有效背景对照时，列出证据缺口；即使信息齐全，最终归属仍需独立复核与重复录制。W1 实验结论由真实证据报告给出，不由本工具自动升级 PASS。

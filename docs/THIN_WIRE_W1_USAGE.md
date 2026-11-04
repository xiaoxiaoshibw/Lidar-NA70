# W1 原始回波工具使用说明

入口：[audit_thin_wire.py](../tools/audit_thin_wire.py)。范围为固定雷达、已知位置目标的原始数据检查，输出不自动确认细线。

现场采集和必填信息见 [W1 现场采集卡](THIN_WIRE_W1_CAPTURE_CARD.md)。建议先完成采集卡，再复制配置运行工具，避免导出后才发现目标坐标、静止依据或背景配对缺失。

## 1. 启动与输入

使用现有 `web/.venv`。绘图依赖 Matplotlib 已加入 `web/requirements.in` 与锁文件；新环境随 `web/start.ps1` 安装锁定依赖，已有环境缺依赖时在仓库根目录运行 `web/.venv/Scripts/python.exe -m pip install -r web/requirements.lock`。

先复制 [thin_wire_config.yaml](../tools/thin_wire_config.yaml) 为本次实验配置，再填写实际采集条件。默认未指定目标位置，适用于原始全云诊断，不形成目标回波结论。

- `target_roi`：目标观察框 `[xmin,xmax,ymin,ymax,zmin,zmax]`，单位米，坐标为正式解码后的 `lidar_link`。
- `background_rois`：同一录制的背景框列表，不能与目标框或彼此重叠。
- `target_reference`：按 [数据契约](THIN_WIRE_W1_CONTRACT.md) 填独立测量的端点、位置误差、走廊半径和来源；未测量保持 null，不从拟合结果反填。
- `acquisition`：填写距离、线径、材料、朝向、静止依据、设备设置及有线/无线背景配对依据。未知不猜填。
- `window_seconds`：默认 `[0.5,1,2,5]`，另输出单帧。全程无体素化、无球门距离/高度过滤。

## 2. 执行

Windows PowerShell，仓库根目录。下例仅使用既有 `1.pcap` 验证工具，不声称它是细线录制；`--output` 必须换成尚不存在的新目录：

```powershell
& .\web\.venv\Scripts\python.exe tools\audit_thin_wire.py `
  --pcap radar_capture_data\1.pcap `
  --config tools\thin_wire_config.yaml `
  --end-frame 50 `
  --output Plan\evidence\thin-wire\my-w1-inspection
```

真实实验将 `--pcap` 和 `--config` 换为对应采集与实验配置。`--end-frame` 可重复以选择多个终点；省略时为最后一帧。`--background-pcap` 可指定独立同机位无线录制，`--background-end-frame` 独立指定其终点，省略时为背景最后一帧。相邻背景 ROI 不能替代有线/无线成对录制。

设备时间缺失/倒退/间隔过大时分段，不跨段凑足秒数；短于请求时长会标 partial_window。预算不足会失败并保留错误，不悄悄截断点云。现有球门脚本的 `--voxel 0` 不适用于本工具，也不表示原始模式。

## 3. 看什么

先读 `REPORT.md` 中的时间质量和缺口，再对照 `figures/` 三视图、目标位置/走廊范围、背景和逐包时间计数。图中参考走廊矩形是投影包围范围，实际距离判断使用有限三维线段；ROI 或走廊有点不等于已确认细线。图有明确抽样标记，原始导出及计数不抽样。

原始点检查示例（将路径替换为本次窗口）：

```python
import numpy as np
raw = np.load("raw_windows/primary-f000050-0.5s.npz", allow_pickle=False)
valid = raw["valid_mask"]
points = raw["points"][valid]  # XYZ/intensity
refs = np.column_stack((raw["frame_indices"][valid], raw["point_indices"][valid]))
packet_times = raw["device_times"][valid]  # 同包所有射线共享包时间
```

零距离射线保留在 NPZ 中且 `valid_mask=false`；其原始角度/强度码、包时间仍可查。使用完整 mask，不能把雷达原点的零距离解码值当成目标点。不同 PCAP 通过 manifest 的 SHA/capture_id 区分，窗口重复来源不算独立重复试验。

最后依据独立位置、背景和重复录制给出“可信回波已观察到 / 证据不足 / 本条件下未观察到”的人工复核结论。程序保持 `wire_return_status=NOT_EVALUATED`，工程跑通不自动将 W1 标 PASS。

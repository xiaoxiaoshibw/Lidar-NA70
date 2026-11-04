# S0 合成户外场景与 NA70 扫描覆盖审计

日期：2026-09-24。状态：**S0 工程审计完成；NA70 导线回波归属 NOT_EVALUATED；机载安装外参 NOT_MEASURED**。

## 1. 交付

- 场景定义：`tools/scenarios/uav_single_wire_outdoor_v1.json`；几何与射线核心：`web/backend/wire_scene.py`；运行入口：`tools/run_wire_scene_s0.py`。
- 每组独立目录保存 `effective_scene.json`、`geometry_world.npz`（带物体标签的**展示几何**）、`scene_views.png`、`packet_opportunities.jsonl`、`summary.json` 和 `scene_manifest.json`（输入及代码哈希）。
- 四组证据：[20 m](../20260924-s0-release-20m/)、[50 m](../20260924-s0-release-50m/)、[100 m](../20260924-s0-release-100m/)和[无导线对照](../20260924-s0-release-absent/)。距离为合成场景起点到导线 `x` 平面的水平距离，不是 NA70 实测距离。

## 2. 输入与方法

运行环境：Windows 本机、`web/.venv/Scripts/python.exe`。输入 `radar_capture_data/1.pcap` 的前 60 个正式解码帧，合计 6000 包、1,200,000 条记录，设备时间 293.041–299.040 s。PCAP 只提供**真实 NA70 角码和包时间模式**；此录制没有本场景导线的独立真值。输入 SHA-256 及精确源码/配置哈希见每组 `scene_manifest.json`。

使用 `backend.decoder.scan_pcap` 和 `decode_frame_codes`，与 `decode_frame` 的角码/工厂偏移方向对齐。部分零距离记录连角码也为零，其发射方向不能恢复；排除这部分后才与合成导线圆柱半径作几何最近距离检验。**几何交会只表示在假设姿态下射线方向经过导线，不表示接收到导线反射。**

机载坐标换算采用明确的合成假设：正式解码安装方向 1 的 `-Y` 映射到场景 `+X`，再下俯 60°。此角度既未由无人机外参测量，也不是 NA70 厂商安装参数。起点和终点分别独立静止审计；终点在各场景均距导线 `x` 平面 13 m。这**不是运动补偿或飞行中的实时仿真**。导线直径 20 mm、抛物线下垂 1.5 m 也是合成参数；未模拟光斑、材质反射率、遮挡、噪声或测距概率。

执行命令（工作目录 `D:\Code\radar`）：

```powershell
.\web\.venv\Scripts\python.exe tools\run_wire_scene_s0.py --pcap radar_capture_data\1.pcap --output Plan\evidence\thin-wire\20260924-s0-release-20m --frames 60 --wire-range-m 20
.\web\.venv\Scripts\python.exe tools\run_wire_scene_s0.py --pcap radar_capture_data\1.pcap --output Plan\evidence\thin-wire\20260924-s0-release-50m --frames 60 --wire-range-m 50
.\web\.venv\Scripts\python.exe tools\run_wire_scene_s0.py --pcap radar_capture_data\1.pcap --output Plan\evidence\thin-wire\20260924-s0-release-100m --frames 60 --wire-range-m 100
.\web\.venv\Scripts\python.exe tools\run_wire_scene_s0.py --pcap radar_capture_data\1.pcap --output Plan\evidence\thin-wire\20260924-s0-release-absent --frames 60 --wire-range-m 50 --wire-absent
```

## 3. 审计结果

四组共享相同扫描模式：1,200,000 条记录中 526,290 条方向可恢复，673,710 条方向不可恢复；784,109 条为零距离码。可恢复方向中的零距离码仍可参与**方向覆盖**审计，不能计为有效测距。所选设备时间单调。

| 场景 | 起点几何交会射线 | 其中零距离码 | 其中非零距离码，但来源无法归属 | 终点几何交会射线 | 起点最后 0.5 / 1 / 2 / 5 s 交会 |
|---|---:|---:|---:|---:|---|
| 20 m | 349 | 4 | 345 | 835 | 41 / 64 / 127 / 297 |
| 50 m | 110 | 0 | 110 | 835 | 5 / 15 / 36 / 96 |
| 100 m | 32 | 0 | 32 | 835 | 2 / 4 / 7 / 27 |
| 无导线 | 0 | 0 | 0 | 0 | 0 / 0 / 0 / 0 |

上述非零距离码**来自原始 `1.pcap` 场景**，不能当作合成导线回波。窗口均以最后包设备时间为终点且不使用未来包；完整逐包计数、实际时间跨度、线段覆盖箱和最大缺口见 `summary.json`/`packet_opportunities.jsonl`。重复射线与重叠窗口没有当作独立试验。场景真值没有输入任何检测器，本轮没有 TP、召回率或导线有效距离结论。

## 4. 验证与限制

- `web` 下运行 `.\.venv\Scripts\python.exe -m pytest tests/test_wire_scene.py tests/test_decoder.py -q --tb=short`：**16 passed**。涵盖导线端点/中垂真值、固定 seed 可重复、无导线几何标签、射线命中/错过，以及真实 PCAP 上导出方向与正式解码点方向一致。
- 四组完整运行均成功生成清单、三视图、逐包及窗口统计；无导线组所有交会计数为 0。图像仅展示几何真值。
- 真实雷达在无人机上的姿态、外参、飞行运动和逐点时间仍未知。`1.pcap` 的角码模式不能替代未来实际安装后的扫描覆盖；方向未知记录也不能当作“已照射但未测到”。
- 若换安装角度、线径、垂度、距离或时窗，上表将变化。角码步长不等于光束发散或硬件线检出能力。

## 5. 下一门禁

按 `docs/THIN_WIRE_W1_CAPTURE_CARD.md` 准备固定雷达、独立测量端点/线径、三次目标及三次同机位移除目标背景录制，每次至少 30 s。取得该数据并复核安装外参后执行 S1；在此之前保持导线回波、距离与机载实时性 `NOT_EVALUATED`。

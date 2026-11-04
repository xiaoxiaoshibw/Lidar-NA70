# 20261003-p0：200 m 候选方位目录与 S0 远程扫描机会扩展（P0）

日期：2026-10-03。状态：**已运行并落盘**，性质为 `RAW_OBSERVATION` + `SYNTHETIC_OPPORTUNITY_AUDIT`，**不是**高压线识别证据，不构成真值。

## 1. 已运行命令

工作目录 `D:\Code\radar`（Windows 本机 Python 3.12）：

```powershell
# S0 扩展：150 m 与 200 m 导线平面审计（合成场景 + 真实 1.pcap 扫描方向）
.\web\.venv\Scripts\python.exe tools\run_wire_scene_s0.py --pcap radar_capture_data\1.pcap --output Plan\evidence\hv-line\20261003-p0\s0_150m --frames 60 --wire-range-m 150
.\web\.venv\Scripts\python.exe tools\run_wire_scene_s0.py --pcap radar_capture_data\1.pcap --output Plan\evidence\hv-line\20261003-p0\s0_200m --frames 60 --wire-range-m 200

# 静止 PCAP catalog：抽样 10 帧/步，统计 >150/>200/>300 m 原始回波与方向直方
.\web\.venv\Scripts\python.exe -c "<见 catalog 生成脚本，内部循环 scan_pcap/decode_frame，1/10 帧抽样>"
```

## 2. 产物

- `catalog/hv_candidate_catalog_v1.json`：4 个静止 PCAP 的 >150 />200 />300 m 原始回波散布统计、yaw/pitch 直方（5°/2° 箱）、距离分布箱和「每个捕获最密集 yaw 扇区」候选（RAW_OBSERVATION、review_status=unreviewed）。
- `s0_150m/`、`s0_200m/`：完整 `effective_scene.json`、`geometry_world.npz`、`scene_views.png`、`packet_opportunities.jsonl`、`summary.json`、`scene_manifest.json`。

## 3. 主要量化结果（合成扫描-机会）

1.pcap 前 60 帧（6000 包、1.2 M 射线、设备时间 293.041–299.040 s、安装方向 1、60° 下俯假设）：

| 距离档 | path_start 5 s 几何交会 | path_end 5 s 几何交会 | path_end 覆盖率 | path_start 包内至少有 1 交会计数 |
|---|---:|---:|---:|---:|
| 20 m（历史） | 297 | 835 | ~22–24% | 41 |
| 50 m（历史） | 96 | — | — | — |
| 100 m（历史） | 27 | — | — | — |
| 150 m（新增） | 9 | 706 | 24%（50 等距 m 箱） | 11 |
| 200 m（新增） | 10 | 706 | 24%（50 等距 m 箱） | 10 |

注：path_end 在原始 S0 定义里仍距线 x 平面 13 m，交会数恒定 835，不随 wire_range 线性变化；不能直接对比。值得关注的证据是 **path_start 远端在 5 s 内仅约 9–10 条方向经过 20 mm 细线圆柱**，这证实了设计稿中「几何机会随距离急剧下降」的判断。

## 4. catalog 结构观察（RAW_OBSERVATION，不是真值）

- 静止 4 PCAP 时长 66–130 s，200 m 估计级回波体量：1.pcap ~164 k、zuo ~216 k、zhong2 ~397 k、you ~440 k（>200 m → 全片线性缩放估算）。
- 远处回波集中在近方位（yaw 大约在 -10° 到 +10° 附近），pitch 中位数 3–5°（俯视策略下），中位距离所有 PCAP 都在 168–192 m——与场景树木/建筑物/电杆候选高度与姿态吻合，不是线状孤立体。
- 每个捕获的 `suspicious_examples` 是一处「值得 P2 做拟合实验」的 ROI 种子，不是「疑似高压线」声明。

## 5. 与 P1/P2 的关系

- 本目录给 P2 提供「先看哪些窗」的优先级 but **不提供真值**。P2/W3 的线段发现如果命中这些 sector，仍要等 P3 的独立测量或 reviewed 摄影/现场记录才能升级为 `LINE_WIRE`。
- 1.pcap 该录制是借用它做 S0（真实角码分布的借用），不是「其中真的有范围 150–200 m 的单根导线」。

## 6. 限制与下一步

- 目录用 1/10 帧抽样、精度 5°/2° 直方，覆盖率仅原始点级频率，没有逐点支持；只是工程指引。
- 下一步按 [高压线识别执行计划](../../../../高压线识别执行计划.md) P1–P2：先补 `windows.py` 包级因果窗对账，再做 W3 线段拟合与 stage trace。P3 需要用户提供实测 200 m 可控/真实录制；否则真实验收止步于「UNKNOWN_LINE 检出 + NOT_EVALUATED」。

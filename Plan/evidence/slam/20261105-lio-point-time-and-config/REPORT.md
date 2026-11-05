# NA70 LIO 逐点时间插值与 FOV 实测/配置修正证据

日期：2026-11-05
执行计划：[NA70_LIO算法优化AI执行计划.md](../../NA70_LIO算法优化AI执行计划.md)
性质：**SYNTHETIC/配置修正。不代表真实数据精度提升；动态 LIO 仍为 `DIAGNOSTIC_ONLY`。**

## 1. 改动清单

| 文件 | 改动 |
| --- | --- |
| `packages/na70_core/na70_core/lio_scans.py` | `build_scans` 新增 `point_time_mode="interpolated"`（默认 `"packet"` 行为不变）；新增常量 `POINT_INTERPOLATED_UNIFORM_PACKET_GAP`、`INTERPOLATED_LIMITATIONS`；`ScanSet` 增加 `time_mode` / `packet_time_counters`；`time_quality` 增加 `point_time_mode`、`packets_interpolated`、`packets_collapsed`、`packets_fallback` |
| `tools/export_na70_lio_scans.py` | 新增 `--point-time-mode {packet,interpolated}`（默认 packet）；manifest 顶层写 `point_time_mode`；REPORT/note 按模式区分 |
| `tools/measure_na70_fov.py` | 新增 FOV 实测工具（经典 PCAP 路径，`web/backend/decoder.py` 解码） |
| `tools/lio/na70_fast_lio.yaml` | `fov_degree` 360.0 → 35.0（实测）；`filter_size_surf/map` 0.3 → 0.15；`blind` 0.5 → 7.4；均带注释 |
| `web/tests/test_na70_lio_scans.py` | 新增 6 个插值模式测试 |
| `web/tests/test_na70_lio_export.py` | 新增 3 个导出模式/manifest 测试 |
| `web/tests/test_na70_measure_fov.py` | 新增 3 个 FOV 工具测试（SYNTHETIC PCAP） |
| `docs/NA70_LIO_INPUT_PREPARATION.md` | 追加"逐点时间模式"与"FOV 实测工具"两节（只增不删） |

## 2. T1 逐点时间插值（SYNTHETIC 回归）

规格：包 k 含 N=200 点、起始时间 t_k 时，`gap_k = t_{k+1} - t_k`（允许跨帧边界）；`gap_k > 0` 时点 i 时间 = `t_k + gap_k × i / N`；`gap_k <= 0`（含主机时间重复的观测事实）退化为共享 t_k（`packets_collapsed`）；会话末包沿用上一个正 gap，否则共享 t_k（`packets_fallback`）。三计数器之和等于总会话包数。

- 插值时间与规格公式逐位一致（`np.array_equal`），跨帧 gap>0 时严格递增；gap=0 无时间倒退；末包 fallback 计数正确。
- 默认 `point_time_mode="packet"` 与既有行为逐字节一致（回归红线），`save_npz`/`load_npz` 往返校验通过（含非递减校验）。
- 测试：`tests/test_na70_lio_scans.py`（6 个新测试）、`tests/test_na70_lio_export.py`（3 个新测试，含 CLI 参数传递与 manifest 字段）。
- 声明：插值是 `POINT_INTERPOLATED_UNIFORM_PACKET_GAP` 声明近似，**不是实测逐点时间**；包内真实采样时序仍未核实，不据此宣称去畸变能力。

## 3. T2 FOV 实测（真实 PCAP，MEASURED）

工具：`tools/measure_na70_fov.py`（有效回波口径：距离码 >0 且两角度码均非 0xFFFF；0xFFFF 与零码计数仅记录，厂家语义未核实）。

本机真实会话实测（输出在本目录，PCAP 均为工程会话，非专门静止采集）：

| 会话 | 时长/帧 | pitch 码范围 | pitch 跨度 | azimuth 码范围 | azimuth 跨度 |
| --- | --- | --- | --- | --- | --- |
| `20261104-170341` | 100.31 s / 834 帧 | 2133–3488 | 34.959° | 1–8333 | 359.942° |
| `20261105-162734-639900` | 70.98 s / 573 帧 | 2133–3488 | 34.959° | 1–8333 | 359.942° |

- 两个不同日期的独立会话 pitch 码范围完全一致（2133–3488），说明设备 pitch 包络已饱和，是设备级 FOV 而非场景偶然；azimuth 覆盖接近全 360°。
- 0xFFFF 射线计数：会话一 pitch 8 / yaw 8 / 全 FF 8；会话二 6/6/6——极少，但不宣称语义。零码（两角度码同为 0）射线数量大（会话一 9,778,424），其口径未核实，仅记录。
- 会话为工程录制而非 ≥60 s 静止专门采集；实测仍按"下界估计"对待。
- **注意**：计划背景中"NA70 实际 ~70°"的估计与本次实测 pitch 跨度 34.959° 不一致；本配置按计划取实测值。

## 4. `tools/lio/na70_fast_lio.yaml` 修正

- `fov_degree: 35.0`（原 360.0）：来自本目录实测 pitch 跨度 34.959° 向上取整到 5°，注释写明证据来源；azimuth ~360° 一并注明。
- `filter_size_surf/map: 0.15`（原 0.3）：室内 10–30 m 尺度假设（Mid-70 惯例 0.1–0.15），注释注明是尺度假设、非验收阈值。
- `blind: 7.4`（原 0.5）：注释引用 [近距下限证据](../20261030-near-range-floor/REPORT.md)（码 468 = 7.488 m，码 1–467 从未出现）。
- `extrinsic_est_en` 保持 false；单位外参未动，未伪造标定值。

## 5. 测试计数

- 基线（改动前）`pytest tests -q`：**578 passed / 2 failed / 3 skipped**；2 个失败为 `test_real_captures_random_access[1.pcap|2.pcap]` 的 FileNotFoundError（本机仓库不含这两个真实采集文件，环境性预存失败，与本计划无关）。
- 本计划新增测试：12 个（T1 9 个 + T2 工具 3 个）。

## 6. 改动后全量回归

- 改动后 `pytest tests -q`：**590 passed / 2 failed / 3 skipped**。较基线净增 12 passed，即本计划新增的 12 个测试全部通过；2 个失败仍为同一环境性预存失败（缺 `1.pcap`/`2.pcap`），与本计划无关。

## 6b. 可选步骤：WSL2 新配置重放前后对比（已执行）

本机存在 WSL2 Ubuntu 22.04/Humble 与已构建的固定 FAST-LIO（`cc3c9e8b…`，工作区未改动），因此执行了计划第 4.3 条可选步骤：同一数据段（`20261104-170341` 帧 10–109，200 万点，`--imu-margin-s 1.0`，默认 packet 模式）分别用旧配置（20261104 诊断，其目录未修改）与新配置重放。内部统计对比（位姿数 81→85、路径长度 43.507→30.312 m、位姿最大间隙 0.361→0.244 s 等）与完整边界声明见 [重放对比报告](../20261105-lio-config-replay/REPORT.md)。**结论仅限内部统计，不归因、不宣称改善**；动态 LIO 保持 `DIAGNOSTIC_ONLY`。

## 7. 边界声明

- 本目录全部为 SYNTHETIC 测试 + 真实 PCAP 的几何/统计实测；**不代表真实数据精度提升**。
- 未解决设备时间戳、陀螺单位、外参；动态 LIO 保持 `DIAGNOSTIC_ONLY`。
- 未修改任何 `20261104-*`、`20261105-*` 既有证据目录；FAST-LIO 工作区与其中的旧配置未改动，重放仅通过 `config_path` 指向新目录。
- 本计划范围内未向设备发送任何配置命令。

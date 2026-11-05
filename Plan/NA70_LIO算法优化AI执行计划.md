# NA70 LIO 算法优化 AI 执行计划

> 本文件是自包含的 AI 执行提示词。执行者应在不依赖其他会话上下文的情况下，仅凭本文件与仓库内文档完成全部任务。开始前**必须先读 `Plan/README.md`**（路线决策记录）与 `docs/NA70_LIO_INPUT_PREPARATION.md`。

## 0. 背景事实（已验证，勿重新推导，勿怀疑）

1. NA70 是双楔形棱镜非重复扫描雷达：100 个有效包 = 1 帧（10 Hz，20,000 射线/帧），每包 200 点；距离码 ×16 mm，pitch 码 ×0.0258°，azimuth 码 ×0.0432°。经典 PCAP 载荷 1410 字节 = 200 点 + 10 字节尾，`web/backend/decoder.py` 是唯一解码参考。
2. 实测近距下限 **7.488 m（距离码 468），码 1–467 从未出现**，属设备/数据侧下限（证据 `Plan/evidence/slam/20261030-near-range-floor/REPORT.md`）。
3. IMU（YESENSE，199.8 Hz）**无设备采样时间戳**，时间等级 C，仅有主机接收时间且批量到达；陀螺单位 deg/s 仍是推断。动态 LIO 状态为 `DIAGNOSTIC_ONLY`。
4. `packages/na70_core/na70_core/lio_scans.py` 当前把整包 200 点复制同一包时间，声明 `PACKET_APPROXIMATION`；实测帧内最大包间隔 8.004 ms（证据 `Plan/evidence/slam/20261104-lio-diagnostic/REPORT.md`）。
5. FAST-LIO 原型（WSL2，patch 后 lidar_type 5）已在 `20261104-lio-diagnostic` 跑通，但配置有明显问题：`fov_degree: 360.0`（NA70 实际 ~70°）、`filter_size_surf/map: 0.3`（对 10–30 m 室内过粗）、`blind: 0.5`（应不低于 7.4）。该诊断目录内的配置副本是历史证据，**不得修改**。
6. 距离码 0 与 0xFFFF（距离/角度全 FF）的有效性口径在 `decoder.py`、sweeps、scene 构建之间不一致；0xFFFF 的厂家语义未核实，只能作为候选假设处理，不得宣称。

## 1. 目标与非目标

**目标（本次全部交付）：**

- T1：`lio_scans.py` 新增显式声明的逐点时间插值模式（包内按点序插值），默认行为不变。
- T2：新增 NA70 FOV 实测工具，用实测结果修正 `tools/lio/na70_fast_lio.yaml`（fov_degree、filter_size、blind）。
- T3：以上改动全部通过 SYNTHETIC pytest 回归，并落盘证据目录与 `Plan/README.md` 条目。

**非目标（本次禁止做，仅记录门禁条件）：**

- 不解决设备时间戳、陀螺单位、外参——这三样是硬件/厂家/现场采集问题，软件侧借不来。
- 不重写 FAST-LIO、不引入 Point-LIO（门禁：T1/T2 完成且 IMU 时间基有改进后另行立项）。
- 不做多帧聚合、不做流式地图（`scene.py` 内存问题另行立项）。
- 不把任何动态 LIO 结果升级为"可用/已验收"；动态 LIO 保持 `DIAGNOSTIC_ONLY`。
- 不修改 `20261104-*`、`20261105-*` 等已有证据目录内的任何文件。

## 2. T1 逐点时间插值（`packages/na70_core/na70_core/lio_scans.py`）

### 2.1 规格

`build_scans(frames, *, points_per_packet=200, point_time_mode="packet")`：

- `"packet"`（默认）：现有行为逐字节不变（回归红线）。
- `"interpolated"`：对包 k（起始时间 t_k，含 N=200 点），令 `gap_k = t_{k+1} - t_k`（k+1 是会话内下一个包，**允许跨帧边界**）：
  - 若 `gap_k > 0`：点 i 的时间 = `t_k + gap_k × i / N`（i 从 0 起）。
  - 若 `gap_k <= 0`（含主机时间重复的观测事实）：该包退化为共享 t_k（与 packet 模式一致），计数 `packets_collapsed`。
  - 会话最后一个包没有 t_{k+1}：沿用上一个 gap（>0 时），否则共享 t_k，计数 `packets_fallback`。
- 由构造保证 `point_time_s` 非递减（`i/N < 1`，点时间严格小于下一包起点；gap<=0 时无插值）。`load_npz` 的非递减校验必须依然通过。
- 近似等级常量：`"POINT_INTERPOLATED_UNIFORM_PACKET_GAP"`，与 `"PACKET_APPROXIMATION"` 并存于声明中；`time_quality` 增加 `point_time_mode`、`packets_interpolated`、`packets_collapsed`、`packets_fallback` 字段；`LIMITATIONS` 相应更新（明确：插值是声明近似，不是实测逐点时间）。

### 2.2 配套

- `tools/export_na70_lio_scans.py` 增加 `--point-time-mode {packet,interpolated}`，默认 `packet`；manifest 写入所选模式。
- 相关文档同步：`docs/NA70_LIO_INPUT_PREPARATION.md`（该文件已有未提交修改，**只增不删，保留无关改动**）。

### 2.3 测试（扩展 `web/tests/test_na70_lio_scans.py` 与 `web/tests/test_na70_lio_export.py`）

1. 全 gap>0 时插值时间严格递增且数值精确等于规格公式。
2. gap=0 包退化为共享时间，不产生时间倒退。
3. 末包 fallback 计数正确。
4. 默认模式与现行为完全一致（回归）。
5. `time_quality` 新字段正确；`save_npz`/`load_npz` 往返校验通过。
6. 导出 CLI 参数传递与 manifest 字段。

## 3. T2 FOV 实测与 FAST-LIO 配置修正

### 3.1 新工具 `tools/measure_na70_fov.py`

- 输入：会话 PCAP（用 `web/backend/decoder.py` 解码；本工具属经典 PCAP 路径，不走 `na70_core.wire`）。
- 输出 JSON：有效回波（距离码 >0 且两角度码均非 0xFFFF）的 azimuth/pitch 码最小/最大值，换算成度（×0.0432 / ×0.0258），按帧与全会话统计；同时报告 0xFFFF 与零码射线计数（口径问题仅供记录，不下结论）。
- 文档中明确：非重复扫描 pattern 需要长时间（建议 ≥60 s 静止）会话才能铺满 FOV，短会话的最小/最大角度是下界估计。
- `--output` 指向新目录；不得覆盖任何已有文件。

### 3.2 配置修正 `tools/lio/na70_fast_lio.yaml`

- `fov_degree`：取 T2 实测值（向上取整到 5°），写明"来自 <证据目录> 实测"注释。
- `filter_size_surf/map`：0.15（室内 10–30 m 尺度，Mid-70 惯例 0.1–0.15），注释注明是尺度假设非验收阈值。
- `blind`：7.4，注释引用 `20261030-near-range-floor` 证据。
- `extrinsic_est_en` 保持 false；单位外参保持，不得伪造标定值。
- 若无实测 PCAP 可用（现场数据不在本机），fov_degree 允许暂填 70.0 并注明 `UNMEASURED_PLACEHOLDER`，工具与文档照常交付，实测步骤写进待办。

## 4. T3 验证与证据

1. 先记录基线：`cd web && .\.venv\Scripts\python.exe -m pytest tests -q`（PowerShell），记录通过数，再开始改代码。
2. 全部测试通过后，SYNTHETIC 证据写入新目录 `Plan/evidence/slam/<YYYYMMDD>-lio-point-time-and-config/REPORT.md`：改动清单、测试计数、T2 输出（若有真实数据）、明确声明"SYNTHETIC/配置修正，不代表真实数据精度提升，动态 LIO 仍 DIAGNOSTIC_ONLY"。
3. 可选（有 WSL2/Humble 环境时）：用新配置重放 `20261104-lio-diagnostic` 同一段数据做前后对比，输出到新目录；结论只允许写内部统计（路径长度、位姿间隙），不得归因或宣称改善原因。
4. `Plan/README.md` 顶部新增一条目（中文，含证据链接），不改写历史条目。
5. 不自动 commit；完成后列出改动文件清单，等用户确认后按仓库风格（简洁中文动宾短语）提交，且只提交本计划涉及的文件。

## 5. 硬性纪律

- 全程遵守仓库 `AGENTS.md`；四空格 Python 缩进；算法与 ROS 包装解耦；阈值进 YAML。
- 不得发明协议语义（0xFFFF、工厂状态字等一律标注"未核实"）。
- 不得宣称：逐点实测时间、时间同步、外参标定、地图精度、动态 LIO 可用。
- 保留工作区无关未提交改动；只触碰本计划列出的文件与新建目录。

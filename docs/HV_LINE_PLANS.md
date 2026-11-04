# 高压线（HV）识别：开发入口与尺度

日期：2026-10-03。范围：执行计划文档、离线算法核心、W3 离线运行器、P0 evidence。**未实现**：`/api/wire/w3`（REST 持久化作业）、`/api/wire/w4`/WebUI 高压线页 reviewed 真值录入、200 m 真实 HV 验收。

## 路径

| 角色 | 入口 |
|---|---|
| 执行计划 | [Plan/高压线识别执行计划.md](../Plan/高压线识别执行计划.md) |
| 路线纪律 | [Plan/README.md](../Plan/README.md)（参考；主线仍是足球门 G0/G1） |
| 物理门禁 | [细线 W1–W5](../Plan/细线回波验证与逐级开发计划.md)、[机载 S1–S4](../Plan/无人机单根高压线识别场景与可行性计划.md) |
| 算法契约 | [NA70_WIRE_ALGORITHMS.md](NA70_WIRE_ALGORITHMS.md) |
| 证据契约 | [NA70_WIRE_DEBUG_ACCEPTANCE.md](NA70_WIRE_DEBUG_ACCEPTANCE.md) |
| 评估证据 | [Plan/evidence/hv-line/20261003-p0/](../Plan/evidence/hv-line/20261003-p0/)（catalog+S0 扩展）与 [Plan/evidence/hv-line/20261003-p0/W3_EVAL_REPORT.md](../Plan/evidence/hv-line/20261003-p0/W3_EVAL_REPORT.md) |

## 当前可运行产物

- `packages/na70_core/na70_core/windows.py`：增量因果包级时间窗（与 thin_wire 同语义），20 项契约 tests。
- `packages/na70_core/na70_core/wire/`：manual-ROI 线段提取 pipeline（算法 R4）。W1 协议**不变**。
- `tools/run_wire_w3_offline.py`：离线 W3 runner，写 manifest/window/stages/candidate/REPORT 完整证据。

## 一次 W3 运行

```powershell
.\web\.venv\Scripts\python.exe tools\run_wire_w3_offline.py `
  --pcap radar_capture_data\2\9.14_caochangzhong2.pcap `
  --end-frame 600 --duration 1.0 `
  --roi 170.0,320.0,-60.0,60.0,5.0,30.0 `
  --output Plan\evidence\hv-line\<自定义-run-id>
```

输出：候选方向/段/最小距离、cover/gap/rms/p95、唯一源包数、stage trace。Result 标签规则见 [wire/constants.py](../packages/na70_core/na70_core/wire/constants.py)。

## 术语与 gate 状态

- W3 = proposed manual ROI line extraction，**不是** HV 识别。识别必须等 [高压线识别执行计划 §3 P3](../Plan/高压线识别执行计划.md) 独立实测 200 m 数据 + reviewed truth。
- 当前 P0–P2 状态：
  - **P0** ✅ 已交付（catalog + S0 150/200 m 扫描机会扩展）。
  - **P1** ✅ 已交付（`na70_core.windows`，含契约与同意义 semantic 对账测试）。
  - **P2** ✅ 已交付（`na70_core.wire` 手动 ROI pipeline + 离线 W3 runner + 20 项 tests）。
  - **P3** ⏳ 需要用户/合作方 200 m 实测数据；分项是物理任务，不是本仓库代码能单独完成。
  - **P4** ⏳ 等 P2+P3；Hough 提议 + frozen 验证。
  - **P5** ⏳ 部署后期。

## 已交付验收（本次）

- 测试：`pytest web/tests/test_na70_core_wire.py web/tests/test_na70_core_windows.py -q` → **20 passed**；包括有效输入、空 ROI、退化样本、pd min_points、`time_quality=partial_window` 语义一致性、阈值非法拒绝。
- 全量回归：`pytest web/tests -q` 待 361 (原有) + 30 (新增) = 391 项数值待记录（参见 [VALIDATION.md](../web/VALIDATION.md) 最新条目）。
- 合成 smoke：8 个真实 PCAP + 合成 batch 批处理，行为符合设计契约；**真实身份 NOT_EVALUATED**，不声称已识别 200 m 高压线。

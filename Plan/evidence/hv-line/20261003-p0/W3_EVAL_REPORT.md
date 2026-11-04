# W3 受控 smoke（2026-10-03）：算法链路证明，真实身份 NOT_EVALUATED

## 输入

| Run | PCAP | end_frame | duration | ROI（sensor m） | 期望解读 |
|---|---|---|---:|---|---|
| zhong2-f600 | 9.14_caochangzhong2.pcap | 600 | 1.0 s | (100, 250, -20, 20, 0, 60) | 中距高 z 候选盒 |
| zhong2-f600-far | 同上 | 600 | 1.0 s | (180, 300, -30, 30, 0, 80) | 远距大盒，距离 180–300 m |
| zhong2-f600-200 | 同上 | 600 | 1.0 s | (190, 240, -15, 15, 0, 50) | 200 m 窄盒 |
| zhong2-f600-catalog200 | 同上 | 600 | 1.0 s | (170, 320, -60, 60, 5, 30) | catalog 高密度扇区对应盒 |
| zhong2-f600-close | 同上 | 600 | 1.0 s | (5, 30, -15, 15, 0, 30) | 近场景负对照 |
| you-f800-far | 9.14_caochangyou.pcap | 800 | 1.0 s | (180, 300, -30, 30, 0, 80) | 远距 |
| zuo-f1200-far | 9.14_caochangzuo.pcap | 1200 | 1.0 s | (150, 300, -30, 30, 0, 80) | 远距 |
| one-pcap-f600-catalog | 1.pcap | 600 | 1.0 s | (120, 260, -40, 40, -5, 25) | 1.pcap 远距扇区 |

## 结果（来自每次 run 的 candidate.json）

| Run | status | first_blocking_stage | span_m | coverage | largest_gap_m | rms_orth_m | p95_orth_m | unique_packets | nearest_m |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| zhong2-f600 | MEASURED_CURRENT | none | 28.5 | 0.83 | 3.3 | 0.432 | 0.584 | 116 | 112.98 |
| zhong2-f600-far | MEASURED_CURRENT | none | 26.5 | 0.93 | 2.8 | 0.347 | 0.576 | 55 | 180.72 |
| zhong2-f600-200 | MEASURED_CURRENT | none | 4.2 | 1.00 | 1.5 | 0.369 | 0.586 | 8 | 227.04 |
| zhong2-f600-catalog200 | MEASURED_CURRENT | none | 72.1 | 0.47 | 21.4 | 0.344 | 0.560 | 63 | 180.70 |
| you-f800-far | MEASURED_CURRENT | none | 26.8 | 0.89 | 2.0 | 0.388 | 0.572 | 31 | 223.66 |
| zuo-f1200-far | MEASURED_CURRENT | none | 59.9 | 0.42 | 35.9 | 0.342 | 0.567 | 36 | 152.77 |
| zhong2-f600-close | UNKNOWN | fit (INSUFFICIENT_SUPPORT) | – | – | – | – | – | – | – |
| one-pcap-f600-catalog | UNKNOWN | fit (INSUFFICIENT_SUPPORT) | – | – | – | – | – | – | – |

## 严格按契约解读

- **算法链路工作**：R1 包级因果窗口 + W3 手动 ROI 拟合 + 有限支持验证 pipeline 全通过，且对没有拟合支撑的负例给出 INSUFFICIENT_SUPPORT 拒绝，行为符合 [W3 门禁](../docs/NA70_WIRE_ALGORITHMS.md) §4 要求（支撑不足/短/断裂则拒绝）。
- **不属于高压线识别成功**：
  1. 所有 ROI 由工程 catalog 或经验扇区驱动，未独立 review；候选没匹配到任何 reviewed HV 真值。
  2. 候选几何**更像场景物体**：
     - zhong2-f600-catalog200 方向 ≈ [−0.06, −0.998, +0.01] → **几乎平行地面**（z 变化 ~0），距离约 180 m，百米内垂直变化 0.7 m。架空单根导线若高 180 m 内应通常与雷达朝向更水平一致或具明显 z 斜率；这段更像远处**屋檐/桥梁横档/树冠边缘/道路对角**或操场围墙的切面。
     - zhong2-f600-far span 26.5 m 部分 z 大幅变化不一致，largest_gap 2.8 m，可能是多层植被/结构混合，不是 1–2 cm 直径线。
     - zhong2-f600-200 span 仅 4.2 m 且 coverage 1.0 — 极小局部一行，符合「偶然拟合」。
     - zuo-f1200-far span 59.9 m coverage 0.42 但 largest_gap 35.9 m 远超 `max_internal_gap_m=50`，判为可接受因门槛为 50；参考值过宽，geo 不符合单源线。
  3. 普通 pyRANSAC 拟合的默认 `thresh_m=0.6` 在远处接近光斑直径数量级；以默认门槛展示的全通过意味着**阈值在当前静态库上不足分辨**，并不意味着目标被识别。
- **结论**：所有该批候选身份 `UNKNOWN_LINE`（几何线结构），wire_return_status 仍为 **NOT_EVALUATED**。**不能直接宣称在 200 m 静止 PCAP 中识别了高压线**。W3 阶段仅证明了 R1+R4 平台级链路。尚无独立测量/同机位背景对比的 200 m 真值。

## 工程结论

- **平台已完成**：`na70_core.windows`（增量因果窗口，与 W1 协议对齐）+ `na70_core.wire`（manual ROI 提议 → pyRANSAC 拟合 → 有限支持验证 → evidence 分桶）可用，全部 20 项新增 tests 通过；与原 W1 的 24 项 tests 互不破坏。
- **默认 wire_default.yaml 在静止整张 PCAP 上过松**：zhong2-catalog200 的候选 coverage_fraction=0.47、largest_gap=21.4、nearest=180.7 m、span=72.1 m、direction 几乎平行地面（z 变化 0.7 m 跨 72 m） — 与单根 18 m 高度架空导线几何特征明显不符；应当在真实 reviewed 数据到来前**不**用默认配置在所有 ROI 上跑全 FOV，否则可能产生大量 UNKNOWN_LINE 噪音工程负担。建议：把 200 m 真值场景专属 config 先冻结在 P3 reviewed-run 路径上，default 只服务合成 fixture。
- **下一步**：按 [高压线识别执行计划](../../../../高压线识别执行计划.md) §6 推进：
  1. **P3** 需要用户提供 200 m 真实可控数据或确认 catalog 中某条意为高压线的现场证据；否则 W3 输出保持 `UNKNOWN_LINE`，不升级为 LINE_WIRE。
  2. 若用户确认这些是预期保留候选，则 P4 在冻结 config 上跑全 FOV 完整回放（Hough/PCA 提议），并在用户提供 reviewed truth 后计 TP/FN/FP。
  3. （无异化）在默认配置下，zhong2 上「近场景负对照」ROI 应继续 trigger INSUFFICIENT_SUPPORT，继续作为回归 fixture。

## 证据目录

每次运行产物在 `Plan/evidence/hv-line/20261003-w3-<tag>/`：manifest.json（SHA + 命令 + env + 全部脚本哈希）、window.json（SelectedWindow 与 1000-packet 数据）、stages.jsonl（实际执行 StageTrace）、candidate.json（含完整 source_ray_indices；可在 WebUI/工具中反向回链 PCAP 字节）、REPORT.md（本次中文摘要）。

# W4 全场提案 smoke：真实 1 s 窗的 angular-slab 模型失效（如实记录）

日期：2026-10-03。状态：**FULL FOV 提案在真实 zhong2 1s 窗上输出 0 候选；`NO_CURRENT_MEASUREMENT`**。不是识别失败证据，是**提案层未达 W4 设计目标**。

## 实测根因（如实）

`radar_capture_data/2/9.14_caochangzhong2.pcap` end_frame=600 / duration=1.0 窗（1000 packets / 200000 rays / 78 225 valid）在 `azimuth_slab_deg=2 / elevation_slab_deg=2` 切分下：

- 整个窗的 slab 连通于 6-邻接格 = **单一连通体**，水平跨度 682 m × 垂直跨度 393 m × 高度跨度 63 m；
- 与 `min_horizontal_extent_m=8.0` 一致「过大」反被 `max_component_spatial_diameter_m=120` ✗；同时不能期望 span_along_dominant_axis ≥6 m 的子格（每个 slab 的 span 被切碎到 2°×2° 格宽）。

工程根因：NA70 的稀疏回波在 60° + 60° 的锥内**每个 2° slab 在 1 s 积累内几乎都有点**，slab 连通不是 discriminative。`NA70_WIRE_ALGORITHMS.md` §5 早就明示：全 FOV 提议要的是 Hough/PCA 线性度，不是纯几何 slab。这次的 full-FOV 简化实现在真实数据上**不工作不能直接推向 W4 门禁**。

## 当前状态

- `na70_core/wire/proposal_full_fov.py` 与 `--enable-full-fov` pipeline 分支保留，供后续按 §5 进入对照实验（Hough/PCA 提议 vs. baseline）的基线与对照入口。
- 本轮真实 zhong2 W4 试运行结果：`NO_CURRENT_MEASUREMENT`（no proposal），如实落盘（manifest/stages/candidate 均空 candidate），不写成「已提案」。
- 合成矩阵（`20261003-synthetic-matrix-v3`）继续验证：manual-ROI 主链 12/12 strict 通过，两个弱点（树冠 SPARSE_SUPPORT、catenary EXCESSIVE_SAG）被新默认阈值正确拒绝。

## 与 P4计划的衔接（现计划文档已更新，下一步入口）

P4 原本设定的「Hough / PCA 提议对照 + frozen 验证」拆两步：

1. **W4-A 提议对照实验**：在同一个真实 zhong2 静态窗上分别实现 (a) 前向角像 HoughLinesP（OpenCV）与 (b) 邻域 PCA 线性度提议，与现有 full-FOV slab 同样不做身份验收；`synthetic.py` 增加 dedicated Hough-friendly 狭窄组 `sequence` （如 two_wires_close_50m、wire_in_canopy_100m、line_with_noise_180m）以在 SYNTHETIC 上预验证每个 proposal 的方向误差与提案数。
2. **W4-B frozen 验证**：P3 真实数据到位后，在冻结的 proposal + 阈值下按 NA70_WIRE_DEBUG_ACCEPTANCE §5 评分 TP/FN/FP。

Physics gate 不变：任一阶段输出都是 UNKNOWN_LINE；identity 需 reviewed truth。

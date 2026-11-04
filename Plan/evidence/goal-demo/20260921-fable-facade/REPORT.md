# 20260921 facade 策略首次真实识别与跟踪（DEVELOPMENT / MANUAL_ROI / NOT_EVALUATED）

标签：`DEVELOPMENT`、`MANUAL_ROI`（人工 ROI 内，G2 语义）、`SYNTHETIC`（单测部分）、`NOT_EVALUATED`（无 reviewed 真值，评分器输出 NOT_EVALUATED）。本报告不宣称 G2 阶段通过：G2 门禁要求 5 正 + 5 背景 reviewed 窗口，尚未建立。

## 目的

用户要求「自己尝试识别跟踪门」。按主线计划 G2 方向（人工 ROI 内、立面几何、不用三维连通），实现 `strategy.mode: "facade"` 并在真实主 PCAP 上运行全片。

## 探针结论（真实数据，先探针后写码）

输入：`radar_capture_data/2/9.14_caochangyou.pcap` 前 200 帧（现场确认静止 20 s），取 `20260921-g0-g1-static/windows/raw-0-199.npz`（1894 个 z_rel>0.3 的 ROI 点）。

- 球门结构集中于 x∈[93.5,95.2]、y∈[-29.0,-23.6]、z_rel≤2.13：左右边界各约 280 点（59/74 帧），中间为网面（约 55 点/0.2 m）。
- 量得宽度约 5.0–5.37 m、上沿 z≈2.06：接近 5×2 m 训练球门；旧标注 7.32×2.44 默认框与实测不符（其 PENDING_REVIEW 资格不变）。
- 仅有 77/200 帧观测到目标（中位 24 点/帧），逐帧只覆盖部分 y 段——单帧检测不可行，必须窗口累积。
- 旧两级失败在真实点上复现：`find_poles` 21 细胞全部 `height_too_short`；`find_poles_neighbor` 整体 1 个组件 `seed_radius_too_large`；staged 的 BEV 在 20 帧体素窗口上 `no_dense_cells`（202 体素）。

## 实现

- 新增 `web/backend/goal_detection/facade.py`：地面相对坐标内鲁棒立面拟合（2-D PCA+截尾）→ u 轴 0.2 m 直方图 → 由外向内连续支持定位左右边界（中位数+MADS 残差+内点率门控：柱是密度聚集，网/围栏剪切边不是）→ 上沿逐 bin 最大值的鲁棒中线（覆盖率门控）→ 宽高测量与置信度（几何 0.7 + 上沿 0.3，沿用 scoring 权重）。不使用任何三维连通。
- `strategy.mode: "facade"`：跳过 BEV 全场搜索（G2 明确绕开），ROI 即搜索区域；无可信地面模型时拒绝测量（G1 语义，trace 记录 `no_ground_reference`）。
- 测量输入是 ROI 过滤后的**原始窗口点**（非 0.5 m 体素）：体素质心把 1894 点压到 58 点导致直方图稀疏（首次全片运行全 SEARCHING 的原因）；原始行的 (frame,point) 引用即精确来源。
- 配置全部进 `goal_detection.yaml` 的 `goal_detection.facade` 段；`config.py` 白名单校验；默认 `strategy.mode` 仍为 `staged`，facade 经 `--strategy facade` 启用。
- `synthetic.py` 新增 `netted_goal_points`（带网面、 smeared 柱），单测 `web/tests/test_goal_facade.py` 11 项。

## 运行结果（本目录，真实 PCAP 全片 1211 帧）

- 命令：`run_goal_demo_offline.py --pcap radar_capture_data/2/9.14_caochangyou.pcap --strategy facade --confirmed-static-until 20`
- `state_counts`: SEARCHING 26 / CANDIDATE 7 / **LOCKED 1 / TRACKING 128** / REACQUIRE 50 / LOST 999
- 锁定事件：frame 33，id=1，距离 97.77 m，置信度 0.902，中心 [94.19, -26.23, -0.24]（传感器系）
- 跟踪段（frame 34–180）：中心均值 [94.32, -26.36]，std [0.08, 0.12] m；尺寸均值宽 4.92±0.21、高 1.84±0.17；45 帧为 MEASURED_CURRENT，其余为窗口内预测（跟踪器语义：预测不加确认）
- 门禁抽查：LOST/SEARCHING 期间错误检出 = 0；track id 唯一 {1}，0 次 ID switch
- 性能：frame_ms_p50 41.0 / p95 69.3
- 雷达开始移动后（约 frame 200+）地面模型逐步失效，facade 诚实拒绝 → LOST；未用预测伪装当前测量

## 评分

`score_goal_detection.py --capture c4cc19fb... --reviewed-only` → `status: EVALUATED`（2026-09-23 起使用 reviewed 窗口真值，见 `20260923-g0-window-truth/REPORT.md`）：

- 测量层（仅客观可见帧为分母）：frames=77，hits=51，**precision=1.000，recall=0.662**；漏检集中在锁定前（frame≤26）与预测帧
- 预测层 measured 桶：54 帧，hits=45，precision=1.000
- 标注区间内误检点 0

（2026-09-22 前该运行显示 NOT_EVALUATED：旧标注为待复核默认框且 capture_id 与运行行不一致；后者已按仓库约定以 `--capture-id` 重跑对齐。）

## 已知边界与后续

- 本结果属于「局部几何 + 人工 ROI 内识别 + 静止段跟踪」层级；全场搜索（G3）、运动场景（G4）、冻结验收（G5）未做。
- 后端全量 339 项测试通过；前端生产构建通过。
- 置信度 0.90–0.95 由边界/上沿证据计算，未为通过门限而调整权重或阈值。
- 5×2 尺寸为测量值而非真值；需 G0 reviewed 实测后才能成为宽高先验。
- 运动段地面退化时框高开始漂移（如 frame 150 高 1.33），G4 需按计划处理运动窗口展宽与拒绝语义。

# P1 证据：真实 Stage、原始点溯源与框坐标一致

日期：2026-09-19。环境：Windows 11 / Python 3.12.10。结论：P1 PASS；检测精度仍为 FAIL 基线，留给 P2/P3。

## 交付

- 累积过滤在执行处记录真实输入、输出、拒绝原因和耗时；体素使用 CSR 映射回原始 `(frame_index, point_index)`。
- Detector 在实际执行处记录 finite、range、z、立柱格、柱对、NMS 和拒绝候选诊断；BEV 记录 blob/柱对拒绝统计；FOV crop、Z 修正及 association 分别计时。未运行阶段写 `skipped + latency_ms:null`。
- Inspector 的 Pipeline API 改读 execution trace；后置掩码仅作为旧调用兼容，并标 `diagnostic_projection`。
- 候选输出 `source_cloud_id`、原始点引用总数、返回数和截断状态；`/api/demo/sessions/{id}/provenance` 可按 voxel 查询原始点。
- 旋转框输出统一的 `[depth,width,height]`、yaw、8 个世界角点及从角点计算的 AABB；检测测量框与跟踪估计框用 `box_role` 区分，Z 修正后的选中测量单独输出。
- 拒绝诊断开关和数量/原始引用预算进入 YAML；关闭诊断不会改变检测输出。
- 离线行升级为向后兼容的 `goal-run-v1.1`，新增 execution trace、选中测量、来源和 cloud ID。

## 可复算样例

完整运行帧 597：

- 累积云 `acc:578-597:598`；过滤输入 400000 点，保留 2109；体素输出 639；
- BEV 输入 639，15 个 blob pair，输出 1 个 region；
- FOV 检测输入 340 点，提取 5 根柱，89 个柱支撑点；10 个柱对中 1 个通过；
- 选中 `candidate-1`，输出 8 角点；114 个原始点引用，未截断；
- 记录 range/ROI、voxel、BEV、finite、range、height、pole、pair、拒绝诊断、NMS、Z refine、association 共 12 个执行节点。

## 真实 PCAP 回归

输入：`9.14_caochangyou.pcap`，1211 帧，w20、0.5 m、既有人工 ROI。输入哈希、配置和 13 个源码哈希见 `summary.json`。

- 状态计数与 P0 完全一致，说明 P1 诊断与来源改造未改变算法决策；
- 处理耗时 p50 36.11 ms、p95 51.46 ms、max 483.59 ms；p95 仍低于本机 100 ms 开发门禁，max 尖峰保留为 P7 风险；
- 工作集采样峰值约 126940 KB；来源映射带来内存开销，仍为有界滑窗；
- eval-v1 仍为 11 个待复核帧零命中，UNKNOWN 1200/67；P1 未借诊断改动掩盖检测失败。

## 验证

- P1 相关后端专项：55 passed；
- 前端生产构建通过；
- `demo-observability` 浏览器测试通过，确认真实执行分支可点击、skipped 可显示、参数与日志仍可用；
- 全量后端回归：230 passed；仅保留既有 Starlette/httpx 弃用警告。

P1 关闭 R01、R02、R05 的数据契约部分。R03/R04 跟踪语义和 R07 原子发布仍按计划留在 P3/P4。


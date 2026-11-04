# P1 发布证据：真实 Stage、来源溯源与框坐标契约

日期：2026-09-20。结论：P1 PASS。输入为 `9.14_caochangyou.pcap` 全部 1211 帧，Windows 11 / Python 3.12.10。

本目录是修正 `skipped` 耗时契约后的 P1 发布证据。旧的 20260919 P1 目录保留作开发记录，不作为最终门禁入口。

## 门禁结果

- 输入 SHA-256 与标注一致；summary 记录配置、环境与 13 个源码哈希，当前复核无哈希差异。
- 1211 个运行行全部为 `goal-run-v1.1`；点过滤满足 `input=output+rejected`，错误 0。
- 所有 `skipped` 节点均为 `latency_ms:null`，错误 0。
- 所有候选/选中测量框均输出 8 个角点，错误 0；AABB 由旋转角点计算。
- 83 个选中测量帧的原始点引用全部位于当前 20 帧滑窗且点索引非负，错误 0。
- 帧 597 可从 `candidate-1` 的累积体素解析到 114 个原始 `(frame,point)` 引用，未截断。
- 关闭 rejected-candidate diagnostics 的合成回归确认检测候选完全一致。
- Web 调试云升级为 v3：Stage 分类与候选高亮是独立字段；完整当前帧用 `context_raw`，不再误报为 rejected。

## 真实回归与性能

- 状态计数保持 SEARCHING 595 / CANDIDATE 2 / LOCKED 1 / TRACKING 188 / REACQUIRE 282 / LOST 143，P1 未改变算法决策。
- 处理耗时 p50 74.12 ms、p95 96.10 ms、max 875.21 ms；p95 勉强通过本机 100 ms 开发门禁，最大尖峰明确留给 P7 优化。
- 工作集采样峰值约 129216 KB；来源映射受 20 帧环形窗口、点数和诊断预算约束。
- eval-v1 仍为待复核 11 帧零命中；1200 个 UNKNOWN 帧中 67 帧有正输出。该失败事实保持不变，等待 P2/P3/P6。

## 验证

- 全量后端：230 passed；
- P1 专项复跑：55 passed；
- 前端 TypeScript/生产构建：通过；
- Playwright `demo-observability`：1 passed；
- 仅有既有 Starlette/httpx 弃用警告。

Web 调试侧补充哈希：`demo_api.py` = `f7258bc564fe...`，`DemoApp.tsx` =
`eba07b36297f...`，`GOAL_DEBUG_CONTRACT.md` = `c9dff005d9d8...`。

P1 关闭 R01、R02 与 R05 的数据/坐标契约部分。R03、R04 和 R07 仍按计划属于 P3/P4。下一阶段为 P2 Synthetic 场景与失败测试。

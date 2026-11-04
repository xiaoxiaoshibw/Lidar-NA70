# 球门跟踪基线清单（P0）

日期：2026-09-19。维护规则：历史证据与标注**原样保留**，新增运行追加新目录；
本清单只记录事实与局限，不改写旧产物。契约见 `docs/GOAL_DEBUG_CONTRACT.md`，
开发顺序与门禁见 `Plan/球门跟踪下一阶段完整开发计划.md`。

## 1. 已有运行证据（状态帧计数 ≠ 正确跟踪帧数）

来源：`Plan/球门跟踪下一阶段完整开发计划.md` §3 重新读取的 summary.json（9.14_caochangyou.pcap，1211 帧，人工固定 ROI + 0.5 m 体素 + staged）。

| 运行目录 | 窗口 | 锁定帧 | SEARCHING | TRACKING | REACQUIRE | LOST | 处理 p50/p95/max ms |
|---|---|---:|---:|---:|---:|---:|---|
| 20260919-observable-pipeline-full | 10 | 862 | 850 | 16 | 62 | 270 | 10.05 / 10.69 / 286.74 |
| 20260919-observable-pipeline-w20 | 20 | 597 | 595 | 188 | 282 | 143 | 19.01 / 20.44 / 314.21 |

局限（逐条）：

1. 这些是状态机帧计数，不是召回率、连续跟踪时长或框精度；禁止据 TRACKING 帧数宣布提升。
2. 20 帧窗口只是待复验的开发默认，未证明框更准确（计划 §1）。
3. 行格式为旧版：无 capture_id / input_sha256 / config_version，capture 归属不明，
   新版评分器仅在显式 `--capture` 下才会使用其标注（`capture_attribution: legacy_via_cli`）。
4. p95 达标不掩盖 max 尖峰，也不覆盖 HTTP/渲染延迟。
5. 旧运行跨多 PCAP 时复用同一 pipeline（R09）；上表两个运行均为单 PCAP，不受影响，但格式局限同上。
6. `20260919-w20-staged-truth`、`20260917-phase*` 等更早目录同理视为旧格式基线。

## 2. 标注盘点

| 文件 | capture | 条数 | 形式 | review_status | 局限 |
|---|---|---:|---|---|---|
| web/data/annotations/c4cc19fb-….json | c4cc19fb（caochangyou） | 2 goal | 纯 box | UNREVIEWED_LEGACY | 可能在 UI 中被旧检测框引导；未经三视图复核 |
| web/data/annotations/f939a3cc-….json | f939a3cc | 1 goal | 纯 box | UNREVIEWED_LEGACY | 同上 |
| annotations-reviewed/ 下两份复制件 | 同上 | 同上 | 纯 box | PENDING_REVIEW | 内容同 legacy，仅加 provenance；评分器去重时优先于 legacy 原件，复核后原地更新 |

## 3. 已复现失败事实（修复前作为基线引用，修复后以测试锁定）

| 编号 | 事实 | 复现方式 | 状态 |
|---|---|---|---|
| R04 | LOST 状态仍返回 detected=true、confidence=0.95、last_seen_index=4 | 5 帧候选 + 45 帧空候选只读复现（计划 §2 R04） | 未修复（P3 处理） |
| R06 | 评分器忽略 UI 保存的纯框标注：score_run([], [合法 box-only goal]) → goal_labels=0 | 本轮只读调用复现；修复后测试 `test_box_only_goal_is_scored` 锁定 | 已修复（P0） |
| R01/R02/R03/R05/R07/R08/R09 | Stage 掩码事后推算 / 图层混用 / 展示分数未参与匹配 / 旋转框 AABB 不一致 / 版本竞态 / 搜索范围与性能证据不足 / 合成验证不足 | 代码审查结论（计划 §2） | 未修复（P1/P3/P4 处理） |

## 4. 测试基线

- 修复前：web/tests 共 213 passed（已有回归结果，非本轮审查通过证明）。
- P0 新增评分器契约测试见 `web/tests/test_score_goal_detection.py`（手工可算小样本）。

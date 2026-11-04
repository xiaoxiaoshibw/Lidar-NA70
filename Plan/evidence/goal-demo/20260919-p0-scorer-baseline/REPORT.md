# P0 证据：冻结基线与修复评价基础

日期：2026-09-19。对应 `Plan/球门跟踪下一阶段完整开发计划.md` P0 阶段。结论：**PASS**。

## 1. 交付物

| 产物 | 位置 |
|---|---|
| 调试与评价契约 v1（标注/运行行/评价配置三段契约） | `docs/GOAL_DEBUG_CONTRACT.md` |
| 版本化评价配置 eval-v1 | `tools/goal_eval_config.yaml`（版本哈希随报告输出） |
| 修复后的评分器（box/点标注统一、capture/帧区间隔离、UNKNOWN 桶、测量/预测/失锁三分层） | `tools/score_goal_detection.py` |
| 修复后的离线 runner（多 PCAP 独立管线、逐帧 capture_id/input_sha256/config_version、env/源码哈希） | `tools/run_goal_demo_offline.py` |
| 标注复核副本（PENDING_REVIEW + provenance，原件原样保留） | `Plan/evidence/goal-demo/annotations-reviewed/` |
| 基线清单（旧证据逐条标局限、失败事实表） | `Plan/evidence/goal-demo/baseline-inventory.md` |
| 契约测试（手工可算小样本 13 项） | `web/tests/test_score_goal_detection.py` |

## 2. 手工可算小样本验证（PASS 条件逐项）

`cd web && .venv/Scripts/python.exe -m pytest tests/test_score_goal_detection.py -q` → **13 passed**。
覆盖：纯框标注计分（R06 回归）、5 点标注、误检只判标注区间内帧、跨 capture 同坐标隔离、
运行外 capture 标注不泄漏、无标注 NOT_EVALUATED、invisible 不进分母、
测量/预测/LOST 三层分离（LOST 框不进分母）、低置信度不算正输出、
旧格式行必须显式 `--capture`、reviewed 优先去重。期望计数全部由夹具手算得出。

全量回归：`cd web && .venv/Scripts/python.exe -m pytest tests -q` → **222 passed**（修复前 213）。

## 3. 真实数据评分（新 runner + 新评分器）

运行：`tools/run_goal_demo_offline.py --pcap radar_capture_data/2/9.14_caochangyou.pcap
--run 20260919-p0-scorer-baseline --capture-id c4cc19fb-…`（w20 默认配置）。
输入 sha256 `ceca9fc0…` 与标注文件记录的 capture_sha256 **完全一致**，capture 归属确认。
状态计数与历史 w20 基线一致（SEARCHING 595 / TRACKING 188 / REACQUIRE 282 / LOST 143）。

评分产物：`scored/validation.json`（行归属 row 模式）；旧运行目录另评一份
`legacy-full-scored/validation.json`（legacy_via_cli 模式，验证旧格式兼容）。

结果（eval_config_version 8a00d3f92a5901f7）：

- goal_labels=2（两条 box-only 标注均计分；修复前为 0）；第 3 条 f939a3cc 标注按
  `capture_not_in_run` 排除——这正是修复过程中抓到并已回归测试锁定的跨 capture 泄漏。
- 测量层：标注帧 11（74–83、0），hits=0，recall=0.0，miss_rate=1.0。
- UNKNOWN 桶：1200 未标注帧，其中 67 帧有正输出——不计误检、如实上报待 P6 标注后判。

## 4. 失败事实（保留，不得改写）

1. 当前 w20 管线在两条待复核标注区间上**零命中**：检测输出与人工标注不吻合。
   注意标注为 PENDING_REVIEW、可能被旧框引导（baseline-inventory §2），
   该结果同时可能反映检测坐标系/修正链问题（R05）——P1/P3 复核后再归因。
2. R04（LOST 仍 detected=true）未修复，属 P3；评分侧已保证 LOST 框不进分母。
3. R01/R02/R03/R05/R07/R08/R09 未修复，见 baseline-inventory §3。

## 5. 复现命令

```bash
cd web && .venv/Scripts/python.exe -m pytest tests/test_score_goal_detection.py -q
# 评分（新运行）：
web/.venv/Scripts/python.exe tools/score_goal_detection.py \
    --run Plan/evidence/goal-demo/20260919-p0-scorer-baseline \
    --output Plan/evidence/goal-demo/20260919-p0-scorer-baseline/scored
# 评分（旧格式运行，须显式 --capture）：
web/.venv/Scripts/python.exe tools/score_goal_detection.py \
    --run Plan/evidence/goal-demo/20260919-observable-pipeline-full \
    --capture c4cc19fb-3020-4ccc-8407-1521f71b7212
```

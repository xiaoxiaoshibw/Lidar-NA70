# 20260923 G0 窗口真值包：两处 reviewed 球门（REAL_PCAP）

**2026-09-23 真值更正**：用户根据点云图明确指出 `(92.7,+29.1)` 也是球门。撤销先前「非球门／困难负样本」判定。标注文件现为近侧与对面两处球门；没有 reviewed 背景窗口。以前使用错误负样本计算的 precision、recall、误检点与阶段门禁结论全部撤回。G0 仍未完成。

## 内容

1. `far-structure-window/`：对面 (92.7,+29.1) 结构的 G0 窗口证据包（`audit_goal_static.py --windows 0:199 --roi 86,100,22,37,-2,6`）：raw npz（2335 ROI 点 + 原始引用）、三视图 PNG、audit.json（地面模型、绝对/相对高度过滤对照、诊断邻域）。注意其 `absolute_filtered = 0 点`——绝对 z>0.3 截断对该结构比近侧球门更致命（相对高度过滤后 1477 点，z 至 1.89 m）。
2. `Plan/evidence/goal-demo/annotations-reviewed/window-truth-c4cc19fb-20260923.json`（评分器默认扫描位置）：
   - **正样本 51 条**：近侧球门逐可见段标签（frame_range=连续可见帧段；可见性由 raw-0-199.npz 中 ≥5 个原始结构回波客观判定；框为 facade 实测 center [94.3,-26.3,-0.3]、size [1.2,4.9,1.9]、yaw 1.62、估计误差 0.3 m）。身份来源：用户 2026-09-21 目视确认 ROI 模式识别正确。
   - **对面球门 40 条可见段（57 帧）**：用户 2026-09-23 更正其身份为球门；由 `far-structure-window/raw-0-199.npz` 的 `relative_keep` 原始引用按每帧 ≥5 个结构回波划段，框为 facade 实测 center `[92.7,+29.1,-1.05]`、size `[1.0,5.1,1.95]`、yaw 1.57，估计误差 0.3 m。
   - 全部 `review_status: reviewed`；provenance 记录派生方法与来源文件。

## 评分状态

旧一正一负评分与其导出的 `validation.json` 属历史错误证据，不得作为当前准确率。现有两处球门在部分帧同时可见，旧评分器的“每帧最佳候选对任意真值”汇总不能表达双目标召回；此外尚无 reviewed 背景，不能给出可信背景误检精度。需补足背景真值并采用逐目标、逐可见帧匹配后重新评价。

## 工具变更（随本包）

- `score_goal_detection.py`：新增 `--reviewed-only`（`filter_reviewed_labels`，旧默认框历史保留但不进分母）；修复：运行行与标注的 capture_id 需一致——本次两个运行已按仓库约定以 `--capture-id c4cc19fb-...` 重跑对齐（此前 pcap-<sha16> 与 web 导入 UUID 不一致，是旧标注被跳过的真正原因）。
- `audit_goal_static.py`：诊断邻域改为跟随 ROI 中心（原先硬编码近侧球门 y 值，对其它 ROI 会产生误导性记录）。
- `web/tests/test_score_goal_detection.py`：新增 reviewed-only 过滤测试；14 项通过。

## 后续（G0 清单剩余项）

- 5 正 + 5 reviewed 背景窗口小集：当前两处正目标、0 处背景；还需从主 PCAP 与后续补录中建立。
- 部件级真值（左右立边/上沿/地面/网面 raw_refs）经标注工作台录入（v2 契约 `window_annotation.py` 已就绪）。

## 2026-09-23 追加交付

1. **历史锁定复核**（清单项关闭）：frame 153 框住的是 16 m 宽、10.5 m 高的连续围栏/看台立面（假阳性）；frame 597 位于运动段拖影窗口、锁点在球门立面外 5 m，无法证明为真实球门——两者均不升格真值。详见 `historical-locks-review.md`。
2. **部件级真值提案**（PENDING_REVIEW）：`Plan/evidence/goal-demo/annotations/window-components-c4cc19fb-20260923.json`——近侧球门窗口的左立边 300 / 右立边 283 / 上沿 314 / 本地地面 1558 / 网面 997 个原始 `(frame, point)` 引用，由 facade 测量派生并经 `validate_window_label` 全量校验；已注入工作台存储（`web/data/annotations/`，原文件备份于同目录 `app-store-backup-*.json`），待人工复核后升为 reviewed。
3. **标注工作台浏览器用例修复**：`demo-observability.spec.ts` 的标注用例在 HEAD 即失败（mock 缺 `/annotate/timeline` 使应用崩溃；按钮名过期）。按当前工作台交互重写：播放/暂停 → 画笔绘制 → G 入列 → Ctrl+S 保存（断言 payload 的 frame_range/cloud_id/review_status）→ 时间轴回退 seek 后旧窗口云保存被阻断（断言门禁错误）。`demo-observability.spec.ts` + `viewer.spec.ts` 6 项通过。
4. **产物哈希清单**：`artifact-hashes.json`（标注/提案/审计/工具/用例共 9 项 sha256）。
- frame 153 / frame 597 历史锁定复核仍未做。

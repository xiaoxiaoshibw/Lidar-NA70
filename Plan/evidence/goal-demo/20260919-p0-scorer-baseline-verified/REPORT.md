# P0 独立复核补充

日期：2026-09-19。结论：P0 PASS。

复核旧 `20260919-p0-scorer-baseline/summary.json` 时发现其中记录的
`tools/score_goal_detection.py` 哈希为 `5082f750...`，而 P0 最终评分器哈希为
`cd7a3fc6...`。原因是 runner 证据生成后评分器仍有修正；PCAP 和算法运行行不受影响，
但旧 summary 不能证明最终评分器源码与运行同时被冻结。

本目录使用最终 P0 工具重新运行完整 `9.14_caochangyou.pcap` 1211 帧：

- 输入 SHA-256：`ceca9fc08758f3c35f7b8b1c024bdab90e65f49ab65e39102cdd58dc0c5292db`，与标注一致；
- summary 中评分器哈希：`cd7a3fc6c8604664d6ac9efbfa07aba2694c152d53e32057168232b50b3548bd`；
- 状态计数复现：SEARCHING 595、CANDIDATE 2、LOCKED 1、TRACKING 188、REACQUIRE 282、LOST 143；
- eval-v1 复现：11 个待复核真值帧零命中、UNKNOWN 1200 帧、其中 67 帧有正输出；
- P0 契约专项：13 passed；P0 复核时全量当前测试：223 passed。

因此 P0 的契约、评分器、输入归属和失败基线可以独立复算。旧目录保留，不覆盖；本报告解释哈希差异。


# P3 开发记录：有限 XY 网格偏移 fallback

日期：2026-09-20。状态：IN_PROGRESS，未通过算法精度门禁。

## 本轮修改

当第一轮固定 `0.8 m` XY 网格提取少于两根柱时，Detector 按 YAML 的
`goal_detection.pole.grid_offsets` 尝试最多三个偏移 `[0.4,0]`、`[0,0.4]`、
`[0.4,0.4]`，选择柱数量最多的结果；主网格和原始点索引保持不变。每次 fallback
记录偏移、柱数量和拒绝原因。

## Synthetic 对照

`translation_yaw / seed=1000` recall 从 P2 的 0.225 提升到 0.625；seed=0 保持
0.865。ID switches 仍为 0。static seed=0 仍只有 0.01，static seed=1000 为 0.78，
说明网格边界只是部分原因，静止点密度和结构提取仍有随机敏感性。

## 真实 PCAP 对照

输入哈希与之前一致，1211 帧完整运行：

- 首次锁定帧仍为 597；
- 状态计数：SEARCHING 595、CANDIDATE 2、LOCKED 1、TRACKING 189、REACQUIRE 281、LOST 143；
- p50 35.55 ms、p95 42.67 ms、max 435.58 ms；
- 评价仍为 11 个待复核帧零命中，UNKNOWN 1200/67；
- 运行决策未被破坏，但真实准确率没有证据提升。

## 当前结论

有限偏移 fallback 可以保留为 P3 的候选改动，尚不能将 P3 标记 PASS。下一步需要把
柱结构提取从单 cell 点数门槛升级为“先按高度层去横梁、再做局部 3D 邻域/多高度证据合并”，
而不是直接扩大邻域把横梁和两柱连成一个宽组件；同时专门处理静止窗口的重复回波。
完成后重新运行 Synthetic 冻结 seeds 和真实 PCAP。P3 仍未关闭
R03/R04 的关联与 LOST 语义问题。

# P3 地面平面归一化真实回归

日期：2026-09-20。状态：FAIL / P3 IN_PROGRESS。

本轮启用 YAML 配置的鲁棒地面平面 z 归一化，输入点的 x/y、原始 provenance 和
检测阈值不变；输出候选恢复到原始 sensor 坐标。完整配置、源码哈希、逐帧 Stage
trace、候选时间证据和 validation.json 均保存在本目录。

结果：

- 1211 帧，`SEARCHING=1105`、`CANDIDATE=106`，仍无 LOCKED/TRACKING。
- 真实 validation：11 个 `PENDING_REVIEW` 标注帧 hits=0、measurement recall=0.0。
- `annotation-time-audit.json` 与 `annotation-window-audit.json` 原样复制到本目录，
  标注仍未修改。
- 标注工作台现会在新标注中保存 `annotation_context`（窗口帧数、voxel、range、
  device_time 和累积云来源）；本次历史标签没有该字段，继续按 PENDING_REVIEW 处理。

地面归一化只修复了 Synthetic `pitch_roll` 的几何失败：两个 seed recall 均达到
1.000；真实零命中和 PENDING_REVIEW 事实保持 FAIL，不能据此进入 P4。

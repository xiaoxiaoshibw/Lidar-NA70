# 球门可观察检测与短时跟踪开发报告

日期：2026-09-19  
环境：Windows 本机，Python 3.12，NA70 PCAP 离线回放  
证据标签：`SYNTHETIC` 与 `REAL_PCAP_WINDOWS` 分开记录

## 结论

旧框偏差不是单一“参数不准”。框由两个立柱格的 XY 中心、半径和 z 极值直接生成；
一旦 FOV/地面门限把真实柱点删掉，或树、围栏、窗口拖影进入同一格，立柱中心、顶部、
底部与朝向会一起偏，最终 bbox 只是把上游错误忠实放大。无 IMU 时不能把传感器坐标中
球门中心的变化当成球门世界运动；当前实现只保证短时 Track ID 关联。

## 当前数据流

```text
PCAP → NA70 decoder → 单帧完整 XYZI
     → 滑动窗口/ROI/距离门/体素化
     → BEV 双致密结构提议
     → Search FOV（锁定后缩窄）
     → z/高度门限 → 立柱格 → 立柱配对/横梁证据
     → Detection Score → bbox + supporting_point_indices
     → Association Score → SEARCH/CANDIDATE/LOCKED/TRACKING/REACQUIRE/LOST
     → FastAPI debug contract → Three.js WebUI Inspector
```

## 新增可观察性

- 十级 Pipeline：RAW、FOV、XY ROI、Z/HEIGHT、CLUSTER、GOAL STRUCTURE、
  CANDIDATE SCORE、BOUNDING BOX、ASSOCIATION、TRACKING。
- v2 调试点云逐点标记：0=Rejected、1=Active、2=Supporting；Rejected 不删除。
- 叠加模式仍把当前完整单帧作为灰色底图，算法叠加云覆盖其上。
- accepted/rejected 候选均可点击；被拒绝柱对带 `width_too_small`、
  `width_too_large`、`height_mismatch`、`base_misaligned` 或 `poor_geometry`。
- Detection Score 与 Association Score 分离；后者报告门限、中心距离、bbox、方向和历史。
- 日志、指标、配置热更新和单次 AI Snapshot 均有稳定 JSON 接口。

## Synthetic 验证

`web/tests/test_goal_pipeline_staged.py` 的随机场景包含标准球门、地面噪声、9 m 树状高物、
7 m 墙体，以及逐帧 0.14 m 平移与 0.32° yaw。5 个随机种子均锁定目标，锁定后 ID 始终
为 1，连续中心步长小于 3 m。该结果仅证明开发场景下的短时传感器坐标关联可运行。

## 真实 PCAP 对照

输入：`9.14_caochangyou.pcap`，1211 帧，staged 策略，0.5 m 体素，50–130 m。

| 窗口 | 首次锁定 | TRACKING 帧 | REACQUIRE | LOST | p50 / p95 |
|---|---:|---:|---:|---:|---:|
| 10 | 862 | 16 | 62 | 270 | 10.05 / 10.69 ms |
| 20 | 597 | 188 | 282 | 143 | 19.01 / 20.44 ms |

证据目录：

- `20260919-observable-pipeline-full/`
- `20260919-observable-pipeline-w20/`

因此开发默认改为 20 帧，但窗口仍可在 WebUI 选择 1/5/10/20/30/60/100，不能把 20 帧
写成普适最优值。真实数据仍有较长 REACQUIRE/LOST 区间；未与人工标注对账前，状态变化
不能证明球门真值持续存在，也不能宣称准确率通过。下一步应在标注工作台给可见/不可见
区间与球门框做真值，再对 `debug snapshot` 中的 Stage 拒绝原因逐段调参。


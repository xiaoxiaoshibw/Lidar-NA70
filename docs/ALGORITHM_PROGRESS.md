# 算法分类与进度

整理日期：2026-11-13。依据源码、计划与已有实验记录整理。本次发布核验见 [发布记录](PUBLICATION_20261113.md)。

## 通用观测与解码

`web/backend/decoder.py` 提供经典 PCAP 解析：1410 字节载荷、200 点／包、100 包／帧。`packages/na70_core/na70_core/observations.py`、`geometry.py`、`windows.py` 提供原始点身份、共享角度转换与有界因果包窗口。`ROS2/src/double_wedge_driver/` 为协议换算参考。四份录制中的最近有效返回为 7.488 m，输入和统计口径见 [近距审计](../Plan/evidence/slam/20261030-near-range-floor/REPORT.md)。

## 细线检测与跟踪

当前目标与阶段安排见 [细线计划](../Plan/细线回波验证与逐级开发计划.md)。

| 层级 | 源码与工具 | 已有材料与后续工作 |
|---|---|---|
| W1 原始回波 | `web/backend/thin_wire.py`、`wire_api.py`、`tools/audit_thin_wire.py` | ROI 统计、背景对照、来源索引、持久化任务、投影；独立真值归属待现场复核 |
| W2 距离与时长 | `na70_core/windows.py`、W1 窗口统计 | 支持积累观测；可用距离与连续时长按受控数据评估 |
| W3 人工 ROI | `na70_core/wire/`、`tools/run_wire_w3_offline.py` | 离线线段几何提取、合成矩阵工具 |
| W4 全视场 | `tools/run_wire_w4_full_fov_offline.py`、`run_wire_w4_proposal_compare.py` | 离线提议与对照脚本；Web 状态由 `/api/wire/capabilities` 声明 |
| W5 运动与跟踪 | 阶段计划、接口契约 | 按可信时间、位姿、运动补偿与现场验收顺序推进 |

入口：[架构](NA70_WIRE_ARCHITECTURE.md)、[调试验收契约](NA70_WIRE_DEBUG_ACCEPTANCE.md)、[W1 实验目录](../Plan/evidence/thin-wire/)、[W3／W4 实验目录](../Plan/evidence/hv-line/)。

## 球门几何检测与跟踪

`web/backend/goal_detection/` 包含 BEV、检测器、跟踪器、叠加与可观测性模块；阈值集中于 `goal_detection.yaml`。`demo_api.py` 和前端提供叠加点云、候选支持点、关联、日志和阶段耗时。`tools/run_goal_demo_offline.py`、`score_goal_detection.py`、`run_goal_synthetic_suite.py` 提供离线运行与评分。

真实数据根因分析、地面相对高度、球网几何和后续叠加实验逐次保存，结果对应各次输入、配置与标注范围。入口：[架构](GOAL_TRACKING_CURRENT_ARCHITECTURE.md)、[调试契约](GOAL_DEBUG_CONTRACT.md)、[实验目录](../Plan/evidence/goal-demo/)。独立真值验收按阶段计划开展。

## 雷达／IMU、标定与 LIO

`na70_core/capture/`、`web/backend/local_capture.py` 提供双流采集；`calibration/` 包含静置统计、六面加速度计和平面法向旋转外参求解；`scene.py` 按位姿拼接；`lio_scans.py` 导出扫描包与来源映射；`map_quality.py` 计算参考距离指标。

`tools/lio/` 提供 FAST-LIO／GLIM 构建、适配和重放工具，上游版本固定在 `upstreams.repos`。逐点时间支持包时间与声明式均匀插值。真实会话时间等级 C，动态轨迹状态 `DIAGNOSTIC_ONLY`。合成回归、现场采集审计和真实重放统计分别归档。下一步为设备时间语义、陀螺单位、真实外参、独立地图真值及 Jetson 性能核验。入口：[标定工作流](NA70_CALIBRATION_WORKFLOW.md)、[实验目录](../Plan/evidence/slam/)。

## 历史建图、运动与独立实验

纯点云里程计、转台、第三方探针、MapMOS、运动点带、低姿态背景与多站位合并见 [历史路线索引](ALGORITHM_HISTORY.md)，逐项列出代码、结果和恢复条件。

## WebUI 与实时观察

WebUI 使用 FastAPI、React、TypeScript、Vite、Three.js，多入口通过 HTTP 轮询获取状态。功能包括 PCAP 回放、标注、算法调试、持久化诊断与历史建图任务。操作和 API 见 [Web 说明](../web/README.md)。

`tools/na70_live_web/` 提供 Jetson UDP 实时观察与滑动帧叠加。2026-11-11 现场记录保存浏览器真实点云与叠加切换观察，见 [现场报告](../Plan/evidence/slam/20261111-jetson-live-view/REPORT.md)。运动补偿、检测与长时性能为后续集成项目。

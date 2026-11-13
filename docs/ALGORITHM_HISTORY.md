# 历史算法路线与复现索引

整理日期：2026-11-13。“路线”涵盖算法尝试和开发方向。本地当前 Git 分支为 `master`，历史路线通过目录与提交保存，各次状态、指标、命令和原因随原始报告归档。

## 路线档案

| 路线 | 保留实现 | 结果与进度 | 材料与复现条件 |
|---|---|---|---|
| 背景减除、聚类与跟踪 | `ROS2/src/na70_motion_detector/`、`web/backend/motion.py` | 算法核心、ROS2 包装与合成测试 | 代码和测试可运行；现场复现需对应 PCAP 与动作标注 |
| 射线历史与时空点带 | `web/backend/bands.py`、`cross_capture.py`、`tools/analyze_motion*.py`、`analyze_ray_motion.py`、`detect_walk_4pcap.py` | 多轮运动时间段、路径与速度探索结果 | `tools/analysis_output/`、`Plan/README.md` 历史条目；输入录制单独准备 |
| MapMOS 官方预训练 | ROS2 接口、迁移记录与 Web 历史说明 | 当前 NA70 实验记录为失败，路线已结束主线迭代 | 完整工程、权重和冻结环境待恢复，见下一节 |
| 纯点云里程计与体素地图 | `web/backend/mapping.py`、`mapping_jobs.py`、`sweeps.py`、`registration_se2.py`、`mapping.yaml` | 真实操场移动数据正式配准成功帧为 0；质量门、独立轨迹段和诊断记录归档 | [历史计划](../Plan/MASTER_开发与验收总计划.md)、[证据](../Plan/evidence/mapping-repair/)；需原 PCAP |
| 早期配准 | `web/backend/registration.py`、`registration.yaml` | 兼容实现与合成测试归档；生产建图统一经 mapping job | 依历史测试和对应输入恢复 |
| KISS-ICP 与 SE(2) 探针 | `tools/kissicp_probe.py`、`sweep_kissicp_probe.py`、`se2_rotation_probe.py`、`sweep_se2_probe.py`、`compare_se2_registration.py` | 静止 10 s 窗口诊断轨迹长 0.44 m、航向 0.02°；短窗覆盖和假航向问题有记录 | [报告](../Plan/evidence/mapping-repair/20260914-playground/kissicp-se2-probe-20260914.md)；kiss-icp 1.3.0、deskew=False、原 PCAP |
| 固定点转台 | `web/backend/turntable.py`、`turntable_jobs.py`、`turntable.yaml`、角度／标定模板、`tools/build_turntable_map.py` | `DEVELOPMENT / DIAGNOSTIC / ESTIMATED_ANGLE`；原始、叠加、角度与微调对照保存 | [证据](../Plan/evidence/turntable/20260915-development-final/)；需原 PCAP、实测或实验角度日志 |
| 球门几何与跟踪 | `web/backend/goal_detection/`、`demo_api.py`、离线评分工具 | 根因审计、地面高度、球网连通与叠加跟踪迭代归档 | [证据](../Plan/evidence/goal-demo/)、[历史计划](../Plan/足球门识别主线开发计划.md)；需录制与标注 |
| 低姿态背景实验 r01 | `tools/low_pose_r01/` | 冻结背景、FIT/CAL/HOLDOUT、背景距离、点索引和人工审核 | [说明](../tools/low_pose_r01/README.md)、`Plan/evidence/indoor-empty/`；输入引用外部 `D:/Code/ldiar`，需恢复路径 |
| FAST-LIO、GLIM 与 LIO 对照 | `na70_core/lio.py`、`lio_scans.py`、`tools/lio/`、`setup_glim_eval.py`、`evaluate_kiss_na70.py` | 扫描包、时间插值、WSL2 原型、配置与重放诊断 | [LIO 计划](../Plan/NA70_LIO算法优化AI执行计划.md)、`tools/lio/upstreams.repos`、`Plan/evidence/slam/`；需外部构建与配套会话 |
| 多站位 ICP 与轨迹对照 | `tools/merge_na70_station_clouds.py`、`compare_lios_replays.py` | `6cf54aa` 保存工具源码 | 各站点云、初始关系及轨迹输入需准备；参数见 `--help` |

## MapMOS 恢复材料

现有材料包括 [ROS2 点云接口与坐标系现状](../ROS2/点云接口与坐标系现状.md)、[迁移进度](../ROS2/迁移进度.md)、[开发执行规范](../Plan/05_开发执行规范.md) 和 [Web 历史说明](../web/README.md)。其中保留输入设计、设备环境、`radar_mapmos` 虚拟环境线索和路线决策。

本次目录核对中，完整 MapMOS 推理工程、预训练权重与冻结环境快照处于**待恢复**状态。当前 Git 历史可追溯至 `7635564`。恢复同一实验需要当时上游版本、权重及哈希、Jetson／CUDA／PyTorch 环境、输入录制、预处理配置、运行命令和输出日志。材料齐备前保留文字档案，恢复后补充复现报告。运动检测替代链的代码范围见上表。

## Git 版本锚点

| 提交 | 内容 |
|---|---|
| `7635564` | 初始驱动、运动感知、WebUI 和早期材料 |
| `194aec1` | 建图实现与验收证据 |
| `02a70dc`～`43c7f9c` | 球门跟踪计划、模板与配置加载 |
| `6cac52d`～`3e21ede` | 十帧缓存、叠加、全片离线工具与证据 |
| `751faab` | 标定工具链、球门 P3 和细线验证 |
| `d0220b6` | 采集修复与静态标定诊断 |
| `5ef2c23` | LIO 时间插值与 FOV 配置修正 |
| `6cf54aa` | 多站位 ICP 与重放轨迹对照 |

`git show <提交>:<路径>` 查看历史文件，`git log --all -- <路径>` 查看演变。历史运行建议使用独立克隆和该版本依赖。

本次 Git 对象检查还发现游离 blob 465 个、tree 562 个，额外游离 commit 数为 0。这些文件与目录对象的时间和路线归属待考证，完整历史以已保留提交及对应档案为索引。当前可追溯的 13 次原始提交已随发布上传。

## 数据与输出恢复

原始录制、视频和设备运行缓存按报告中的名称、哈希与路径恢复。仓库已经跟踪的历史证据随版本保存，新增原始大数据由忽略规则管理。报告中的本机绝对路径表示原实验位置。缺项通过文字记录保留，再运行时另建日期目录并注明输入、配置与环境。

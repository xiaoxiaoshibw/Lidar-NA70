# NA70 SLAM 无现场数据开发任务

日期：2026-10-30。执行依据：[雷达＋IMU 计划](NA70_雷达IMU_SLAM开发计划.md)、[源码调研](../docs/NA70_NONREPETITIVE_SLAM_CODE_SURVEY.md)。

本批执行 P0–P5 软件准备，现场 S0–S5 门禁另记。已有 NA70 PCAP 可作解码/包时间对账，不补造现场 IMU。

- [x] T1 固定 FAST-LIO2、GLIM、Livox 源码/子模块和实际依赖版本，工作区 `/home/radar/na70_lio_ws`。
- [x] T2 完成时钟映射、IMU 单位/时间/缺口/饱和检查与外参方向转换。
- [x] T3 完成保留源点引用的 NA70 包级相对时间数组导出，无伪造逐点时间。
- [x] T4 提供 `tools/prepare_lio_session.py`；未知同步、标定或 IMU 明确 BLOCKED，禁止覆盖旧输出。
- [x] T5 输入契约测试 13 passed；全量后端 420 passed / 2 skipped。旧 `1.pcap` 预检正确 BLOCKED，保留诊断与引用。
- [x] T6 FAST-LIO2、GLIM CPU 及 ROS2 wrapper 均构建成功；FAST-LIO 初始化/SIGTERM 退出检查通过。
- [x] T7 GLIM CPU 在作者 Ouster＋IMU 公开数据上完成 30 s 冒烟：270 行轨迹、11 子地图；存在 1 条 IMU 覆盖警告，精度与 NA70 能力未评价。
- [x] T8 新增标定计算核心 `na70_core/calibration/`：静置/Allan、六面加速度计、平面测距、`R_IL`＋时间偏移、外参候选合成；6 个 CLI 与 15 项 SYNTHETIC 测试。证据 `Plan/evidence/slam/20261030-calibration-synthetic/`。
- [x] T9 新增场景装配核心 `na70_core/scene.py`：位姿插值、逐点包时变换、体素地图、来源引用、PLY 导出；`tools/build_na70_scene.py` 与 SYNTHETIC 场景回归 `Plan/evidence/slam/20261030-scene-synthetic/`（地图到已知平面 P95 约 1.1 cm，10 Hz 位姿插值＋5 mm 合成噪声）。
- [x] T10 新增 `tools/audit_na70_imu_timebase.py`：从接收块与帧审计批量到达、设备时间字段、tid 连续性与时间等级。真实 60 s 会话为 1,180 批、每批 10–11 帧、批次间隔最大 53.0 ms、无 0x51/0x52，判 C 级（仅主机接收）；证据 `Plan/evidence/slam/20261030-timebase-audit/`。该结果说明动态 B5/SLAM 的时间门禁当前不能通过。
- [x] T11 采集侧动态识别 0x51/0x52：`ImuRecorder` 统计 `sample_time_frames`，质量报告与 manifest 不再硬编码 `sample_time_field=false`；新增 `tools/export_na70_imu_device_csv.py`，仅在字段齐全、单调、处理 32 位回绕后导出 LIO 七列设备时间 CSV，缺字段或倒退拒绝。当前固件无该字段，未生成设备 CSV。
- [x] T12 新增 `tools/audit_na70_imu_ranges.py` 与 `imu.range_summary`：逐轴范围、数值满量程、重复极值平台、可选配置量程的 98% 连续段。真实 60 s 会话无饱和事件（角速度最大约 2.24 °/s，加速度 z 9.35–10.16 m/s²）；静置拒绝与饱和无关。证据 `Plan/evidence/slam/20261030-imu-range-audit/`。
- [x] T13 新增 `tools/report_na70_session.py` 一键会话诊断：汇总时间基、量程/饱和、静置窗口与 PCAP 主机端点重叠，输出 `report.json`/`REPORT.md` 和保守用途判定。真实 60 s 会话结论 `REJECTED_NOT_STATIC`、时间等级 C、端点重叠 59.977 s、6 窗口全拒绝；证据 `Plan/evidence/slam/20261030-session-report/`。
- [x] T14 新增 `tools/fit_na70_range_model.py`：把多个墙面 JSON 合成距离斜率/截距模型，<3 个距离返回 `NEEDS_MORE_DATA`，并保留“截距混合光学原点”的限制说明；SLAM/标定工具链的多距离环节闭合。
- [x] T15 新增 `na70_core/lio_scans.py` 与 `tools/export_na70_lio_scans.py`：把会话导出为保留包级时间与帧/包/点源引用的 LIO 扫描包（`scans.npz`、`imu.npz`、manifest），点数不匹配直接拒绝，`lio_ready` 恒 false 并列出时间/外参/覆盖阻塞。真实前 5 帧 smoke：100,000 点、500 包、最大包间隔 1.147 ms，IMU 首样本比首雷达包晚 19.204 ms；证据 `Plan/evidence/slam/20261030-lio-package-smoke/`。
- [x] T16 新增 `na70_core/calibration/gyro_unit.py` 与 `tools/verify_na70_gyro_unit.py`：用设备四元数增量对照陀螺，检验 deg/s/rad/s 与 body/world 约定。真实 60 s 振动会话 `INCONCLUSIVE`（比例 0.284、余弦 0.269；0.255 s 时 0.261/0.479），融合姿态被平滑，陀螺单位仍为推断 deg/s；证据 `Plan/evidence/slam/20261030-gyro-unit/`。
- [x] T17 静置分析增加主机批次平均 Allan：`imu.batch_average` / `host_batch_allan`，在无设备时间且主机时刻重复但跨度 ≥60 s 时输出 `HOST_BATCH_AVERAGED`，只反映批次尺度慢漂移。10 分钟长录 120,017 帧审计：60 个静置窗口全拒绝、无饱和、批次结构稳定；证据 `Plan/evidence/slam/20261030-long-imu-audit/`。
- [x] T18 新增 `na70_core/map_quality.py` 与 `tools/evaluate_na70_map.py`：PLY 读取、地图双向一致性、带 ROI 的平面残差、控制点距离；不做配准、不缩放，明确“一致性/残差不等于绝对准确度”。5 项测试通过；SYNTHETIC 评估中 ground/back/side 残差 P95 约 0.0098/0.0100/0.0108 m，控制点 3/5 ≤0.05 m；证据 `Plan/evidence/slam/20261030-map-quality-synthetic/`。
- [x] T19 NA70→FAST-LIO 接入原型（WSL2/Humble，固定 commit `cc3c9e8`）：新增可审阅补丁 `ROS2/src/na70_lio/patches/fast_lio_na70_handler.patch`、回放/记录脚本；补丁编译成功。50 帧（5 s、1,000,000 点）真实包被 FAST-LIO 连续处理约 45 次更新，记录 27 个 `/Odometry` 位姿（范围 0.184/0.230/0.130 m，最大半径 0.199 m）与 1,326 点最新配准云。**未通过**：时间等级 C、单位外参、无独立真值、无完整 PCD，轨迹只作诊断，ATE/RPE `NOT_EVALUATED`；FastDDS 发现失败需 CycloneDDS，5 帧短包投递不稳定。状态 `PROTOTYPE_CONSUMED_50_FRAMES / DIAGNOSTIC_TRAJECTORY_ONLY / ACCURACY_NOT_EVALUATED`，见 `Plan/evidence/slam/20261030-na70-fastlio-prototype/REPORT.md`。
- [x] T20 Windows 平面法向外参求解器：`na70_core/calibration/plane_extrinsic.py`（RANSAC 平面、`R_IL` + 世界法向联合求解、时间偏移网格、四元数 SLERP）与 `tools/calibrate_na70_rotation_planes.py`；5 项 SYNTHETIC 测试恢复真值通过，静态 v4 会话正确判 `INSUFFICIENT_MOTION`。v4 会话无大平面（残差 P95 1.52 m），需要重新采集正对大平墙的慢速三轴转动；证据 `Plan/evidence/slam/20261030-session-audit-v4/`。

后续仍未完成：NA70 专用 ROS2 帧内时间 handler/数据桥接；实际 USB IMU 协议与驱动；本套硬件同步、外参标定、运动补偿；NA70 移动 LIO、真实回环与完整操场地图；Jetson/ARM 验收。T8/T9 只是离线计算与装配软件，不能升级这些门禁。

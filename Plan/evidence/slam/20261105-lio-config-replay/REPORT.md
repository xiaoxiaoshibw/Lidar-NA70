# 20261105 新配置重放前后对比（可选步骤，DIAGNOSTIC_ONLY）

数据段与 [20261104-lio-diagnostic](../../20261104-lio-diagnostic/REPORT.md) 完全相同：会话 `20261104-170341` 第 10 帧起 100 帧、200 万点（`lio-package/manifest.json`，`--imu-margin-s 1.0`，默认 `packet` 点时间模式）。唯一变量是 FAST-LIO 配置：旧运行用当时配置（哈希 `713460dd…`，fov 360 / filter 0.3 / blind 0.5，证据目录内 `fastlio-config.yaml` 副本，**未修改**）；本运行用 `na70-new.yaml`（哈希 `901bce59…`，fov 35 / filter 0.15 / blind 7.4）。

运行方式：`run_replay.sh`，WSL2 Ubuntu 22.04 / Humble，FAST-LIO `cc3c9e8b…`（工作区未改动，通过 `config_path` 指向本目录配置）；pace 0.2、linger 3、discovery 5、recorder 43 s，与旧诊断脚本一致；ROS_DOMAIN_ID 90（旧 89）。

## 内部统计（允许记录的全部结论）

| 统计 | 旧配置（20261104 证据） | 新配置（本目录） |
| --- | --- | --- |
| 位姿行数 | 81 | 85 |
| 记录时间跨度 | 10.389 s | 10.511 s |
| 路径长度 | 43.507 m | 30.312 m |
| 位姿最大间隙 | 0.361 s | 0.244 s |
| 位姿中位间隙 | 0.119 s | 0.120 s |
| 距原点最大半径 | 41.753 m | 29.834 m |
| 单步最大位移 | 3.529 m | 2.902 m |

`replay_exit=0 recorder_exit=0`（`exit-status.txt`）。

## 边界声明

- 上表只是两次运行的内部统计，**不得归因于任何具体参数、不得宣称精度改善或恶化**：时间等级 C、陀螺单位推断、单位外参、无独立真值。
- 本运行不升级任何状态：动态 LIO 保持 `DIAGNOSTIC_ONLY`，轨迹仅作诊断，`ACCURACY_NOT_EVALUATED`。
- 输入包 `lio-package` 为本次新导出（与旧包相同导出参数；默认 packet 模式与历史行为回归一致），本目录及旧证据目录内容均未被修改。

## 补充（2026-11-05，源码核查）

对本工作区 FAST-LIO `cc3c9e8b…` 的 `laserMapping.cpp` 逐文件核查：`mapping.fov_degree` 在该版本中**未被算法使用**——`FOV_DEG`/`HALF_FOV_COS` 仅在参数读取处计算（883–884 行），`lasermap_fov_segment()` 只依赖 `mapping.det_range`，全源码树无其他引用。因此本目录前后对比中的有效配置变量只有 `filter_size_surf/map`（0.3→0.15）与 `blind`（0.5→7.4）；`fov_degree` 360→35 是记录修正，不构成行为变化，也不能解释两次运行差异。此核查只覆盖本工作区该 commit，不外推到上游其他版本。

## 补充（2026-11-05，对比图）

`trajectory-comparison.png`：两次运行的位姿轨迹三视图（X-Y/X-Z/Y-Z，▲起点 ▽终点）。仅内部统计可视化，坐标是各自地图系，两轨迹不共坐标系；不归因、不宣称改善。绘图脚本按上表 CSV 复算生成。

## 补充（2026-11-06，对比工具 canonical 化）

重放对比的统计复算与三视图此后由 canonical 工具 `tools/compare_lios_replays.py` 产出（本目录 `trajectory-comparison.png` 为当时临时绘图，保留不动）。已用该工具对本目录与 [20261104](../../20261104-lio-diagnostic/REPORT.md) 两份 `trajectory.csv` 复算，上表 14 项统计全部逐字吻合（81/85 位姿、10.389/10.511 s、43.507/30.312 m、0.361/0.244 s、0.119/0.120 s、41.753/29.834 m、3.529/2.902 m）。复现命令见 `docs/NA70_CALIBRATION_WORKFLOW.md` §7.1。下次采集 `--point-time-mode interpolated` 与 packet 同段重放的对比即用此工具出证据；边界声明不变（各轨迹不共坐标、仅内部统计、不归因、`ACCURACY_NOT_EVALUATED`）。

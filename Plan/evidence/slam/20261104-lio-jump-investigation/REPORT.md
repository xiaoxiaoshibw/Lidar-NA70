# 2026-11-04 轨迹跳变与 ROS2 回放送达诊断

**状态：DIAGNOSTIC_ONLY。** 复用 `20261104-lio-diagnostic` 的同一输入包、配置和轨迹；现场是否原地或平移没有独立证据。本调查不更改原始文件、生产回放器、时间或点云筛选。FAST-LIO 原始结果不是可信动态 LIO 或绝对地图评价。

## 实测事实：跳变与时间批次

- 轨迹 81 位姿，80 个相邻间隔；按 `(前一位姿时刻, 后一位姿时刻]` 是否含 IMU **主机接收时间**分组。17 个含批次的间隔，位移中位数 2.850409 m、最大 3.528646 m；63 个无批次间隔，中位数 0.000455 m、最大 0.003113 m。最大的 10 个位移间隔均含一个批次，各有 122–123 个 IMU 帧共用该主机时刻。这是观察性关联，并非真实运动或误差的测量，不能由此单独证明原因。
- 最大跳变在轨迹第 77→78 个零基索引之间，`1793783034.401329→1793783034.532156`，相差 0.130827 s、位置步长 3.528646 m、姿态步长 2.014830°；后时刻与源扫描帧 97 的末包主机时间相同。该帧 20000 射线中 3140 条解码距离至少 100 m，最大 694.320 m；这不是经过协议确认的真实回波。候选 IMU 范数为 0.147–7.207 deg/s 和 9.533–10.033 m/s²，单位及轴向仍未独立核验。详见 `analysis.json`（就近扫描为时间邻近，不保证唯一对应）。
- 全输入包 2812 条 IMU 仅 23 个不同主机接收时间，2789 个连续相邻时间戳相等，相邻不同批次最大间隔 0.615628 s。批次间隔并非同步误差上界；无真实 IMU 采样时间，不能证明批次内的采样时间或连续性。雷达逐点时间是复制的包接收时间，不是逐点采样时间。
- 固定版本 FAST-LIO 的 IMU 处理按相邻输入消息时间差传播；共享主机时间产生零时间间隔，批次间出现较大接收时间间隔。这是代码行为及输入特性，不等于已经确认的跳变根因。实际 FAST-LIO 接收帧数、逐帧配准残差和所用数据未记录。
- 原 FAST-LIO 日志有 9 条 `No point, skip this scan!`，10 条 PCL voxel-grid 整数索引溢出警告；日志没有足够的逐扫描可关联标记，不能断定警告与特定跳变一一对应。未确认的超远距离编码及有效点规则仍可能影响匹配；没有实验证明它单独造成跳变。平面可观测性、点云重影和绝对地图精度仍为 `NOT_EVALUATED`。

## 受控轻量探针：验证发布数不等于送达数

WSL2 Ubuntu 22.04 / ROS2 Humble、Cyclone DDS、`OMP_NUM_THREADS=2`。两次使用同一 `lio-package-margin1s`、同一轻量 `probe.py` 单线程订阅（IMU reliable depth 10、PointCloud2 sensor-data QoS），同样 `--pace 0.2 --linger 3 --discovery-wait 5`。探针不运行 FAST-LIO、回调比 FAST-LIO 轻；每次为隔离使用不同 ROS domain。单次实验不能保证其他执行负载或调度下的结果。

| 对照 | 发布器报告 IMU / 扫描 | 探针收到 IMU / 扫描 | 结果与时长 |
|---|---:|---:|---|
| 原始突发回放，domain 91 | 2812 / 100 | 1568 / 92 | 双方退出码均 0；未记录进程耗时 |
| 独立候选，domain 92 | 2812 / 100 | 2812 / 85 | 双方退出码均 0；发布进程 32.797 s |

候选与原始回放唯一代码差异：每个 IMU `publish()` 后固定 `sleep(0.001)` 秒；不修改时间戳、帧内容或包范围。`probe-count-check.log` 对比每个 ROS 纳秒时间戳的计数，候选探针与输入包逐时间戳计数一致；这不是逐帧内容校验。候选仍未收到全部扫描，也未测试 FAST-LIO 实际接收，因而它不是可靠传输修复、采样时间重建或轨迹改善的证明。`replay.log` 的 2812 仅为历史发布端计数。此次结果证明原始回放在此探针条件下未完整送达，不可把差额直接当作 FAST-LIO 的丢失数。

## 单位、变换与暂定条件

原实验中激光点坐标及外参平移按米，点时间以秒送入，固定版本 preprocess 配置 `timestamp_unit: 0` 将秒换为内部毫秒。IMU 角速度根据未经实物核验的 deg/s 假设转换成 rad/s，加速度字段按 m/s² 使用；坐标轴、重力比力含义与真实外参均未独立证实。源码 `laserMapping.cpp:218` 为 `p_IMU = R_IL * p_LiDAR + t_IL`；配置使用单位旋转、零平移，关闭外参估计和自动时间同步，仅为诊断假设。没有可用的平移证据或冻结标定。

## 复现、来源和限制

原记录和配置在 `../20261104-lio-diagnostic/`；该目录保存的详细版本、固定源 commit、数据包、原日志和轨迹是本调查输入。原仓库 HEAD `751faab3efcc7fadba9f264cf68cd364d616416f`；WSL FAST-LIO HEAD `cc3c9e8b1469b33b8072764d6db2348ed6dc597f`，源码工作区的 `preprocess.cpp`、`preprocess.h` 有 NA70 适配修改，`config/na70.yaml` 未跟踪。实际 `laserMapping.cpp` SHA-256 `2055037366b7f0451a914fc4b9fae90527d4ac1388fd31daa7491d41904adb61`，`preprocess.cpp` `c279700992d213d26113d3b0030056507612f9535d6f784a4dd6e238c8ad1f95`，`IMU_Processing.hpp` `2b786c50c255a118fe95c9b017ef13af9e27a79b0624a718d4e192c3e5ca138d`。输入和脚本哈希见 `hashes.txt`。

历史执行命令（脚本内输出路径固定；复现前请复制并修改到新的空目录，避免覆盖证据）：

```powershell
web\.venv\Scripts\python.exe Plan\evidence\slam\20261104-lio-jump-investigation\analyze.py
wsl -d Ubuntu-22.04 -- bash /mnt/d/Code/radar/Plan/evidence/slam/20261104-lio-jump-investigation/run_probe.sh
wsl -d Ubuntu-22.04 -- bash /mnt/d/Code/radar/Plan/evidence/slam/20261104-lio-jump-investigation/run_probe_candidate.sh
```

日志和逐时间戳计数分别见 `probe*.json`、`probe*-replay.log`、`probe*-exit-status.txt`；候选源码单独保存为 `replay_candidate.py`。额外校验用原包时间戳与探针逐时间戳计数比较，结果保存在 `probe-count-check.log`。原始与候选中扫描缺失，证明此探针条件下两个回放都不满足完整送达。

下一步可继续设计具有端到端接收计数、序号核验和可控背压的独立回放实验，并核对 FAST-LIO 实际接收及扫描处理情况；真实同步、外参和独立真值未建立前不得升级为可信动态 LIO。ATE、RPE、绝对地图精度、Jetson 性能均 `NOT_EVALUATED`。本调查无需新增现场数据；后续现场采集动作以总报告的单组指令为准。

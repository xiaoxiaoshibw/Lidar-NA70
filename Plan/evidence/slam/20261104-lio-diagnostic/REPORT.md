# 2026-11-04 当前采集：标定与 LIO 诊断

**状态：DIAGNOSTIC_ONLY；动态 LIO、地图准确度及同步均未通过。** 用户说明是拍摄操场、大致旋转；是否严格原地、是否平移尚无独立证据。不得用这一描述作为位移真值。

## 实测与结论

- PCAP SHA-256 `e0e4d9f73d4332f0ac40aad18f264d26d1cbb1888756c2d837ba828a71bc3128`，IMU JSONL `c661d5510be9e797e4d5fecd5dcf812bb0ab676f6b80957ddf629c454adee92b`。输入包第 10 帧起共 100 帧、200 万点，11.943145 s；每 200 点共用接收包时间，最大包间隔 8.004 ms。`lio-package/manifest.json` 列出默认选取 ±0.1 s 时 IMU 不包围扫描的情况；另以 ±1 s 选取原样 IMU 得 `lio-package-margin1s`，端点包围不代表内部有效采样时间覆盖。后者仍 `EXPORTED_BLOCKED`：时间等级 C、无校验外参、运动包络未知。
- `prepare_lio_session.py` 按原始 host CSV 执行，报 `IMU CSV requires device_time_s,gx,gy,gz,ax,ay,az`：CSV 同时不具该规范列名及设备时间；未改造主机时间伪造设备时间。预检 `BLOCKED`。
- 静置诊断全部 10 窗口拒绝，均有加速度及角速度波动和主机接收间隔超限，不能据此定性物理运动、偏置或硬件故障。陀螺单位内验结果 `INSUFFICIENT_DATA`，最大 0.2 s 有效间隔设定下不足三个可用批次；deg/s 仍为推断。加速度单位及坐标轴也需独立验证。
- 平面法向全段求解因 IMU 端点不覆盖拒绝。选内部帧 20–799、步长 20 的诊断有 39 个平面，法向散布比 0.000746，状态 `INSUFFICIENT_MOTION`。输出中数字矩阵与搜索下限 -0.2 s 仅是弱可观测失败求解的内部结果，不是可用旋转和时间偏移；平移为 null，未进行留出验证。低残差不改变失败状态。该主平面可能不是墙，不能由原点到拟合平面的距离推定最近雷达测距或墙距。

## WSL2 回放与地图

固定 FAST-LIO `cc3c9e8b1469b33b8072764d6db2348ed6dc597f`，工作区实际检查的 commit 一致；配置与仓库 `tools/lio/na70_fast_lio.yaml` 哈希均为 `713460ddb0b273013ba61c10f0a81647ba7433fb2445068260ba1d5326154c60`，留存副本 `fastlio-config.yaml`。源码 `laserMapping.cpp` 的 `p_body_imu = offset_R_L_I * p_body_lidar + offset_T_L_I` 验证此版本使用 LiDAR→IMU，单位为米。此实验配置临时用单位旋转及零平移、关闭外参估计和自动时间同步，**只是运行假设而非标定**；陀螺由记录字段假设 deg/s 转 rad/s，运动补偿使用同包统一主机时间；批次内没有插值。安装方向使用默认 1。时间戳单位秒，PointCloud2 包含 x/y/z/intensity/time，time 为相对帧首的包时间。

原始批次时刻直接回放 100 扫描及 2812 IMU 帧，两个 topic 各有 1 订阅者；记录到 81 个位姿及最后一帧 9233 点 `latest-cloud.ply`，这不是完整地图。首尾时间跨度 10.389 s；最大位姿时间间隙 0.3605 s，路径长度 43.507 m、最大相邻位移 3.529 m、末端距离首位姿 41.765 m。这些是算法输出的内部统计，并非真实位移或误差评估，存在显著漂移/跳变，原因未查明，不能归因于单一因素。

FAST-LIO 未在本次流程中保存完整 PCD。为保留可追溯到帧的可视诊断，使用原始 PCAP 与输出位姿构建 `scene/map.ply`：将零外参实验下的 IMU 位姿按 LiDAR 位姿使用（这依赖上述单位外参临时假设），按包接收时间插值、体素 0.1 m，排除轨迹外及跨 0.3 s 位姿缺口点。100 帧中 86 帧贡献 550214 个体素代表点；轨迹覆盖外 260800 点、时间缺口 59400 点被丢弃。地图点距地图原点 P50 110.04 m、P99 305.43 m、最大 1055.23 m；该距离不等于传感器测距。最大值已追溯到源帧 96、帧内包 42 点 3，PCAP 偏移 14154559 的七字节全 FF，距离及两个角度码均为 65535：解码得到 1048.56 m，再变换得到该地图极值；重算坐标相差 0.018 mm。项目历史将距离码 0xFFFF 标为无效码，sweeps 将角码 0xFFFF 视为方向未知；场景构建直接接收解码数组未过滤这类原码。该极值不能作为可信物理回波；详见 `extreme-point-trace.json`。不同路径的有效性口径不一致：观测契约的 `valid_return` 只判非零及有限，当前 `direction_known` 亦把该全 FF 射线视为方向已知；因此不能断言厂家协议含义，亦不能据此判轨迹跳变的原因。地图只保存 `source_frame`，不含逐点包与点索引；本次是结合原包重建反查极值，完整索引存于扫描包。点云重影、平面一致性因未指定可信稳定场景 ROI 和独立参考而 NOT_EVALUATED；ATE、RPE、绝对精度均 NOT_EVALUATED。无回环或全局优化。

为量化规则差异，不改变生产代码或原数据，另作 `candidate-sweep-mask-scene` 对照：使用现有 sweeps 规则“距离码大于零且两角度码均非 0xFFFF”，它与 RayBatch 当前口径冲突，尚无厂家协议确认，故仅为候选假设。200 万射线中过滤 1,050,405 条（在时间筛选之前），剩余候选地图 549,777 体素、最远距地图原点 700.78 m，仍有未经确认的大范围点，不能把过滤当作地图正确性或位姿改善。该对照不重跑 LIO，轨迹不变。复现见 `build_candidate_map.py` 及 `candidate-sweep-mask-scene/report.json`。

## 复现与限制

Windows 仓库根执行，输出目录必须不存在：

```powershell
web\.venv\Scripts\python.exe tools\export_na70_lio_scans.py --pcap radar_capture_data\na70_sessions\20261104-170341\raw\lidar.pcap --imu-frames radar_capture_data\na70_sessions\20261104-170341\imu_frames.jsonl --start-frame 10 --max-frames 100 --imu-margin-s 1.0 --output <new-package>
web\.venv\Scripts\python.exe tools\calibrate_na70_rotation_planes.py --pcap radar_capture_data\na70_sessions\20261104-170341\raw\lidar.pcap --imu-frames radar_capture_data\na70_sessions\20261104-170341\imu_frames.jsonl --start-frame 20 --end-frame 800 --frame-step 20 --output <new-rotation.json>
web\.venv\Scripts\python.exe tools\build_na70_scene.py --pcap radar_capture_data\na70_sessions\20261104-170341\raw\lidar.pcap --poses <trajectory-with-tx-ty-tz-header.csv> --pose-frame lidar --start-frame 10 --max-frames 100 --voxel-size 0.1 --max-gap-s 0.3 --output <new-scene>
```

`run_fastlio_diagnostic.sh` 记录 WSL2/Humble 回放参数；输出应定向至新目录，勿覆盖本次证据。首次启动脚本因 Bash `set -u` 不兼容 ROS setup 失败；删除该选项后完成回放。结束时 launch 后台进程没有响应 SIGINT，人工仅向其子 `fastlio_mapping` PID 发 SIGINT 后正常退出；脚本已补充仅向该子进程发信号。产物及日志 `trajectory.csv`、`latest-cloud.ply`、`scene/`、`replay.log`、`recorder.log`、`fastlio.log`、`diagnostic-stats.json` 和输入包均保留。`summarize_diagnostic.py` 可复算统计。关键代码摘要见 `code-hashes.txt`。仓库 HEAD：`751faab3efcc7fadba9f264cf68cd364d616416f`。

SYNTHETIC 数学/装配及标定相关测试在 web 目录执行 47 passed，`synthetic-tests-retry.log`；首次从根目录执行因 `backend` 不在模块路径而收集失败，保留 `synthetic-tests.log`。这不是实物标定、同步、地图精度或 Jetson 验收。进一步需要有效同步依据、单位与坐标轴证据、可观测的旋转和独立平移测量、留出数据及真值，才能进入可信动态 LIO 验证。

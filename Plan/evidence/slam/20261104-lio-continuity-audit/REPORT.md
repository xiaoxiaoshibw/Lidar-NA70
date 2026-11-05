# 2026-11-04 NA70＋WHEELTEC 会话及 LIO 连续性复核

**状态：DIAGNOSTIC_ONLY。** 本目录汇总互相独立的新证据；真实同步、外参、动态 LIO 与绝对地图准确度没有通过。没有回环与全局优化，只称 LIO 轨迹及局部地图。原始数据、历史失败记录与原有工作区修改均保留。

## 已实测事实

- [会话重新审计](../20261104-session-independent-recheck/REPORT.md)未发现 `20261104-170341` 后的新现场会话。该会话 83,351 个有效雷达载荷、20,052 个完整 IMU 帧；原始重放尾部 4 字节不完整，164 个接收块均为 8192 字节。两路主机接收端点交叠 99.639859 s，不证明中途无损或同步。串口修复尚未实机复测，应用接收数量不是网络丢包计数。
- 未见 IMU 0x51/0x52；仍为 `C_HOST_RECEIVE_ONLY`，没有引入批内插值。主机接收时刻不是采样时刻，观察到的批次间隔不是误差上界。雷达包尾依驱动可解释为内部秒/毫秒，但采样语义和与 IMU 时钟映射未知；`point_time_s` 是主机包时间复制的 `PACKET_APPROXIMATION`，不是实测逐点时间。
- [原码普查](../20261104-encoding-investigation/REPORT.md)与[跳变调查](../20261104-lio-jump-investigation/REPORT.md)保留原始索引和对照口径。生产解码及点有效规则没有因未确认的 `0xFFFF` 语义而改动。
- 原 100 帧诊断轨迹有 81 位姿、相邻最大位移 3.529 m。跳变与 IMU 接收批次高度时序相关；这不证明因果，也不能排除时间、投递、外参、单位、几何或其他原因。IMU 发布次数不等于 FAST-LIO 接收次数，历史回放没有接收计数。独立 ROS2 订阅探针在指定测试条件下收到 1,568/2,812 条已发布 IMU 和 92/100 扫描；另一次使用每条 IMU 发布后等待 1 ms 的候选副本时，探针收到 2,812/2,812 条 IMU 和 85/100 扫描。两次均为单次实验；延时不改变样本时间，探针并未同时运行 FAST-LIO，不能推算 FAST-LIO 接收数或证明跳变已修复，详见跳变调查的条件和日志。
- 原场景装配 86 帧贡献 550,214 体素代表点，轨迹覆盖外及时间缺口点分别丢弃 260,800、59,400；原点极值来自可追溯全 FF 原码。极值候选过滤仅为假设，未改变轨迹，不能证明地图正确。重影和平面一致性缺少可信稳定 ROI 和参考，`NOT_EVALUATED`；ATE、RPE、绝对地图精度缺少独立真值，同为 `NOT_EVALUATED`。

## 本次开发与 SYNTHETIC 验证

旧 `time_quality` 逐帧计算不同接收时刻的间隔，忽略帧边界，可能漏报长间隔。现按原包索引提取包时刻并统计相邻包，包括跨帧及相同主机时间的包；不插值也不改变点或时间。新增单元和从合成 PCAP 到导出 manifest 的端到端测试，确认跨帧 0.501 s 缺口产生 `packet_gap_over_limit`。修正文档中旧会话路径及把批次间隔混同采样误差的表述。

对相同 100 帧、200 万点历史扫描包只读复算：[packet-gaps.json](packet-gaps.json) 中旧帧内最大 8.003950 ms、帧边界最大 3.235102 ms、新统计最大仍为 8.003950 ms，按原 0.2 s 阈值没有超限；该事实既不表示连续无损，也不能证明时间同步。历史 manifest 不被覆盖。

```powershell
cd D:\Code\radar\web
.\.venv\Scripts\python.exe -m pytest tests/test_na70_lio_scans.py tests/test_na70_lio_export.py tests/test_lio_inputs.py tests/test_na70_calibration_batch_report.py tests/test_na70_plane_extrinsic.py tests/test_na70_scene.py tests/test_na70_capture.py tests/test_na70_imu_timebase.py tests/test_na70_session_report.py tests/test_local_capture_dual_stream.py -q
cd D:\Code\radar
web\.venv\Scripts\python.exe Plan\evidence\slam\20261104-lio-continuity-audit\check_packet_gaps.py
web\.venv\Scripts\python.exe Plan\evidence\slam\20261104-lio-continuity-audit\provenance.py
```

合成测试 **86 passed**，见 [synthetic-tests.log](synthetic-tests.log)；不是硬件、同步、标定或性能验收。`cli-help.txt` 保存本次相关工具实际 `--help`；[provenance.json](provenance.json) 保存输入清单与 SHA-256、实际修改文件和关键代码/配置/产物哈希、Git HEAD、工作区状态和 Windows/Python/依赖版本。采集原始文件未复制进证据目录；其它子报告分别保存自身命令、配置、版本和日志。

## 诊断假设与未验证条件

历史固定 FAST-LIO 配置采用单位旋转和零平移、关闭外参估计及自动同步；角速度按推断的 deg/s 转 rad/s，加速度单位和安装方向未经本次独立校验，点使用解码输出及主机包时间。这些均为临时运行假设。固定版本源代码方向是 `p_IMU = R_IL · p_LiDAR + t_IL`，平移单位米；平面法向求解因 `INSUFFICIENT_MOTION` 不可用，平移保持 `null`，没有生成冻结标定。设备型号、轴向、物理量程、实际时间映射及延迟界、雷达真实网络损失、轨迹和地图的真值均待验证。

允许继续离线调查和工具检查；不得声称可信动态 LIO、真实外参或 SLAM 验收。WSL2/Humble x86_64 只证明该环境的集成现象，Jetson Orin Nano/Humble 的性能及长期运行必须独立实测。

## 下一组最小现场操作

重新连接雷达和 WHEELTEC IMU，刚性连接后将整机放稳，雷达正常工作并保持静止。打开本机采集页面，核对实际串口及雷达网卡地址，点击开始；确认两路计数增长，录制 60 秒后点击停止并等待保存。成功信号为两路有数据、正常停止且生成新会话目录；再复核读取块大小、时刻、序号、校验与停止尾部。这只复测采集，不证明同步。未来墙面旋转采集墙距至少 8 m、建议 9–15 m；独立动态验证须另次采集保留。

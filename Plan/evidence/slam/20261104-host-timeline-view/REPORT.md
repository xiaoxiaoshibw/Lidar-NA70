# NA70 与 IMU 主机接收时间诊断回放

状态：**DIAGNOSTIC_ONLY**。打开同目录 `index.html` 可离线播放；若重做请使用不存在的新目录，禁止覆盖本证据或原始采集。保留会话原始数据和历史失败记录。

## 展示内容和事实

- 使用同一次双流录制的主机 Unix 接收时间，首个雷达包为相对时间零点。834 个雷达分组覆盖约 100.307 s；最后一组不足 100 包。IMU 20,052 帧归为 164 个共享时间的接收批次，首末批次分别为 0.621 和 100.261 s。两路可按主机时钟粗关联；首末时刻差不是传感器时间偏移。
- 左侧显示所选雷达分组在雷达坐标系下的点云，未用 IMU 或 LIO 变换。每帧保留来源帧内点索引：点云由仓库 `backend.decoder` 解码，按源序最多抽样 1200 点；仅显示时排除零距离及距离或角度码 `0xFFFF`，再按下拉菜单的显示距离裁剪。`0xFFFF` 是未确认厂家语义的候选显示过滤，不修改原始数据或生产有效性规则。
- IMU 匹配取当前雷达帧**最后包主机时刻之前最近批次的最后帧**，显示批次帧数、主机接收时刻和距当前帧末的间隔。首批之前或末批之后明确显示无数据。没有推算批内时间或插值。姿态图显示每批全部帧的欧拉角范围及末帧；批内共同时间不是同时采样。欧拉角、角速度的度与度/秒为推断，设备姿态世界系、轴系与加速度单位仍未独立核验。位置不能仅由该 IMU 记录可靠恢复。
- 右侧是历史 FAST-LIO 诊断输出的 XY 轨迹，81 位姿覆盖相对 2.715–13.105 s，仅用于观察；在覆盖范围外明确显示无当前位姿。它使用单位外参、未核实的时间与单位等假设且有跳变，不是 IMU 独立位置轨迹、真值或可信地图；不应用于左侧点云。
- 页面不能证明两传感器采样同步、真实外参、轨迹或地图精度。批次主机到达时间不能界定采样时刻或误差上界。没有回环与全局优化。ATE、RPE、绝对精度仍为 `NOT_EVALUATED`。

## 实际命令及验证

在仓库根目录运行，输出目录必须不存在：

```powershell
web\.venv\Scripts\python.exe tools\na70_host_timeline_export.py --session radar_capture_data\na70_sessions\20261104-170341 --trajectory Plan\evidence\slam\20261104-lio-diagnostic\trajectory.csv --output Plan\evidence\slam\NEW-HOST-TIMELINE
cd web
.\.venv\Scripts\python.exe -m pytest tests/test_na70_host_timeline_export.py -q
```

输入及脚本 SHA-256、帧数和输出哈希保存在 `manifest.json`，导出标准输出保存于上级 `20261104-host-timeline-export.log`。SYNTHETIC 测试检查源点索引、显示筛选、批次范围和末帧：[synthetic-tests.log](synthetic-tests.log)。开发时原 Playwright 默认 headless 浏览器不可用，失败见 `browser-check.log`；改用本机 Chrome 的历史运行验证了真实数据页面初始化、覆盖边界、帧选择、播放推进和无 JavaScript 错误，结果 `browser-check-chrome.log`，画面 `preview.png`。此验证只检查显示流程，并非采样同步或精度验收。

后续仍可完善 IMU/雷达的真实时间关系和传输完整性；若补采，优先按总报告提供的单组 60 秒静置复测动作进行，不把此视图作为动态标定已通过的证据。

# NA70 + WHEELTEC IMU 主机时间 3D 装配回放

状态 `DIAGNOSTIC_ONLY`。使用一次同时录制的数据，在同一主机时间轴展示约 100 秒雷达点云和 IMU 数据。双击 `index.html` 可离线使用，原始数据和旧证据未被覆盖。

## 展示和限制

- 834 个雷达分组（最后一组不完整），164 个 IMU 接收批次，共 20,052 个 IMU 完整帧，主机接收时间相对雷达首包为 0.621–100.261 s。每批原始帧保留 `tid`、四元数、欧拉角、角速度、加速度；手动批次与批内帧序控件可访问每一帧，批内自动演示间隔 35 ms 仅为显示节奏，**不是设备采样时间**。主时间轴按雷达帧末查找之前最近 IMU 批次；手动选批时页面显示时间差，不声称与当前雷达帧匹配。
- 装配模型来自仓库现有 `tools/imu_live/na70-yesense-approx-mm.obj`（外壳 W 90、D 130、H 110 mm，IMU 示意中心约 [-43,0,60] mm）。模型姿态以 IMU 设备输出四元数 `[w,x,y,z]` 相对首帧计算 `conj(q_first) * q_current`，并将其作用于整个模型。**假设四元数为 IMU→世界且 IMU 轴与装配外壳轴一致；安装方向、物理旋转方向和外参没有独立验证。** 模型中心固定，仅表示平移未知，不表示设备实际静止。
- 角速度数值与整段 X/Y/Z 曲线由每批末帧及批内范围展示，单位 deg/s 为推断。欧拉角和批内范围亦按接收时间展示，没有批内时间插值。经主机接收时间关联不是验证的采样同步；批量读入的时间间隔不是采样误差上界。
- 雷达显示仍在雷达自身坐标系，并不使用上述模型姿态或未经标定的 LIO 结果变换。仅展示时排除零距离及 0xFFFF 码，再按源序每帧最多取 1200 点；显示距离可调，厂家对 0xFFFF 的语义未确认。原始点及完整源索引保持在原 PCAP 中。
- 右侧已有 81 个位姿的 FAST-LIO XY 轨迹仅覆盖 2.715–13.105 s，存在 3.529 m 最大相邻跳变、采用单位外参等假设，仅为算法诊断，不是 IMU 独立位置或真值；覆盖外无当前位姿。其不驱动 3D 模型的位置，也不组成可信地图。

## 复现与验证

输入文件、生成器、模板、模型 OBJ 和输出页面哈希见 `manifest.json`。在仓库根目录执行，输出必须为不存在的新目录：

```powershell
web\.venv\Scripts\python.exe tools\na70_host_timeline_export.py --session radar_capture_data\na70_sessions\20261104-170341 --trajectory Plan\evidence\slam\20261104-lio-diagnostic\trajectory.csv --output Plan\evidence\slam\NEW-IMU-MODEL-TIMELINE
cd web
.\.venv\Scripts\python.exe -m pytest tests/test_na70_host_timeline_export.py -q
```

合成导出测试 1 passed，检查选点源索引、批内帧序、设备字段、角速度范围及模型存在性。`verify_export.py` 对照原 JSONL 的全部 20,052 帧，验证 `tid`、四元数、欧拉角、角速度、加速度及批次主机时刻逐项一致，结果在 `verify-export.json`。历史回放视图曾进行浏览器检查，但本版本尚无新的浏览器可视检查，自动打开 `file://` 被当前浏览器安全策略拒绝。时间同步、绝对姿态、位置、地图精度和 Jetson 性能均未由本视图验证。

补充的 SYNTHETIC 检查：在仓库根目录执行 `node web/tests/test_na70_timeline_math.cjs` 与 `node web/tests/test_na70_timeline_ui.cjs`，均通过。前者检查单位旋转、90° 旋转、四元数正负号等价和首帧归零；后者用模拟 DOM/画布接口检查时间边界、手动选批和批内逐帧控件。日志分别见 `quat-test.log` 和 `ui-logic-test.log`，脚本及页面哈希见 `verification-hashes.json`。模拟检查不代替浏览器视觉验收。

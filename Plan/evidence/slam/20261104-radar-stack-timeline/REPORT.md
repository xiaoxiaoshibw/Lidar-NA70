# 雷达历史帧堆叠回放

**DIAGNOSTIC_ONLY。** 此视图在既有 100 秒雷达与 IMU 回放上增加雷达帧堆叠；输入不变，原始会话及旧证据未覆盖。

## 显示口径

- 下拉框提供 1、5、10、20、50、100 帧，默认 10 帧。只显示当前帧与其之前至多 N−1 帧，不读取未来帧；开始不足 N 帧时只显示实际存在的帧。
- 所有帧在各自雷达坐标系的数值直接叠加，没有根据 IMU 或 LIO 进行运动补偿或配准。若设备移动或旋转，可能重影；此结果不能称为全局地图或定位精度评价。越早的帧画得越淡，颜色仍反映高度。
- 画面报告实际源帧区间、包主机接收时间首末及跨度、原始射线数、符合显示假设的点数、均匀抽样点数、视口点数，以及当前尾部分组是否不完整。雷达数据仍使用现有的 `backend.decoder`；每帧最多显示抽样 1200 点，排除距离 0 和距离/角度 `0xFFFF` 后再按距离裁剪，厂家对 0xFFFF 的语义仍未确认。大帧数可能造成绘制变慢，100 帧最多 120,000 个抽样点。
- 原来的 IMU 姿态模型、角速度、批次和诊断 LIO 轨迹保持原有语义；它们不参与堆叠变换。播放过程中仅进入新雷达帧时重绘；拖动时间轴、切换堆叠数或显示距离及旋转视角均触发重绘。

## 真实数据检查

`manifest.json` 保存了原 PCAP、IMU、轨迹、OBJ、生成器、模板及 HTML 的 SHA-256；`verify-stack.json` 将 834 帧点云、164 个 IMU 批次、历史轨迹、模型和原点与前一版逐项对比，均一致，仅模板行为不同。例：选 10 帧且当前帧为 60 时，源帧 51–60、主机包时间 6.068–7.255 s、抽样总数 12,000；最后帧 833 选 100 帧时为源帧 734–833、抽样 120,000，最后一组不完整。这些统计仅描述导出的显示点，不是运动补偿或地图质量结论。

## 复现与测试

Windows，Python 3.12.10、Node 24.14.0，Git HEAD `751faab3efcc7fadba9f264cf68cd364d616416f`，工作区包含未提交修改；实际文件哈希在 `verify-stack.json`。输出目录须不存在：

```powershell
web\.venv\Scripts\python.exe tools\na70_host_timeline_export.py --session radar_capture_data\na70_sessions\20261104-170341 --trajectory Plan\evidence\slam\20261104-lio-diagnostic\trajectory.csv --output Plan\evidence\slam\NEW-RADAR-STACK
web\.venv\Scripts\python.exe Plan\evidence\slam\20261104-radar-stack-timeline\verify_stack.py
node web/tests/test_na70_timeline_stack.cjs
node web/tests/test_na70_timeline_ui.cjs
node web/tests/test_na70_timeline_math.cjs
cd web
.\.venv\Scripts\python.exe -m pytest tests/test_na70_host_timeline_export.py -q
```

`stack-test.log` 的 SYNTHETIC 数据验证首帧、中帧、末帧及无未来帧、显示点数、切回单帧和距离过滤；`ui-test.log`、`math-test.log` 和 `synthetic-tests.log` 均通过。该页面尚未重新进行浏览器视觉检查：先前浏览器自动打开本地 `file://` 被安全策略拒绝；Node 模拟测试不代替视觉验收。真实时间同步、位姿、绝对地图精度及 Jetson 性能仍未验证。

# 100 秒全候选点密度诊断回放

**DIAGNOSTIC_ONLY。** 本目录的 `index.html` 使用同一次 `20261104-170341` 会话。用户说明前 80 秒为手持举起时的正常抖动；不能据此假定严格静止或无平移。雷达坐标系数值直接堆叠、没有运动补偿或配准；不解释为世界坐标地图。

## 对照和实际内容

- 834 个雷达分组，前 833 组各 100 包、末组不完整。经既有显示假设过滤（排除距离码 0 和距离/角度码 0xFFFF；厂家语义仍未确认），导出上限设为 20000 点/组，因此保存全部 **6,600,738 条候选射线**，包含 XYZI 和对应帧内源点索引。原始 PCAP 完全不变，解码仍调用 `web/backend/decoder.py`。本页实际导出点数由 `verification.json` 对 834 组逐组检查。
- 同一源帧 91–100、同一 150 m 条件：全候选 79,005 点；旧版 1200 点抽样 10,109 点（视口内实际显示可能更少）。全部选项与旧版 1200 显示模式使用同一数据和筛选顺序；旧版帧内取样按 `np.linspace` 口径重现。六帧在第一版 JS 先乘后除时因浮点取整有差异，已改为先计算步长并固定末点；v3 对全部 834 帧检查零差异，v2 中 `verification.json` 和 `verification-trial.json` 保留该过程。
- 默认仅为方便观察选择 50 m、全部已导出候选，可切换 100/150/300/1050 m，显示上限可选全部、1200、4000、8000；切换只影响画面。可选 XY 俯视、XZ 侧视、立体视角以及拖动和缩放。不同距离和视角不会增加原始信息。`50 m` 是观察范围而非识别结果或物理阈值。
- 仍显示同一时间轴的 IMU 模型和角速度；姿态相对首帧示意，真实轴向/外参和设备时钟映射未知。不同帧原样叠加会因手持运动出现重影，不能从图像外观判断传感器 SLAM 能力或地图准确度。

## 来源、复现与验证

Windows，Python 3.12.10、Node 24.14.0，HEAD `751faab3efcc7fadba9f264cf68cd364d616416f`；工作区原有修改保留。`manifest.json` 包含会话、轨迹、OBJ、代码及 HTML 的哈希；`code-hashes.json` 记录测试文件和复核脚本的哈希。使用不存在的新输出目录：

```powershell
web\.venv\Scripts\python.exe tools\na70_host_timeline_export.py --session radar_capture_data\na70_sessions\20261104-170341 --trajectory Plan\evidence\slam\20261104-lio-diagnostic\trajectory.csv --points-per-frame 20000 --output Plan\evidence\slam\NEW-FULL-DENSITY
web\.venv\Scripts\python.exe Plan\evidence\slam\20261104-radar-full-density-v3\verify_full_density.py
node web/tests/test_na70_timeline_stack.cjs
node web/tests/test_na70_timeline_ui.cjs
node web/tests/test_na70_timeline_math.cjs
cd web
.\.venv\Scripts\python.exe -m pytest tests/test_na70_host_timeline_export.py -q
```

`verification.json` 对比上一版，检查 834 组的每个旧索引与 XYZI 均能从新导出重建、候选总数与每帧有效索引严格递增、IMU/模型/轨迹未变，以及页面和模板哈希；全部通过。`stack-test.log`、`ui-test.log`、`math-test.log` 均为 SYNTHETIC 通过，`pytest.log` 为 1 passed。`stack-test.log` 还验证密度切换、XY/XZ 视角和旧版浮点取整边界。测试不证明配准或几何特征质量。

完整单页 **179,899,740 字节（约 171.6 MiB）**；生成及对比脚本可运行，但浏览器加载时间、内存和绘制性能尚未实测，100 帧密集绘制可能缓慢。浏览器安全策略先前拒绝自动打开本地 `file://`，本版本无浏览器视觉验证。可使用上一版轻量 30 MB 页面作为备用。初版和 v2 的文件均保留；此 v3 为含修正 XY/XZ 标签与旧采样兼容的最终导出。

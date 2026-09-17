# 首版实测记录

## 2026-09-10：本机离线运动分析（W2 PARTIAL）

Windows Web 后端已接入固定雷达运动分析。首版启动体素背景在 003.pcap 上把双楔镜后续扫描方向误判为前景，现已改为使用原始俯仰码和航向码建立全片射线距离众数背景，只比较重复扫描方向；随后执行时序窗口、加权 DBSCAN、恒速 Kalman、匈牙利匹配和自适应事件阈值。分析结果随导入记录持久化；浏览器显示红色移动点、前景目标框、速度箭头、事件跳转和逐帧 CSV 导出。

算法结构参考项目及许可证记录在 `THIRD_PARTY_NOTICES.md`。当前实现没有直接嵌入参考仓库源码。

验证结果：

| 检查 | 结果 |
|---|---|
| Web 后端全部 pytest | 19 passed |
| 合成移动人员 | 前景聚类、轨迹确认、原始点索引映射和移动状态通过 |
| 合成新增静止人员 | 保持前景，不标记为实际运动 |
| 合成孤立噪点 | 邻域过滤通过 |
| API | 启动、进度、摘要、二进制掩码及服务重启恢复通过 |
| TypeScript／Vite | 构建通过 |
| Playwright `motion` | 分析入口、单帧切换、逐帧元数据和高亮开关通过 |

首版体素方案的真实工程性能使用同一台 Windows 11／Ryzen AI 9 H 365 本机、Python 3.12.10、NumPy 2.5.3、SciPy 1.17.1。以下 4.pcap／5.pcap 数字仅保留为 v1 性能记录，不能与当前射线历史 v2 的输出直接比较：

| 输入 | 帧数 | 点数 | 分析耗时 | 输出说明 |
|---|---:|---:|---:|---|
| 4.pcap | 234 | 4,677,800 | 0.845 s | 609 个去噪前景点、28 个轨迹移动点；没有帧达到整帧运动阈值 |
| 5.pcap | 338 | 6,750,000 | 1.061 s | 586 个去噪前景点、10 个轨迹移动点；没有帧达到整帧运动阈值 |

4.pcap／5.pcap 没有逐点或动作时间真值，上述输出只能证明性能、数据链和低变化场景行为，不能证明召回率或准确率。W2 保持 `PARTIAL`，待新采集的人群 PCAP 完成关键帧标注和参数验收。

003.pcap 首轮射线历史 v2 分析：448 帧、44.794 秒、8,958,600 个原始点，SHA-256 为 `025d4736159c9074a8903a29d17db4f5ace28dd53ea906e0e53ebcece3ceb343`。建立 111,600 个稳定重复射线方向，筛出 65,691 个距离变化候选点，经时序空间聚类和事件门限保留 41,897 个移动点。检测到两个主要时段：0.000～26.401 秒和 37.101～39.602 秒。本机 CLI 完整分析约 11.6 秒，Web 后台复测约 5.0 秒；轨迹会被非重复扫描拆成片段，轨迹片段数不得解释为人数。输出位于 `../2/analysis_003/`。

浏览器证据：`test-results/motion-analysis.png`。

## 2026-09-10：密集点云与相机修订（v0.2）

用户反馈默认画面太稀疏、相机难操作。当前默认已替换为 200 帧密集窗口、无抽样；支持整包全部点，原始解码器不变。下面的 2026-09-09 记录属于 v0.1 历史配置，不能将其中的单帧默认和 40 万点上限当作当前设置。

本轮在同一 Windows／Chromium D3D11 环境验证：

| 场景 | 原始点数 | 显示的非零真实点数 | 观测 FPS |
|---|---:|---:|---:|
| 2.pcap 默认 200 帧 | 4,000,000 | 2,723,736 | 60 |
| 2.pcap 整包 1,534 帧 | 30,675,400 | 21,654,649 | 61 |
| 用户新增 4.pcap 默认 200 帧 | 4,000,000 | 432,854 | 60 |
| 用户新增 4.pcap 整包 234 帧 | 4,677,800 | 526,615 | 60 |

2.pcap 在第 400 帧开始的密集滑动窗口回放，3 秒前进至第 430 帧，观测 60 FPS。整包最终测试的加载及截图取证合计约 26.8 秒；此前尝试该区间约 13–20 秒，该数字含截图等待，不作为纯解码耗时。加载期间每 25 帧渐进显示，可继续操作视角。零距离记录保留在原始帧里，显示时不绘制；没有用复制点或虚构表面增加密度。

相机使用正交投影，左键默认旋转且拖拽方向与画面跟手、上下俯仰受限避免翻转，右键平移，支持切换模式、鼠标位置缩放、缩放按钮、双击定位、主体聚焦、全景和 F/R/1/2/3 快捷键。自动主体范围来自代表样本的 5%–95% 坐标区间，仅控制取景，未删除远处点。

验证命令：`npx playwright test --project density`、`node scripts/dense-latest.mjs`。覆盖默认无抽样、全部 3,067 万原始点加载、密集滚动回放、相机操作、文件切换、尾帧及返回较小窗口；最终没有浏览器页面错误或资源错误。TypeScript／Vite 构建通过。一次早期切换测试发现长文件末尾帧号被短暂用于短文件请求，已增加索引所属文件校验，修复后重跑通过。

证据：`test-results/density-metrics.json`、`test-results/dense-file4-metrics.json`、`test-results/dense-default.png`、`test-results/dense-full-cloud.png`、`test-results/dense-file4-cloud.png`。当前代码哈希记录在 `test-results/source-manifest-v0.2.json`。

用户新增 4.pcap 的实际整包点云：

![4.pcap 整包真实点云](test-results/dense-file4-cloud.png)

## v0.1 历史记录

日期：2026-09-09。结果仅适用于本机离线 WebUI；不代表运动检测效果或 Jetson 实时性能。

## 环境与输入

- Windows 11，10.0.26200；AMD Ryzen AI 9 H 365，AMD Radeon 880M。
- Python 3.12.10／NumPy 2.5.3；Node.js 24.14.0；React 19／Three.js 0.180／Vite 7，精确依赖见锁文件。
- Chromium 153（Playwright，D3D11），交互测试视口 1440×960，单帧默认约 2 万原始点。
- ROS2 参照环境：本机 WSL2 Ubuntu-22.04／ROS2 Humble x86_64，使用当前源码重新构建；独立 Domain 174／UDP 12368／CycloneDDS。

| 输入 | 文件字节数 | SHA-256 |
|---|---:|---|
| 1.pcap | 97,141,988 | b17d7c17926e670b3d89ba0774c4140feb810e4935971ab57760f4bdd00636cd |
| 2.pcap | 225,157,460 | 39899e0fb25c98a6dd2f2e5a1831b5256904fc23ae730ef9166329be845f8db7 |

ROS2 驱动源码 SHA-256：`bccb2509ef17858aafd61e48bc563b0f955c90079b79394e5aa4026eec122756`。

## 验收结果

| 项目 | 1.pcap | 2.pcap |
|---|---:|---:|
| 有效 UDP 包 | 66,173 | 153,377 |
| 异常包 | 0 | 0 |
| 帧数 | 662 | 1,534 |
| 录制时长 | 66.174 s | 153.382 s |
| 最后不完整帧点数 | 14,600 | 15,400 |
| 全部解码点数 | 13,234,600 | 30,675,400 |
| 本地索引耗时（无上传） | 0.342 s | 0.914 s |
| 全帧解码耗时（不含渲染） | 0.735 s | 2.296 s |
| P95 单帧解码 | 1.73 ms | 2.44 ms |
| 浏览器导入至首帧（最终界面复测） | 2.49 s | 5.14 s |
| 1× 播放 5 秒前进帧数 | 50 | 51 |
| 浏览器完整 1× 回放耗时 | 66.71 s | 153.92 s |
| 完整回放末尾 FPS | 60 | 60 |

完整解码基准进程的最大观测工作集分别约 34.1 MiB／42.6 MiB。两文件导入后的服务进程另测约 71.0 MiB；不能将独立解码进程内存当作完整系统内存。浏览器最终交互复测的 JS 堆观测约 21.7 MB，Chromium 的该指标为近似值，**不包含全部 GPU 内存或浏览器进程内存**。

### 解码与 ROS2 对齐

对每个真实 PCAP 的前 12 个完整包组，各对比 240,000 点，共 480,000 点：

1. 实际启动当前 C++ 驱动与合帧节点，通过 UDP 回放原始载荷。
2. 接收真实 `/web_compare/cloud` PointCloud2，按字段名称和 offset 读取，校验 frame_id、点数、点顺序及反射率。
3. 将收到的原始点云保存为 NPY，再用 **Windows Python 3.12／NumPy 2.5.3 解码器**独立比较。

最大绝对坐标分量误差：1.pcap 为 **0.03052 mm**，2.pcap 为 **0.12207 mm**，均小于 1 mm。反射率逐点完全一致。随机跳转／继承出厂偏移另由自动测试覆盖。此项为 24 帧抽样数值对齐，不宣称全片每一帧均经过 ROS2 对比。

最终取证运行收到全部目标帧，重发次数为 0。早期尝试曾因验证脚本 Humble API 不兼容失败；修正后，又遇到 DDS BEST_EFFORT 分片丢帧。验证脚本现在按设备时间匹配目标帧，必要时重发同一包组并记录次数。旧驱动／合帧失败日志保留为 `ros2-first-attempt-*.log`、`ros2-second-attempt-*.log`；不把丢失样本计入数值通过结果。产品使用直接文件解码，不依赖这条 DDS 测试链路。

### 自动检查

- **15 项 Python 测试通过**：四种经典 PCAP 格式、空文件／错误格式／无目标端口、损坏文件、不完整尾帧、设备时间异常、时间倒退、取消索引、出厂偏移、真实数据随机读取、API 二进制帧、分页、删除和重启恢复。
- **2 项浏览器交互测试通过**：真实文件导入、着色、距离过滤、点大小／透明度、叠加、视角、快速跳转、刷新恢复、错误提示和移除。
- **3 项完整回放／取消测试通过**：两文件各完整 1× 播放、结束后 2× 重新播放、鼠标旋转缩放、上传仍在传输时取消。
- TypeScript 检查及生产构建通过。Vite 对包含 Three.js 的主包给出大小提示（约 718 kB，gzip 195 kB）；测试中不影响本机回放目标。Python 测试有测试客户端依赖弃用提示，未产生测试失败。
- `start.ps1 -SkipBuild -NoBrowser -Port 8766` 启动和健康检查通过，临时测试服务已停止；8765 主服务继续运行。

## 复现与证据

```powershell
cd D:\Code\radar\web
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe tests\benchmark.py
wsl -d Ubuntu-22.04 -- bash /mnt/d/Code/radar/web/tests/ros2_compare.sh
.\.venv\Scripts\python.exe tests\compare_windows.py
cd frontend
npm run build
# 8765 服务保持运行
npm run test:e2e
```

结构化证据位于 `test-results/`：

- `benchmark.json`：两文件全部帧的解码耗时、内存、输入／输出哈希。
- `browser-metrics.json`、`playwright.json`：最终交互复测；完整回放结果分别保留在 `full-playback-1.pcap.json` 与 `full-playback-2.pcap.json`。Playwright 汇总会被下一次运行替换。
- `ros2_comparison.json`、`windows_ros2_comparison.json`：实际 ROS2 与 Windows 解码对齐。
- `1.pcap.ros2-first12.npy`、`2.pcap.ros2-first12.npy`：抽样 ROS2 原始点云。
- `source-manifest.json`：本轮交付代码文件 SHA-256。

以下为真实 `2.pcap` 第 81 帧附近的显示效果：最近 20 帧叠加、0–100 m 显示过滤。用于清楚展示近场点集；没有改变解码坐标或原始数据。默认导入仍为完整距离范围、单帧。

![真实 PCAP 三维点云](test-results/webui-nearfield.png)

## 已知范围

当前仅经典 Ethernet PCAP、NA70 目标端口 2368、正常安装配置。pcapng、IP 分片、其他雷达协议需要后续扩展。点云叠加不包含运动补偿、网格重建或 SLAM；目标分类与实时连接尚未实现。显示预算与距离过滤只影响画面，不影响原始帧接口。

## 2026-09-10 四点带修订验证

命令（Windows 本机，环境 `web/.venv`，Node 24.14.0，无 WSL2／ROS2／CUDA 参与）：

```powershell
cd web
.\.venv\Scripts\python.exe -m pytest tests -q          # 33 passed
cd frontend
npm run build                                          # tsc + vite 通过
npx playwright install chromium
# 8765 服务运行中
npx playwright test --project=motion                   # 2 passed
cd ../..
web\.venv\Scripts\python.exe tools\analyze_ray_motion.py 2\003.pcap --outdir 2\analysis_003_bands
```

`003.pcap`（SHA-256 `025d4736159c9074a8903a29d17db4f5ace28dd53ea906e0e53ebcece3ceb343`）实测：

| 指标 | 旧方法（`na70-ray-history-v2`） | 本版（`na70-ray-history-v3`） |
|---|---|---|
| 稳定射线 | 111,600 | 111,600（未变） |
| 候选变化点 | 65,691 | 65,691（未变） |
| 移动点 | 41,897 | 666 |
| 运动帧 | 291 | 84 |
| 运动时段 | 2 | 4 |
| 轨迹／点带 | 按窗口聚类片段统计，未按点带口径评价 | 5 条四维时空点带 |

5 条点带的持续时间 1.20–1.80 s、起终点位移 1.08–1.96 m、速度 0.83–1.21 m/s、垂直于运动方向的截面 0.57–1.01 m；4 个运动时段为 2.70–5.60 s、13.50–14.90 s、21.00–22.70 s、36.60–38.60 s，其中 36.6–38.6 s 与旧方法独立给出的 37.10–39.60 s 区间吻合。`4.pcap` 全片只有 1 个候选点、0 条点带，属于无运动场景。

`SYNTHETIC`：`tests/test_bands.py` 用生成数据覆盖静态背景、单个移动目标、拉长点带与人体尺寸分离、静止前景、扫描方向变化无目标、双目标交叉、短暂丢失合并、孤立噪点、阈值非法，共 12 项，全部通过。这些只证明逻辑正确，不是效果证据。

**未验证**：没有逐点真值，也没有与录屏逐帧对齐的标注，因此点带数量、召回率与漏检率都不能作为结论；阈值是按"步速 0.35–4 m/s、持续时间≥1.2 s、直度≥0.45"的物理先验设定的默认值，未经真实人群数据调参。W2 门禁保持 `PARTIAL`。

## 2026-09-10 旋转视角缺陷修复

现象：水平拖动旋转到一定角度后不再响应，随后视角随机乱转。

根因：`OrbitControls` 在构造函数里用 `object.up` 计算一次并**缓存**"以 +Y 为极轴"的四元数（`three@0.180.0` `OrbitControls.js:404`），此后不再重算。`CloudView` 原先在创建控制器**之后**才把 `camera.up` 设为 `(0,0,1)`，于是轨道数学仍以 Y 为极轴、渲染却以 Z 为上，`minPolarAngle = .001` 把相机夹到了错误的极点上；极点附近方位角病态，表现为水平拖动失效并跳变。

修复：把 `camera.up.set(0, 0, 1)` 移到 `new OrbitControls(...)` **之前**；极角限制改为相对 +Z 的 `.25`（距天顶 14°）到 `Math.PI / 2 + .02`；俯视预设方向由 `[0, -.001, 1]` 改为 `[0, -.35, 1]`，避免正好落在极点上。底栏新增方位角／俯仰角读数。

验证：新增回归测试 `tests/viewer.spec.ts` 的 `horizontal orbit keeps responding and never collapses onto the pole` —— 连续 6 次水平拖动采样方位角，要求至少 5 次产生大于 5° 的变化，并断言俯仰角全程落在 10°–95°。

- 修复后：`1 passed`。
- 把代码临时还原为缺陷版本后重跑：**失败**，6 次拖动只有 2 次改变了视角（`Expected: >= 5, Received: 2`），与用户描述一致。测试确实能抓到该缺陷，不是装饰性用例。
# 2026-09-11 建图阶段（mapping job）验证记录

## 2026-09-15 固定点转台现有 PCAP 开发版

新增 `backend.turntable` 与 `backend.turntable_jobs`，在现有解码器和 `VoxelMap` 上实现多捕获固定点转台建图。角度日志、标定、时间段重叠/越界、NaN/Inf、正负角度、偏心旋转、转动段拒绝、质量门禁、受约束微调留出拒绝、地图导出和服务重启读取均有测试。

正式运行：

```powershell
cd D:\Code\radar
.\web\.venv\Scripts\python.exe tools\build_turntable_map.py --pcap 2\9.14_caochangzhong2.pcap --pcap 2\9.14_caochangzuo.pcap --pcap 2\9.14_caochangyou.pcap --angle-log web\backend\angle_log.20260914-development.csv --calibration web\backend\turntable_calibration.20260914-assumed.json --output Plan\evidence\turntable\20260915-development-final
cd web
.\.venv\Scripts\python.exe -m pytest tests -q
cd frontend
npm run build
npm run test:e2e -- --project=turntable
```

开发证据目录为 `../Plan/evidence/turntable/20260915-development-final/`，包含 `job.json`、`config.yaml`、`source_hashes.json`、`angle_log.csv`、`turntable_calibration.json`、`submap_quality.jsonl`、`transforms.json`、`validation.json`、`map_metadata.json`、`map.ply`、`map.pcd`、来源/基线预览、几何诊断和 `turntable-development-evidence.zip`。结果标签固定为 `DEVELOPMENT / DIAGNOSTIC / ESTIMATED_ANGLE`。

输入时长：中心 `129.9009 s`，左 `122.5718 s`，右 `121.0493 s`。最终静止段为中心 `0–129.9 s`、左 `0–20 s` 与 `113–122 s`、右 `0–20 s` 与 `82–120 s`；左右转动及未采用尾段由 `validation.json.excluded_rotation_time` 逐段记录。A 原始直累积 median/P95 `2.794/26.456 m`，B 静止未补偿 `8.035/25.732 m`，C 估计角度 `0.633/12.517 m`，D 与 C 相同，因为所有微调均被留出结构门禁拒绝。角度候选均带来源，约 `6.2°/10.1°` 仅作为未采用先验。

该运行不证明正式转台精度、完整操场覆盖、独立复采或 SLAM；编码器同步角度、轴偏心实测、安装 RPY、回零重复性和独立验收采集仍待未来补齐。

主线改为静态环境中移动雷达、无 IMU 的点云里程计与体素地图融合。移动物体检测退出主验收范围。**当前阶段不是完整 SLAM**（无回环、无位姿图、无全局优化）。

## 环境与输入

- Windows 11 10.0.26200 / AMD Ryzen AI 9 H 365，Python 3.12.10，NumPy 2.5.3，SciPy 1.17.1，Node 24.14.0，Chromium 153（Playwright）。
- 输入：`1.pcap` SHA-256 `b17d7c17926e670b3d89ba0774c4140feb810e4935971ab57760f4bdd00636cd`（662 帧，97.1 MB）；`4.pcap` SHA-256 `0b53026639d48223…`（234 帧，34.3 MB）。
- 配置版本 `71a362d157246524`（`backend/mapping.yaml` 内容哈希）；代码版本 `na70-lidar-odometry-v1+ros2-na70-16mm-v1`。

## 命令

```powershell
cd D:\Code\radar\web
.\.venv\Scripts\python.exe -m pytest tests -q          # 73 passed
cd frontend
npm run build                                          # tsc + vite 通过
# 8765 服务运行中（python -m backend.run）
npx playwright test --project=mapping                  # 1 passed, 1 skipped
```

## 自动检查

| 检查 | 结果 |
|---|---|
| 后端全部 pytest | **73 passed**（含新增 22 项算法 + 9 项建图接口；原有 42 项未回归） |
| TypeScript / Vite 生产构建 | 通过（主包 749.6 kB，gzip 206.2 kB） |
| Playwright `mapping` 界面级用例 | 通过：三种视图按钮、四种坐标系命名、失败帧时间轴、页面不宣称 SLAM |
| Playwright 真实数据建图用例 | 默认跳过，需 `NA70_MAPPING_E2E=1`（真实 234 帧任务需数分钟 CPU） |
| Playwright `motion` | 已跳过：移动检测入口按主线要求移出主流程，后端链路与 Python 测试保留 |

## 合成数据验证（`SYNTHETIC`，只证明数学与状态机，不是效果证据）

`web/tests/test_mapping.py`，22 项，全部通过：

- 合成刚体变换恢复：point-to-point 与 point-to-plane 均恢复平移 0.1526 m（真值 0.1526 m）与 2.5° 旋转，RMSE 8×10⁻¹⁶。
- 轨迹误差：9 帧缓慢平移+旋转序列，接受率 ≥90%，逐帧平移误差 <0.12 m、旋转误差 <1.5°。
- 拒绝门限：点数不足、NaN/Inf/零点过滤、平面退化、重叠不足、残差过大、运动跳变。
- 状态机：时间戳倒退/None/NaN → `timestamp_invalid`；连续失败 → `tracking_lost` 且不融合；重新初始化开启新轨迹段；空点云 → `insufficient_points`。
- 关键帧：仅在大位移/大转角处触发，且相邻关键帧间距 ≥ 阈值。
- 体素地图：同体素强度取均值、观测次数累加、最近时间与来源关键帧更新、体素上限只丢弃新体素、分块与导出（PLY 21 字节/体素、PCD 28 字节/体素）。
- 首尾闭合：环形路径闭合平移误差 <0.1 m；单帧时无闭合指标。

## 合成数据验证（接口层）

`web/tests/test_mapping_api.py`，9 项，全部通过：任务字段完整性与 `imu_used/loop_closure_used=false`、全部 11 个结果文件落盘、**服务重启后任务/轨迹/地图/分块仍可读**、中断任务重启后标记为 `error`、运行中进度上报与耗时/峰值内存、任务取消（`validation.json.claims.engineering_chain = CANCELLED` 且拒绝导出）、并发任务拒绝、**分块按 id/包围盒/limit 读取**、导出 zip 内容校验、配置覆盖随任务记录。

## 真实 PCAP 验证：**失败**（证据保留）

对 `1.pcap`（662 帧）与 `4.pcap`（234 帧）执行真实任务：

| 指标 | 1.pcap | 4.pcap |
|---|---:|---:|
| 接受帧 | 1（仅参考帧） | 1（仅参考帧） |
| 拒绝帧 | 2 | 2 |
| 跟踪失锁帧 | 659 | 231 |
| 地图点数 / 体素数 | 1579 / 1579 | 296 / 296 |
| 分块数 | 54 | 5 |
| 处理耗时 | 18.9 s | 3.2 s |
| 首尾平移/旋转误差 | 无（可用位姿不足） | 无 |

首帧之后的第一个待配准帧即 `degenerate_geometry / unobservable_direction`，其后累计失败进入 `tracking_lost`，重初始化持续失败。**地图只含参考帧，不存在可评价的累计地图，真实无重影验收未完成。**

逐帧量化（`1.pcap` 帧 0/1，`voxel_size 0.25 m`、`max_range 80 m`）：

| 量 | 数值 |
|---|---|
| 原始 → 过滤后点数 | 20000 → 1579 / 2080 |
| 帧内最近邻间距中位数 | 0.16–0.56 m |
| 帧间 0.6 m 内对应点 | 250 / 2080（12%） |
| point-to-point | overlap 0.202、RMSE 0.336 m、step 0.93 m（真值位移 0） |
| point-to-plane 条件数 | λmin/λmax = 0.0026 |
| 体素 × 距离 × 对应阈值扫描 | 最佳 overlap 0.620（cd=3.0 m）伴随 RMSE 1.48 m、step 1.5 m |

结论：NA70 连续 100 包帧不是同一表面的重复扫描，逐帧对局部子地图在当前帧结构下不可行；放宽对应距离只制造虚假重叠。此项与 `Plan/README.md` 既有结论一致。

失败证据保留在 `web/data/320b5387-e20c-484c-a3a7-1a25bf3baf10/mapping/` 与 `web/data/4c0d511d-2ca0-4060-9158-d8c6bf9b4010/mapping/`（`job.json`、`frame_quality.jsonl`、`validation.json`、`map.ply`、`map.pcd`、`export/mapping-evidence.zip`）。

## 明确未验证

- 真实静态环境、雷达缓慢移动、返回起点的**无拖影/无重影**验收。
- 墙面厚度、墙角双层、固定物体重复、首尾闭合误差的真实数据统计。
- 回环检测、位姿图优化、全局地图重建、重定位。
- 帧内时间处理与运动去畸变。
- Jetson Orin Nano 4GB / Humble 上的频率、延迟、内存与长时间运行。
- 阈值标定：`mapping.yaml` 当前数值是可运行基线，不是验收阈值。

## 下一项任务

旧的“最近关键帧、前 N 帧无条件累积”建议已被实现和修正，后续执行顺序统一见 `../Plan/MASTER_开发与验收总计划.md`。新的真实静态环境移动采集仍是 R7 的必要门禁。


# 2026-09-11 本机正确性修复与候选评估（执行中）

历史“无条件累积前 N 帧”的建议已由 Plan/10 覆盖，不再适用。现已修复融合坐标、相邻增量和时间预测、分块索引尺寸、MiB 重复换算、上传初期 404 后轮询停止。子地图使用有界已接受帧窗口，静止初始化需要明确声明。

后端完整测试阶段结果：84 passed / 2 warnings；随后恢复段坐标用例和落盘故障/取消竞争定向测试通过。前端构建通过；浏览器 mapping 2 passed / 1 skipped，成功用例实际创建合成任务、请求地图坐标点云、切换 MAP 并下载 ZIP。默认真实上传用例仍跳过；真实数据通过独立任务复跑，不能把二者混称浏览器真实验收通过。

006.pcap 经用户最新确认从头到尾没有移动，是纯静止基线，不能作为 R7 移动验收数据。此前按“前 60 秒静止、后续移动”拆分的统计已作废；1.pcap/4.pcap 仍仅参考帧融合。所有任务与导出索引见 `../Plan/evidence/mapping-repair/20260911-repair/comparison.json`。

追加使用前 300 帧（约 30 秒）作为静止初始化的生产复跑：初始化 300 帧、正式配准 0 帧、失锁 1249 帧；第 30.201 s 首次质量失败。该结果只说明静止重复扫描仍存在帧间对应问题，不能评价移动段。扩大初始化窗口只改善静止参考覆盖，未解决后续帧间运动配准；R7 需要新的真实手动移动 PCAP。

按用户“继续”扩展到成熟无 IMU 候选评估，独立环境 KISS-ICP 1.3.0 的默认启动与 30 帧初始化均存在静止姿态漂移，没有接入生产。GLIM CT CPU 在本机 WSL2/Humble 评估中；仅为本机实验，不是 Jetson 验收。Jetson 由用户明确延期。


## 2026-09-12 采集预检工作台

采集模式、检查项与路线记录自动保存；上传诊断和任务快照写入证据。预检与任务完成均不等于真实定位通过。104 项后端测试通过，构建通过，mapping 浏览器 2 passed / 1 skipped（真实用例未启用）。本轮测试服务使用 localhost:8767；原 8765 后端尚未重启。P3 几何评价仍缺真实数据、ROI 与冻结容差。

详细命令、文件与限制见 [交付证据](../Plan/evidence/mapping-repair/20260911-repair/preflight-delivery-20260912.md)。R7 等待真实移动采集，R8 延后。


P3 几何准备补充：`tools/measure_mapping_geometry.py` 读取现有任务 map_chunks，按用户给定的固定结构 ROI、平面法向、坐标段和冻结标准测量 P95–P05 厚度及双层峰候选，保存合同与地图分块 SHA-256。不同段分开测量，候选不等于固定物体语义真值或 R7 PASS。未提供真实标注合同，真实几何评价仍未运行。

## 2026-09-12 Windows PowerShell 启动兼容修复

start.ps1 的 Python 版本探测改用 chr(46) 分隔主次版本，避免 Windows PowerShell 5.1 向原生程序传参时丢失内嵌双引号。通过 powershell.exe -NoProfile -EncodedCommand 执行脚本中的实际探测行，在 Windows PowerShell 5.1.26100.9444、Python 3.12.10 下返回 3.12，退出码 0。本次仅验证版本检查，未重新启动 Web 服务。

## 2026-09-12 全帧回放入口与组件收纳

恢复 WebUI 的全帧回放主入口：时间轴显示完整 PCAP 帧范围，支持回到第 1 帧、上一帧、播放全部帧、下一帧和倍速播放；失败帧状态条改为可展开的辅助信息。显示参数与回放设置保持默认展开，配准质量、轨迹/失败帧、地图结果和任务详细证据改为可收纳区块。`npm.cmd run build`（TypeScript 5.9.3、Vite 7.3.6）通过。

## 2026-09-14 操场新数据开发前基线

输入为 `9.14_caochangzuo/zhong2/you/yidong.pcap`。本轮只运行正式解码、预检统计和当前未修改生产配准链，没有调参、代码变更或 R7 验收。四份文件约 10 帧/s、`invalid_packets=0`；右侧文件第 79 帧检测到一次设备包时间回退。详细哈希、点数、距离与照片限制见 [数据分析证据](../Plan/evidence/mapping-repair/20260914-playground/dataset-analysis.md)。

当前 `mapping.yaml` 单帧 point-to-plane 基线：四份数据的正式配准成功帧均为 0；移动/左/右/中分别有 939/1216/1207/1296 个失锁帧，轨迹均为 0 m。中心数据的 1298 帧全部呈平面退化，30 帧声明式静止窗口仍初始化失败；移动数据前约 4.10 s 预处理为空，之后绝大多数帧仍为平面退化，抽样帧无 10 m 内点。

结论：R7 不再因“没有新数据”而阻塞，但当前基线失败，门禁保持 `IN_PROGRESS`。本轮结果只证明固定 100 包成帧、当前场景可观测性和现有配准链不匹配；不证明数据永久不可用，也不允许通过放宽门限宣称成功。完整 SLAM 与实时设备链均未运行。

## 2026-09-14 覆盖驱动 Sweep 与 SE(2) 候选验证

新增 `backend.sweeps`，保留原 100 包 WebUI 帧兼容接口，同时提供 PCAP/逐包流式共用的包级 sweep 组装。阈值集中在 `mapping.yaml`；只保留包时间，不伪造点级时间。新增 `backend.registration_se2`，以信息矩阵特征分解输出六自由度可观测评分，并用 TSVD 冻结弱方向；候选包括 point-to-plane、robust GICP 和 multi-resolution NDT。论文与 GitHub 来源记录在 `THIRD_PARTY_NOTICES.md`，实现未复制参考仓库源码。

四份 PCAP 覆盖驱动 sweep 的 0.3 m 相邻覆盖中位数为：中 0.591、左 0.570、右 0.585、移动 0.465；固定 100 包对应值仅为 0.255、0.224、0.219、0.213。四份真实输入以变化分块大小模拟流式供包，sweep 边界 SHA-256 均与一次性输入一致。右侧 device time `26.766→26.512 s` 被标记但不参与时长；移动文件 source index 76101 的完全重复记录被标记并去重。G1 `PASS`。

G2 合成回归覆盖纯平面、地面加立柱、旋转、平移、组合运动、错误对应和空输入。候选显式选择局部法向可约束 x/y/yaw 的非地面结构；GICP 使用 0.8/0.4/0.25 m 三层，NDT 使用 2.0/1.0 m 两层。真实数据每份均匀抽 12 对相邻 sweep 时，左转三种候选 yaw 中位数为正、右转为负，但正式接受仍为左 0/12、右 1/12、中心 0/12、移动 0/12。全量相邻复跑进一步显示：左 243 对三种方法均 0 对接受，右 230 对均仅 3 对接受；左候选正向比例 49%–52%，右候选正向比例 37%–49%。扩大到 10 个 sweep 跨度仍无改善。未通过候选不输出也不融合。G2 保持 `IN_PROGRESS（真实回归 FAIL）`，R7 不进入生产复跑。

## 2026-09-14 已观测覆盖地图

按用户要求，先分析并过滤相对地面超过 4.5 m 的高点集，再对 zhong2/zuo/you 生成一张当前 PCAP 可观测范围的诊断地图。高度分析显示：zhong2 排除 9 点（0.0002%）、zuo 排除 582,867 点（13.50%）、you 排除 546,286 点（14.51%）。输出体素地图 182,340 个体素，角域覆盖约 126.9°。

地图文件位于 `../Plan/evidence/mapping-repair/20260914-observed-map/`。跨文件时变姿态检查仍失败（zuo yaw spread 13.6°、you 30.0°），所以这张图是“已观测覆盖地图”，不是完整操场地图或 R7/G4 通过证据。

命令与逐 sweep/逐配准对证据见 `../Plan/evidence/mapping-repair/20260914-sweeps/development-report.md`、`../Plan/evidence/mapping-repair/20260914-sweeps/*.json`、`../Plan/evidence/mapping-repair/20260914-se2/*.json`、`../Plan/evidence/mapping-repair/20260914-se2-full/*.json` 和 `../Plan/evidence/mapping-repair/20260914-se2-stride10/*.json`。

## 2026-09-16 WebUI 工具箱、原点测距与网格尺度

新增互斥式查看器工具状态，首批包含浏览和原点测距。测距从当前已加载真实点云的代表样本中拾取点击处附近的点，输出该点到当前坐标原点的三维距离、XY 水平距离、ΔZ 与 XYZ 坐标；RAW、ODOMETRY、MAP 分别将原点标注为雷达原点、首帧原点和全局地图原点。三维场景以实线、分量虚线和端点标记同步显示结果。测距模式使用 15×15 px 细十字准星，中心热点与点击坐标一致，退出测距后恢复浏览光标。网格间距支持 1/2/5/10/20/50 m，且页面明确声明网格不是重建地面。

验证环境为 Windows 本机、Node.js/Vite 当前锁定依赖、Chromium。`npm run build` 通过；`npx playwright test tests/viewer.spec.ts --project=viewer` 为 4 passed（含真实 PCAP 导入/回放、非法输入、合成测距与网格切换、水平环绕极点回归）。测距浏览器证据为 `test-results/webui-toolbox-measurement.png`。本轮只验证 WebUI 交互与坐标计算，没有运行或改变点云里程计算法，也不构成 R7 或 Jetson 性能验收。

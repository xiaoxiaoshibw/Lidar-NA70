# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

2026-10-30 标定与场景工具链：新增 `packages/na70_core/na70_core/calibration/`（静置/Allan、六面、平面测距、`R_IL`＋时间偏移、外参候选合成、陀螺单位检查）、`na70_core/scene.py`（位姿插值、逐点变换、体素地图、PLY）、`na70_core/lio_scans.py`（包时 LIO 扫描包、源引用、导出阻塞项）与 `na70_core/map_quality.py`（地图一致性/平面/控制点评估）及多个 CLI；SYNTHETIC 证据在 `Plan/evidence/slam/20261030-*`，用法见 `docs/NA70_CALIBRATION_WORKFLOW.md`。同日补充一键批采闭环：`开始外参采集.bat`/`开始全量标定采集.bat`（STATIC 120 s＋CAL-ROT 80 s＋六面各 25 s，`na70-calibration-batch-v1` 批次清单）＋本地批处理 `tools/report_na70_calibration_batch.py`、`tools/solve_na70_six_face_sessions.py`；采集脚本遇服务端 `failed` 终态立即报错不挂死；SYNTHETIC 测试覆盖批处理与平面法向 `calibrate()` 端到端（工作流文档 0.1/4.1/4.2 节）。近距下限审计（四段 PCAP 一致）：**有效回波最近 7.488 m（距离码 468），码 1–467 从不出现**，属设备/数据侧下限而非解码器或工具过滤；外参 ROT 墙距因此由 2–5 m 改为 **≥8 m（建议 9–15 m）**，见 `Plan/evidence/slam/20261030-near-range-floor/REPORT.md`。**仍未完成**：B1–B5 现场数据、真实时间同步、真实外参与真实操场场景、Jetson 验收；未生成 `calibration_frozen.yaml`。

2026-10-03 高压线分支更新：新增 `packages/na70_core/na70_core/windows.py`（增量因果包级时间窗，与 thin_wire.select_window byte-compatible）与 `packages/na70_core/na70_core/wire/`（手动 ROI 线段提取 pipeline）；离线 W3 运行器 `tools/run_wire_w3_offline.py`；执行计划 `Plan/高压线识别执行计划.md`。**未实现**：`/api/wire/w3` REST 持久化作业、WebUI 高压线真值录入、P3 独立实测 200 m 数据、P4 全 FOV frozen 评分。依赖已加 `pyransac3d==0.7.0`。真实 200 m 高压线归属仍 `NOT_EVALUATED`。足球门主线状态没变。

2026-09-26 WebUI 更新：前端已有第四个 `/wire/` 导线原始回波工作台，后端 `/api/wire` 提供持久化 W1 诊断运行、逐窗/逐包数据与有界原始点引用。W1 真实导线回波归属仍为 `NOT_EVALUATED`，W3/W4/W5 模式尚未实现；本项 WebUI 改造不自动更改下述足球门主线和 Plan/README.md 的路线记录。启动新代码任务前先读取最新 Plan/README.md。

## 项目概况

NA70 双楔镜激光雷达感知系统。当前主线（2026-09-21 路线决策）是**足球门识别**：先把真实足球门识别成功，用它验证从原始点云、预处理、检测、评价到跟踪和 WebUI 的完整感知链。唯一执行入口是 `Plan/足球门识别主线开发计划.md`，顺序固定 **G0 可信真值 → G1 地面相对高度基准 → G2 人工 ROI 内含网面几何 → G3 全场候选搜索 → G4 跟踪 → G5 冻结验收**，第一轮只执行 G0–G1。当前状态以 `Plan/README.md` 为准（决策记录已更新至 2026-09-24）：G0 门禁于 2026-09-23 重新打开（真值更正：`(92.7,+29.1)` 结构也是球门；唯一未完成项是补充 reviewed 背景窗口），G1 退出证据已备、待 G0 补齐后复核，G2–G5 未通过。`Plan/README.md` 记录全部路线决策，动手前先读它。

历史/暂停路线（代码与证据保留，不进入当前开发顺序）：无 IMU 点云里程计 + 体素地图融合（`Plan/MASTER_开发与验收总计划.md`，现为失败证据，真实数据配准成功帧为 0）；细线 W1（`Plan/细线回波验证与逐级开发计划.md`，PAUSED，真实回波归属 NOT_EVALUATED）；固定点转台拼接（`Plan/固定点转台操场建图计划.md`）；移动物体检测（`web/backend/motion.py`、`ROS2/src/na70_motion_detector/`，后端链路保留、入口移出）。候选路线（未入主线）：`Plan/无人机单根高压线识别场景与可行性计划.md`（2026-09-24 新增，S0 合成审计已实施、S1 真实回波 NOT_EVALUATED，README 尚未收录）、`Plan/新PCAP_无IMU移动建图采集方案.md`。

部署目标：Jetson Orin Nano 4GB + ROS2 Humble（最终性能验收）；Windows 本机负责开发、离线 PCAP 处理与 WebUI 回放；WSL2/ROS2 仅作集成验证，不是当前前置条件。

## 常用命令

需要 Python 3.12（start.ps1 强制校验）、Node.js 22.12+。shell 是 bash（Git Bash on Windows），但项目脚本按 PowerShell/Windows 约定运行。

### WebUI（`web/`，以下命令均在 `web/` 目录下运行）

```powershell
# 一键启动（建 venv、装锁依赖、构建前端、起服务、开浏览器，默认 127.0.0.1:8765）
.\start.ps1                 # 参数：-SkipBuild / -Port / -Python / -NoBrowser；或直接运行仓库根目录 启动WebUI.bat

# 后端测试（decoder/API/mapping/goal_detection/motion/preflight 等）
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m pytest tests\test_goal_accumulation.py -q  # 单文件
.\.venv\Scripts\python.exe -m pytest tests\test_mapping.py::test_synthetic_rigid_transform_is_recovered -q  # 单用例（测试均为模块级函数，无测试类）
.\.venv\Scripts\python.exe tests\benchmark.py                        # 解码吞吐量/内存峰值基准（写 web/test-results/benchmark.json）

# 后端单独跑（已装依赖时；--port 默认 8765、--no-browser）
.\.venv\Scripts\python.exe -m backend.run
```

### 球门离线工具（在仓库根目录，用 web venv 运行）

```powershell
# 全片离线运行：逐帧检测 JSONL + summary 落盘 Plan/evidence/goal-demo/<run>/
.\web\.venv\Scripts\python.exe tools\run_goal_demo_offline.py --pcap radar_capture_data\<路径>.pcap [--capture-id <id>] [--run <名>] [--max-frames N] [--no-roi] [--strategy staged|poles|facade]

# 标注对账评分：命中率/precision/recall、首个命中帧、中心误差、误检清单，产物写 <run>-validation/
.\web\.venv\Scripts\python.exe tools\score_goal_detection.py --run <run目录> --annotations <标注.jsonl> [--capture <id>]
```

旧标注行（无 capture_id）评分时必须显式 `--capture` 指定归属；`--annotations` 可重复，缺省扫描 `Plan/evidence/goal-demo/annotations{,-reviewed}` 与 `web/data/annotations`；评分默认配置 `tools/goal_eval_config.yaml`（可用 `--hit-radius`/`--min-confidence` 覆盖）。`--no-roi` 忽略 `goal_detection.yaml` 的 `webui.demo_roi` 做全图运行（与 `--roi` 互斥）；`--pcap` 可重复以顺序跑多个 PCAP。

### 前端（`web/frontend/`）

```powershell
npm run dev          # Vite dev server（绑定 127.0.0.1），/api 代理到 8765
npm run build        # tsc -b && vite build（类型检查 + 生产构建）
npx playwright install chromium   # 首次
npm run test:e2e     # 需要 8765 后端已在运行；产物在 web/test-results/
```

E2E 用例在 `web/frontend/tests/`，需要 `radar_capture_data/1.pcap`、`2.pcap`。

### ROS2（`ROS2/`，WSL2 Ubuntu 22.04 + Humble，仅用于验证）

```bash
source /opt/ros/humble/setup.bash
colcon build --symlink-install && source install/setup.bash
ros2 launch na70_motion_detector motion_detector.launch.py
```

`na70_motion_detector` 内单测（在包目录 `ROS2/src/na70_motion_detector/` 下运行）：`python -m unittest discover -s test`。

## 架构

### web/（Windows 本机 WebUI，独立可运行）

- `backend/decoder.py`：NA70 PCAP 解码。协议唯一参考是 `ROS2/src/double_wedge_driver/src/inno_device_lidar_node.cpp` 与合帧节点 `test_lidar_driver.cpp`。关键事实：1410 字节载荷 = 200 点（每点 7 字节）+ 10 字节尾部（8 字节设备时间 + 2 字节出厂状态）；距离码 ×16 mm、俯仰 ×0.0258°、航向 ×0.0432°；UDP 端口 2368；每 100 个有效包组成一帧（~10 Hz、20000 射线/帧）；初始轴映射按 `install_direction` 取 `(z, -x, -y)`（正向安装）或 `(z, x, y)` + Rx(180°)，再按 `Ry(pitch_offset) × Rz(yaw_offset)` 旋转。
- `backend/goal_detection/`：球门检测/跟踪算法核心（当前主线），仅依赖 numpy/scipy、自身不做 I/O。`pipeline.py` 组装累积器（`accumulation.py`/`buffer.py`）、检测器（`detector.py`）、跟踪器（`tracker.py`，SEARCHING→CANDIDATE→LOCKING→LOCKED→TRACKING→LOST→REACQUIRE 状态机）；三种策略：`staged`（BEV 纯 XY 提议 → 锁定后 FOV 收缩 → FOV 内 z 分析修高度，`bev.py`/`focus.py`/`ground.py`）、`poles`（单帧与叠加云各自全图检测）、`facade`（人工 ROI 内立面测量，G2 实现，`facade.py`）。`observability.py` 记录逐帧 Stage 结果与点可见性掩码；`synthetic.py` 是带真值的确定性合成夹具（改阈值须同步）；`window_annotation.py` 校验窗口真值标注。分层固定：算法核心 / `demo_api.py` / 前端 `DemoApp.tsx`，阈值唯一来源 `backend/goal_detection.yaml`。
- `backend/demo_api.py`：`/api/demo/*` 路由——回放会话（最多 2 个并发；`PUT .../config` 白名单校验后覆盖配置并重算 config_version）、accumulated cloud、stage/溯源/快照等 debug 端点、标注工作台端点（`annotate/cloud|timeline|analyze`）、标注读写（`/api/demo/annotations`，存 `web/data/annotations/`）。标注与评分数据契约见 `docs/GOAL_DEBUG_CONTRACT.md`（标注 v1 / 运行行 v1（兼容 goal-run-v1.1）/ eval-v1），当前架构详见 `docs/GOAL_TRACKING_CURRENT_ARCHITECTURE.md`。
- `backend/mapping.py` + `mapping_jobs.py`：无 IMU 点云里程计 + 体素地图融合的完整实现，经 `POST /api/mapping/jobs` 使用，已非主线，保留用于回放/验收视图与失败证据。任务结果全部落盘 `web/data/<uuid>/mapping/<job_id>/`，服务重启后直接读取不重算；`registration.py` 已被取代，仅供历史合成测试；`registration_se2.py` 是独立的 SE(2) 配准候选实现（配合 `cross_capture.py` 做跨捕获验证）。
- `backend/mapping.yaml` / `goal_detection.yaml`：阈值集中于此。**尽管扩展名是 .yaml，内容是 JSON，用 `json.loads` 解析——不能写注释或 YAML 专有语法**；`config_version` 是配置内容 sha256 前 16 位，随会话/任务/标注记录。**这些数值是可运行基线，不是验收阈值。**
- 前端四个页面（Vite 多入口，React 19 + Three.js + lucide-react）：`index.html` 主回放/验收，`demo.html`（DemoApp：球门 Demo、实时标注对比、跟随镜头），`annotate.html`（AnnotateApp 标注工作台），`wire.html`（W1 原始回波工作台）。无路由库、仅 HTTP 轮询、无 WebSocket。
- 其他保留模块：`sweeps.py`（逐 sweep 组装，里程计生产扫描模型）、`turntable.py`+`turntable.yaml`+`turntable_jobs.py`（转台路线及持久化任务，`POST /api/mapping/turntable/jobs`）、`thin_wire.py`+`thin_wire_plot.py`+`wire_scene.py`（细线 W1 证据）、`motion.py`+`bands.py`+`spatial.py`（历史运动分析：射线历史、距离众数背景、4D 体素化加权 DBSCAN、点带判别；点带数 ≠ 人数，点带长度 ≠ 人体尺寸）、`preflight.py`+`preflight.yaml`（采集预检工作台）、`capture_validation.py`/`geometry_validation.py`/`temporal_yaw.py`（证据与校验）。
- `backend/app.py`：FastAPI 入口，**只绑定 127.0.0.1**（单用户本地服务，无认证）；独立启动入口是 `backend/run.py`。帧二进制格式 v1 见 `web/README.md`。
- 缓存目录 `web/data/<uuid>/`（可由 `NA70_DATA_DIR` 重定向）仅由本应用管理，已导入的捕获视为不可变。`radar_capture_data/` 存放录制，PCAP 不入 Git。

### ROS2/

- `double_wedge_driver/`：C++17 NA70 驱动、消息、launch、RViz 配置——是解码协议的权威参考（另有毫米波串口节点 `inno_device_radar_node.cpp`）。
- `na70_motion_detector/`：Python 算法核心（`motion_core.py`，仅 numpy + 标准库）与 ROS2 封装（`motion_detector_node.py`）分离，核心可离线单测。
- 工作区附属：`ROS2/config/`（CycloneDDS/QoS 配置）、`ROS2/scripts/`（DDS/E2E 探针脚本）。

### Plan/ 与证据纪律

- 先读 `Plan/README.md`（路线决策记录）。当前执行入口：`足球门识别主线开发计划.md`。根因细节与历史实验矩阵：`GOAL_P3_真实球门检测修复执行计划.md`、`GOAL_P3_下一窗口交接提示词.md`；历史分支安排：`球门跟踪下一阶段完整开发计划.md`；历史失败证据：`MASTER_开发与验收总计划.md`；历史手册：`当前开发执行手册.md`。均不是当前开发顺序，冲突时以足球门主线计划为准。
- `05_开发执行规范.md` 对所有工作生效：保留无关的工作区改动、阈值进 YAML 不硬编码、证据诚实标记（SYNTHETIC/WSL2、失败事实保留）。另有既定探针纪律：涉及真值的判据改动先用探针实测再写码，探针用后删。
- 门禁验收证据存 `Plan/evidence/`（球门线在 `evidence/goal-demo/`，含根因审计报告）；测试失败必须保留失败事实，不得改写描述把失败变成通过；合成数据结果显式标记 `SYNTHETIC`；WSL2 结果标记 `WSL2_HUMBLE_X86_64`，不能替代 Jetson/ARM 证据。
- 严格区分「已运行」「已输出」「指标达标」「阶段通过」。球门「识别成功」分四层：局部几何 → 全场检测 → 跟踪 → 冻结参数真实验收；页面出现框、候选数或 TRACKING 帧数增加都不等于成功。当前真实基线：staged/poles 与移动录制（944 帧）全片无 LOCKED/TRACKING、待复核标注 0/11；2026-09-23 facade 全片重跑曾出现 LOCKED 1 / TRACKING 854，但身份未核实（NOT_EVALUATED），不作为基线变化。不得通过降低锁定阈值、扩大默认框或增加跟踪预测来绕过检测失败。

## 代码与提交约定

- Python 四空格、snake_case；TS 两空格、camelCase；C++ 随周边风格。无 formatter/linter 配置。
- 所有检测/配准阈值进 YAML，不硬编码；PointCloud2 字段按名称和 datatype 查找，不假定固定 offset；保留 header stamp、frame_id、intensity 与点索引对应。
- 空输入、NaN/Inf、乱序时间、参数非法必须有明确处理。
- 提交信息用简洁中文动作总结（如「实现十帧点云缓存与叠加」「更新球门叠加全片验收证据」）。
- PCAP、缓存、构建产物不入库；保留 `web/requirements.lock` 与 `web/frontend/package-lock.json`。
- 根目录 `AGENTS.md` 是内容重叠的英文版仓库指南；修改结构/约定/主线状态时两份同步更新。

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概况

NA70 双楔镜激光雷达感知系统。当前主线（见 `Plan/MASTER_开发与验收总计划.md`，唯一开发与验收入口）是**无 IMU、静态环境中移动雷达的点云里程计 + 体素地图融合**——不是完整 SLAM（无回环、无位姿图、无全局优化）。移动物体检测（`backend/motion.py`、`ROS2/src/na70_motion_detector/`）已退出主流程，后端链路保留，入口移出，相关文档为历史背景。

部署目标：Jetson Orin Nano 4GB + ROS2 Humble；Windows 本机负责开发、离线 PCAP 处理与 WebUI 回放。

## 常用命令

需要 Python 3.12、Node.js 22.12+。shell 是 bash（Git Bash on Windows），但项目脚本按 PowerShell/Windows 约定运行。

### WebUI（`web/`）

```powershell
# 一键启动（建 venv、装锁依赖、构建前端、起服务、开浏览器，默认 127.0.0.1:8765）
.\start.ps1                 # 参数：-SkipBuild / -Port / -Python / -NoBrowser；或直接运行根目录 启动WebUI.bat

# 后端测试（decoder/API/mapping/registration/motion/preflight 等）
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m pytest tests\test_mapping.py -q        # 单文件
.\.venv\Scripts\python.exe -m pytest tests\test_mapping.py::TestX::test_y -q  # 单用例
.\.venv\Scripts\python.exe tests\benchmark.py                        # 回放性能基准

# 后端单独跑（已装依赖时）
.\.venv\Scripts\python.exe -m backend.run
```

### 前端（`web/frontend/`）

```powershell
npm run dev          # Vite dev server，/api 代理到 8765
npm run build        # tsc -b && vite build（类型检查 + 生产构建）
npx playwright install chromium   # 首次
npm run test:e2e     # 需要 8765 后端已在运行；完整回放测试约 4 分钟，产物在 web/test-results/
```

### ROS2（`ROS2/`，WSL2 Ubuntu 22.04 + Humble，仅用于验证，非当前前置条件）

```bash
source /opt/ros/humble/setup.bash
colcon build --symlink-install && source install/setup.bash
ros2 launch na70_motion_detector motion_detector.launch.py
```

`na70_motion_detector` 内单测：`python -m unittest discover -s test`。

## 架构

### web/（Windows 本机 WebUI，独立可运行）

- `backend/decoder.py`：NA70 PCAP 解码。协议唯一参考是 `ROS2/src/double_wedge_driver/src/inno_device_lidar_node.cpp` 与合帧节点 `test_lidar_driver.cpp`。关键事实：1410 字节载荷 = 200 点 + 尾部设备时间/出厂状态；距离码 ×16 mm、俯仰 ×0.0258°、航向 ×0.0432°；UDP 端口 2368；每 100 个有效包组成一帧；初始轴映射 `(z, -x, -y)` 后按 `Ry(pitch_offset) × Rz(yaw_offset)` 旋转。
- `backend/mapping.py` + `mapping_jobs.py`：点云配准与建图的**唯一实现**，通过 `POST /api/mapping/jobs` 走此链路。旧 `/api/captures/{id}/registration` 只是兼容外壳；`registration.py` 已被取代，仅供历史合成测试。任务结果（轨迹、逐帧质量 JSONL、关键帧、地图分块 JSONL、PLY/PCD、validation.json）全部落盘到 `web/data/<uuid>/mapping/<job_id>/`，服务重启后直接读取不重算。
- `backend/mapping.yaml`：配准与地图阈值集中于此，随任务记录 `config_version`（内容哈希）。**这些数值是可运行基线，不是验收阈值**。
- `backend/motion.py` + `bands.py` + `spatial.py`：历史运动分析（射线历史、距离众数背景、4D 体素化加权 DBSCAN、点带判别）。点带数 ≠ 人数，点带长度 ≠ 人体尺寸。
- `backend/preflight.py` + `preflight.yaml`：采集预检工作台。
- `backend/app.py`：FastAPI 入口，**只绑定 127.0.0.1**（单用户本地服务，无认证）。帧二进制格式 v1 见 `web/README.md`。
- `frontend/`：React 19 + TypeScript + Vite + Three.js。三种视图：RAW（原始坐标）、ODOMETRY（后端位姿，经 `/api/mapping/jobs/{id}/frames/{index}/cloud` 读地图坐标，前端不自行伪造累计坐标）、MAP（体素融合地图，分块流式读取）。
- 缓存目录 `web/data/<uuid>/`（可由 `NA70_DATA_DIR` 重定向）仅由本应用管理，已导入的捕获视为不可变。

### ROS2/

- `double_wedge_driver/`：C++17 NA70 驱动、消息、launch、RViz 配置——是解码协议的权威参考。
- `na70_motion_detector/`：Python 算法核心 + ROS2 封装。算法核心与 ROS2 封装必须分离，核心可离线单测。

### Plan/ 与证据纪律

- `Plan/MASTER_开发与验收总计划.md` 是唯一开发顺序与门禁（R0–R8 + 完整 SLAM），`Plan/05_开发执行规范.md` 是执行规范。旧阶段计划已删除，不作参考。
- 门禁验收证据存 `Plan/evidence/`；测试失败必须保留失败事实，不得改写描述把失败变成通过；合成数据结果显式标记 `SYNTHETIC`；WSL2 结果标记 `WSL2_HUMBLE_X86_64`，不能替代 Jetson/ARM 证据。
- 严格区分「已运行」「已输出」「指标达标」「阶段通过」；当前 R7 为「执行中（基线失败）」，真实移动定位未通过，不能宣称无拖影验收。

## 代码与提交约定

- Python 四空格、snake_case；TS 两空格、camelCase；C++ 随周边风格。无 formatter/linter 配置。
- 所有检测/配准阈值进 YAML，不硬编码；PointCloud2 字段按名称和 datatype 查找，不假定固定 offset；保留 header stamp、frame_id、intensity 与点索引对应。
- 空输入、NaN/Inf、乱序时间、参数非法必须有明确处理。
- 提交信息用简洁中文动作总结（如「初始化项目：…」「同步 WebUI 测试用例…」）。
- PCAP、缓存、构建产物不入库；保留 `requirements.lock` 与 `package-lock.json`。

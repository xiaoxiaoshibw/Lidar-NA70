# NA70 点云工作台

Windows 本机 Web 应用：导入经典 PCAP，按项目 ROS2 驱动解码、组帧，在浏览器回放三维 XYZI 点云；提供采集预检、球门标注与跟踪、历史建图验收和导线 W1 原始回波诊断。数据、缓存和分析结果均保存在本机。

当前路线决策以 [Plan/README.md](../Plan/README.md) 为准。球门识别仍是已记录的近期主线；导线分支现提供 [W1 原始回波工作台](../docs/NA70_WIRE_ARCHITECTURE.md)及离线工具 `tools/audit_thin_wire.py`。两者只做原始观测诊断，真实细线回波归属仍为 `NOT_EVALUATED`，W3/W4/W5 识别与运动接口尚未启用。

## 启动

需要 **Python 3.12**、**Node.js 22.12+（本次验证为 24.14.0）** 和开启硬件加速的桌面浏览器。

**一键启动：双击项目根目录的 `启动WebUI.bat`。** 脚本优先使用已有的 `web/.venv`，否则使用 PATH 中的 `python`，调用下述启动流程并自动打开浏览器。运行期间保留命令窗口，按 Ctrl+C 停止；启动失败时窗口会保留错误信息。首次启动需要联网安装依赖。

BAT 也支持命令行参数，例如 `启动WebUI.bat -SkipBuild` 跳过前端构建，或 `启动WebUI.bat -Port 8766` 更换端口。

在 PowerShell 中运行：

```powershell
cd D:\Code\radar\web
.\start.ps1
```

脚本创建 `web/.venv`，按锁文件安装依赖、构建前端、启动服务；健康检查成功后自动打开浏览器。默认地址 `http://127.0.0.1:8765`，Ctrl+C 停止。

已有构建时：

```powershell
.\start.ps1 -SkipBuild
```

参数：`-Python 'C:\path\to\python.exe'` 指定 Python 3.12；`-Port 8766` 更换端口；`-NoBrowser` 仅启动服务。PowerShell 若限制脚本执行，可对本次进程使用 `powershell -ExecutionPolicy Bypass -File .\start.ps1`，无需修改全局策略。

完全离线且已安装依赖时可以直接启动：

```powershell
.\.venv\Scripts\python.exe -m backend.run
```

首轮安装依赖需要联网，构建后的页面不依赖 CDN 或在线字体。

## 使用

1. 点击「导入 PCAP」或拖入文件；左侧会显示上传和索引状态。支持取消导入。
2. 默认叠加 **200 帧（约 400 万原始点）**，文件开头也加载完整窗口。原始解码不变，不复制点、不生成虚构表面。视图采用正交投影，自动聚焦主体，远处回波仍然保留。
3. 默认左键拖动旋转视角：左右拖动水平环绕、上下拖动控制俯仰，画面跟随鼠标方向且不会翻转；右键拖动平移，滚轮向鼠标位置缩放。左下可切换左键的平移／旋转模式，也有放大、缩小和聚焦按钮。双击点云定位观察中心。画布获得焦点后，F 聚焦主体、R 查看全部、1／2／3 切换俯视／正视／侧视。工具箱可切换浏览与原点测距；原点测距拾取点击处附近的真实点，显示三维直线距离、XY 水平距离、ΔZ 和 XYZ 坐标，并按 RAW／ODOMETRY／MAP 标明坐标原点。右侧可把参考网格调整为每格 1／2／5／10／20／50 m；网格只表达尺度，不是重建地面。俯仰角限制在距天顶 14° 到略低于水平面之间，不会转到极点（极点处方位角无定义，会让水平拖动失效后突然乱转）；底栏显示当前方位角与俯仰角。
4. 底部「全帧回放」控制播放、暂停、逐帧和跳转，支持 0.5×／1×／2×，时间轴覆盖整段 PCAP。密集窗口在播放中保留；整包模式会继续按全帧时间轴播放，点云在后台渐进加载。想观察短时间变化，可手动选单帧。
5. 右侧调整高度、反射率或距离着色，点大小、透明度和距离范围。默认保留完整测距范围（0–1200 m）。「聚焦主体」只改变相机范围，不删点；「全景」包含所有远处点。高度／距离色标按代表样本的 2%–98% 区间显示以增强主体层次，超出区间使用端点色，不丢弃点。
6. 支持 1／5／10／20／100／200／500 帧，以及 **整包全部点**。整包按批渐进加载，可在加载时改变视角或切换窗口取消加载。文件开头窗口包含后续帧；达到窗口长度后使用截至当前帧的历史窗口。因此密集叠加用于空间展示，不能把窗口内点都视为当前时刻的观测。运动会形成拖影，叠加不含运动补偿。
7. 默认 **全部点、不抽样**，取消原先 40 万点上限；显存有限时可手动选择 100／400／800 万点预算。只剔除零距离及非有限坐标，HUD 分别显示非零渲染点数与原始点数。
8. 导入记录在重启后保留；点击文件条目可重新打开。垃圾桶只删除本应用的导入副本和索引，不删除原文件。
9. 主工作台当前提供原始点云、历史配准和地图视图，球门与导线功能通过顶部导航进入各自工作台。移动点带分析的后端与历史证据保留，入口已退出当前验收流程；下述算法说明仅作历史功能记录。

分析使用固定雷达的全片射线历史、距离众数背景，随后把候选点放到联合 (x, y, z, t) 四维空间做体素化加加权 DBSCAN 聚类，再按持续时间、时间片覆盖、起终点位移、速度、PCA 主轴比、时间-位置回归残差和直度判别点带，最后按时间片恢复每一时刻的局部目标并做片段合并。它只比较再次扫到同一原始方向时的距离，避免把双楔镜扫描轨迹变化误判为移动。

**重要限制**：点带数量是时空片段数量，**不等于人数**；页面与导出都不输出"检测到 N 个人"。点带长度是观测期内的运动路径，**不是人体尺寸**——人体宽高由垂直于运动方向的截面估计。两个目标在同一时刻占据同一四维体素时会被合并为一条点带。当前默认参数尚未通过人工逐点真值验收。

## 解码边界

唯一参考是 `ROS2/src/double_wedge_driver/src/inno_device_lidar_node.cpp` 和合帧节点 `test_lidar_driver.cpp`。

- 经典 PCAP 2.4，支持四种大小端／微秒／纳秒魔数，Ethernet、可选 VLAN、IPv4、UDP 目标端口 2368。
- 只接受 1410 字节的 NA70 载荷：前 1400 字节是 200 个点，尾部 8 字节设备时间与 2 字节出厂状态。不会把其他端口的同长度报文当作雷达数据。
- 距离码 ×16 mm；俯仰码 ×0.0258°；航向码 ×0.0432°；坐标单位 m，反射率原值 0–255。
- 默认正常安装，初始轴映射 `(z, -x, -y)`，随后按 ROS2 的 `Ry(pitch_offset) × Rz(yaw_offset)` 旋转。每组结束时的偏移作用于整组点。前端相机 Z 向上，点坐标不交换。
- 每 100 个有效包组成一帧。索引保存每个载荷的文件位置及组结束时的偏移，随机读取与顺序解码一致。末尾不足 100 包也可回放，标记「不完整帧」；ROS2 原驱动不会发布这个尾组。
- 合帧元数据的 `device_time` 使用最后一个包，符合 ROS2 合帧节点时间戳选择；同时保留首包设备时间和捕获时间。无效设备时间记为 null，离线回放不伪造当前墙钟时间。
- 保留包顺序；倒退捕获时间产生数据提示，播放时间轴单调化。帧重复时间不会导致除零。
- pcapng、非 Ethernet、分片 UDP 暂不支持。无有效包时导入失败；可恢复的异常包计数并跳过，损坏记录长度会停止扫描并保留此前完整记录。

## 接口

接口默认只对本机开放；这是单用户本地服务，未提供互联网部署的用户认证。

| 接口 | 行为 |
|---|---|
| `GET /api/health` | 服务与解码器版本 |
| `GET /api/captures` | 已导入记录，支持页面刷新后恢复 |
| `POST /api/captures?id=<uuid>&filename=<name>` | 原始文件字节流，Content-Type: application/octet-stream；返回 202，随后后台索引 |
| `GET /api/captures/{id}` | uploading／indexing／ready／error、进度、包数、时长、SHA-256、数据提示 |
| `GET /api/captures/{id}/frames?offset=0&limit=1000` | 分页索引，limit 最大 2000 |
| `GET /api/captures/{id}/frames/{index}` | 元数据与点数据的二进制帧 |
| `POST /api/captures/{id}/analysis` | 启动本机全片射线历史运动分析，返回 202 |
| `GET /api/captures/{id}/analysis` | 分析配置、整包计数、事件和轨迹摘要 |
| `GET /api/captures/{id}/analysis/frames/{index}` | 当前帧分析元数据、逐点 uint8 运动掩码与本帧运动点带目标 |
| `GET /api/captures/{id}/analysis/bands.csv` | 每个运动点带逐时间片的局部目标状态 CSV |
| `DELETE /api/captures/{id}` | 取消导入或删除导入副本和缓存 |

上传采用原始流而非 multipart，避免重复落盘；客户端预先生成 UUID，可以在上传完成前取消。后台索引最多两个任务并行；原始点云不会全部展开到内存。

帧二进制格式 v1：

```text
uint32 little-endian：JSON 头长度
UTF-8 JSON：补空格至 4 字节边界
N × 4 float32 little-endian：[x, y, z, intensity]
```

JSON 包含 `version`、`fields`、`dtype`、`index`、`capture_time`、`end_time`、`time`、`device_time`、`device_start_time`、`frame_id`、`point_count`、`packet_count`、`complete` 及角度偏移。前端验证版本和二进制长度。

前端缓存所选窗口及少量预取帧；整包模式缓存整包，其内存开销随文件点数增长。读取最多 4 个并发请求，每批 25 帧渐进更新，GPU 缓冲按固定 25 帧分块复用；着色、大小和透明度用着色器更新，不重建全部点坐标。跳转或换文件时取消旧请求，释放不再使用的 GPU BufferGeometry。显示参数不会修改解码数据。

缓存位于 `web/data/<uuid>/`：`capture.pcap`、`meta.json`、`frames.json`、分析后的 `analysis.json` 和 `analysis_masks.bin`。可用 `NA70_DATA_DIR` 指定其他目录。该目录仅应由本应用管理；已导入的捕获文件应视为不可变。如需换文件，请移除后重新导入。

## 页面互通

四个页面（主工作台 `/`、标注工作台 `/annotate/`、跟踪 Demo `/demo/`、导线工作台 `/wire/`）顶部共用互通导航（`frontend/src/PageNav.tsx`）。生产环境链接指向后端路由；Vite 开发模式使用对应 `.html` 入口。

## 导线工作台（/wire/）

导线页使用 `/api/wire` 的独立持久化运行接口。可从已导入的 PCAP 创建 W1 原始诊断，选配独立背景录制和终点帧；运行状态、逐窗原始统计、逐包时间线、有限抽样的正／侧／俯视投影与每个抽样点的原始帧／包／点身份可在重启后回看。抽样仅用于浏览，窗口指标来自完整原始数据。运行目录保存配置、输入与源码哈希；缺少独立目标位置和复核时，界面始终显示 `NOT_EVALUATED`，不会把 ROI 点数认定为导线回波。

接口分为 `GET /api/wire/capabilities`、`POST/GET /api/wire/runs`、`GET /api/wire/runs/{run_id}`、`GET /api/wire/runs/{run_id}/windows/{window_id}` 与其 `/cloud` 子资源。后续 W3 人工 ROI、W4 全视场和 W5 运动接口先在 capabilities 中标为不可用；数据和验收语义见[导线调试契约](../docs/NA70_WIRE_DEBUG_ACCEPTANCE.md)。

## Demo 页（/demo/）

独立入口 `http://127.0.0.1:8765/demo/`：足球球门远距离识别 / 锁定 / 跟踪演示（LiDAR-only，无 IMU）。与主页面并列，不影响现有功能。

- 采集列表只显示适合演示的目标场景数据（按 `backend/goal_detection.yaml → webui.capture_name_filter` 文件名过滤）。
- 会话以真实帧率（≈10 Hz）回放所选 PCAP，逐帧运行可调滑动窗口叠加 + 球门几何检测 + 状态机跟踪；默认 20 帧来自 `9.14_caochangyou.pcap` 对照（20 帧比 10 帧更早锁定且连续跟踪帧更多），不是不可修改的常数。`叠加帧数`（1–100）与`回放速度`（0.5–5×）可调，变更自动重启会话。
- 视图：单帧 / 叠加点云切换；ROI、立柱、轨迹、debug 图层；下方面板显示叠加状态（Accumulation n/N、FPS、点数）与目标状态（Goal ID、距离、置信度、State、Age）。右上 `Pipeline / Algorithm Inspector` 显示当帧实际执行分支，例如 Range/ROI、Voxel、BEV、FOV crop、Z、立柱、柱对、NMS 和 Association；未运行分支显示 `skipped / 未执行`，不会用 0 ms 冒充。当前 Stage 可见点为青色、候选支撑点为橙色、拒绝点为暗灰色。
- 接口：`GET /api/demo/captures`、`POST/GET/DELETE /api/demo/sessions*`、`GET /api/demo/sessions/{id}/cloud?mode=single|accumulated`（帧二进制 v1）、`GET /api/demo/sessions/{id}/debug`。可观察性接口为 `GET .../pipeline`、`GET .../debug/cloud?source=&stage=&candidate_id=`（帧二进制 v3，`classification` 与候选 `highlight` 分离）、`GET .../provenance?voxel_index=`、`GET .../logs`、`GET .../metrics`、`GET/PUT .../config` 与 `GET .../snapshot`；最后一个接口把当前 Pipeline、候选、关联、日志、指标和配置版本一次性输出给 Codex/AI。会话在内存中，服务重启后需重新开始。
- 算法阈值集中在 `backend/goal_detection.yaml`；离线整片运行与证据见 `tools/run_goal_demo_offline.py` 与 `Plan/evidence/goal-demo/`。状态机与检测仅使用几何特征，不输出「检测到 N 个人」类语义结论；候选≠语义确认。
- **分级检测策略（`strategy.mode: staged`，窗口长度可配置，当前默认 10 帧）**：第 1 级 BEV 在纯 XY 平面按「双致密 blob + 跨度密度对比」提议球门区域（忽略 z，容忍 yaw 拖影）；锁定后第 2 级把检测收缩到候选周围 6 m FOV——同一时间最多一个球门假设；第 3 级在 FOV 内用 z 直方图众数估地面、修正包围盒高度；聚焦后位置优先取当前单帧（抑制拖影平均）。预锁定阶段强候选（≥lock_confidence）优先并允许跳转，围栏缺口等低置信度结构无法锁定。`strategy.mode: poles` 保留旧的全图检测行为作对照。状态面板显示「策略 / FOV」，勾选 debug 可见 BEV 区域标记。
- **实时标注对比（/demo/ 页勾选「标注」）**：回放可暂停（控制条「暂停/继续」），点云上单击生成标准球门框（自动朝向雷达）、拖框平移、拖黄把手旋转，与系统跟踪绿框同屏对比；面板实时给出中心偏差 Δ / 宽度差 / 高度差，供人工直观判断跟踪是否正确；「保存标注」写入 `/api/demo/annotations`（labeler=demo-live，评分器可对账），「清除」移除。
- **框来源可追溯**：每个帧内候选包含 `candidate_id`、检测分解分数、距离/方位/宽高、8 个旋转框角点、AABB、体素支撑索引、原始 `(frame,point)` 引用与拒绝原因；Track ID 才表示跨帧身份。点击候选只把该候选的支撑点高亮。Detection Score 与 Association 调试信息分开；P3 将统一实际关联决策和展示分数。
- **无 IMU 边界**：当前 ID 是短时传感器坐标系关联。雷达移动/转动时允许框在传感器坐标中连续移动；缩窄 FOV 只在 LOCKED/TRACKING/REACQUIRE 使用，失锁后恢复全图搜索。它不恢复世界坐标，也不宣称 SLAM。合成测试包含随机噪声、树/高墙干扰和逐帧平移+yaw，验证短时 ID 连续性。
- **Synthetic 套件**：`tools/run_goal_synthetic_suite.py` 运行 15 类带独立真值的场景，按 seed 复现点云、传感器运动、遮挡和干扰物，输出 precision/recall、框误差、ID switch、最长连续命中和目标缺席期间错误确认。它显式标记 `SYNTHETIC`，用于开发失败定位，不替代真实标注或设备验收。

## 标注工作台（/annotate/）

独立入口 `http://127.0.0.1:8765/annotate/`：球门检测的人工标注页面，与 demo 页分离。标注人是语义判定的唯一来源；系统只负责把画笔/点选变成可复查的标注。

- 流程（画笔工作流）：选采集 → 时间轴拖动/点击定位（或空格播放、←/→ 步进）→ 在 2D 俯视点云上**用画笔沿球门轮廓涂画**（B 键画笔模式，M 键移动视图，N 键负样本点选）→ 画完一组按**回车（脚本分析并入列）**或 **G（直接入列，不跑分析）**，同一画面可连续标注多组 → **Ctrl+S 一次性保存全部**。画错了 Ctrl+Z/删除撤销上一笔、Esc 清空，无需逐点改坐标；已保存标注可在列表中定位（跳到其帧区间）或删除。
- 脚本分析（可选）：后端对画笔区域做纯几何测量（主轴双立柱密度峰 → 柱心/门宽/柱高/厚度/朝向），返回提案框并显式给出宽度/高度是否在 `goal_detection.yaml` 常规区间的检查与警告；提案是否采用由标注人决定，不自动保存。分析阈值在 `goal_detection.yaml` 的 `annotate_analysis` 段。
- 点云叠加：**原始全量点**（无体素化、无距离/ROI 过滤），20 帧起可选（20/30/50/100/200），显示的点集即标注所指的点集。`POST /api/demo/annotate/cloud`（`voxel: 0` 为原始模式）返回的 meta 显式声明 `filtering: "none"`，前端校验后渲染。
- 视频轨道：底部时间轴按帧索引铺满全片，刻度用真实设备时间，悬停显示 帧/时间/包数/点数；帧号、设备时间、包数全部来自同一份解码帧索引，当前叠加窗口以蓝色带显示，已保存标注按帧区间画绿/红带。标注保存前强制「叠加云窗口 = 时间轴当前窗口」且记录 `window_context`（帧区间、设备时间区间、时长、cloud_id），防止标注与实际错位。
- 存储位置 `web/data/annotations/<capture_id>.json`，goal 标注存框（center/size/yaw）或纯画笔区域（strokes，无框，评分器按 no_truth 跳过几何评价），每条标注带唯一 `label_id`；绑定采集 SHA-256、配置版本、标注人与时间，原子写盘、服务重启后保留。数据契约见 `docs/GOAL_DEBUG_CONTRACT.md` §1.1。
- 对账：`tools/score_goal_detection.py` 读标注 + 逐帧检测日志，输出命中率、首个命中帧、中心误差与误检点清单（含负样本匹配），产物写 `Plan/evidence/goal-demo/<run>-validation/`。
- 接口：`GET /api/demo/annotate/timeline`（逐帧对齐数据）、`POST /api/demo/annotate/cloud`（按需原始/体素叠加）、`POST /api/demo/annotate/analyze`（画笔几何分析）、`GET/POST/DELETE /api/demo/annotations`（按 `label_id` 删除）。回放会话 `/api/demo/sessions*` 仍服务于 Demo 页。

## 开发和验证

```powershell
cd D:\Code\radar\web
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe tests\benchmark.py
cd frontend
npm run build
npx playwright install chromium
# 先在另一个终端启动 8765 服务
npm run test:e2e
```

完整回放测试使用约 4 分钟，先运行导入交互测试再执行全片播放；浏览器测试会保留两个真实 PCAP 的导入副本。测试产物在 `web/test-results/`。

ROS2 逐点对比（可选，仅用于验证；日常运行不依赖 WSL2／ROS2）：

```powershell
wsl -d Ubuntu-22.04 -- bash /mnt/d/Code/radar/web/tests/ros2_compare.sh
```

它从当前 ROS2 驱动源码构建到 WSL2 `/tmp/na70-web-ros2-validation`，在独立 DDS Domain 174、UDP 12368 中对比真实 PointCloud2。仅清理脚本启动的子进程，不干扰其他节点。

开发模式：后端 `python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765`；前端 `npm run dev`（/api 代理到 8765）。启动脚本／构建使用 `requirements.lock` 与 `package-lock.json`；更新依赖后重新生成锁文件并验证。

## 下一阶段

### 固定点转台多视角建图（当前开发版）

现有建图卡片内的“固定点转台多视角建图”支持选择多个已导入 PCAP、导入 `angle_log.csv` 和 `turntable_calibration.json`，只融合日志标记的静止驻留段，连续转动段会作为排除区间写入质量证据。后端接口为 `POST /api/mapping/turntable/jobs`，结果按任务目录落盘并可在服务重启后读取。

核心变换是 `T_map_lidar(theta) = T_map_turntable × Rz(theta) × T_turntable_lidar`，允许雷达原点与旋转轴偏心。角度来源必须是 `MANUAL`、`ESTIMATED_TEMPORAL`、`ESTIMATED_GEOMETRY` 或 `REFINED_CONSTRAINED`；非 `MANUAL` 永远标记为估计值。当前 PCAP 没有同步编码器角度，开发清单和地图均明确写为 `DEVELOPMENT / DIAGNOSTIC / ESTIMATED_ANGLE`，不能当作正式精度验收或完整 SLAM。

示例模板在 `backend/angle_log.example.csv`、`backend/turntable_calibration.example.json`；现有数据清单在 `backend/angle_log.20260914-development.csv` 与 `backend/turntable_calibration.20260914-assumed.json`。离线运行：

```powershell
cd D:\Code\radar
.\web\.venv\Scripts\python.exe tools\build_turntable_map.py --pcap 2\9.14_caochangzhong2.pcap --pcap 2\9.14_caochangzuo.pcap --pcap 2\9.14_caochangyou.pcap --angle-log web\backend\angle_log.20260914-development.csv --calibration web\backend\turntable_calibration.20260914-assumed.json --output Plan\evidence\turntable\<run>
```

当前开发运行证据见 `../Plan/evidence/turntable/20260915-development-final/`；其中 A/B/C/D 指标、每个子图时间边界、来源、拒绝原因和最终变换均可复查。

下一步使用带动作时间记录的真实人群 PCAP 验收和调参，覆盖单人、多人交叉、遮挡、停留与离场。现阶段只证明工程链路和合成行为正确，不能把默认参数输出当作最终效果结论。当前项目规划已将 MapMOS 退出主线。

实时接入由雷达侧服务接收 UDP 或订阅 ROS2，再以 WebSocket 发送统一帧数据。浏览器画布复用，实时队列另行限制并显示断流与延迟。本版不含这两项实现。
# 当前入口：无 IMU 静态环境建图（mapping job）

2026-09-11 主线调整。WebUI 主流程改为：导入 PCAP → 检查帧索引 → 确认静态环境模式 → 开始点云配准 → 查看轨迹与失败帧 → 查看累计地图 → 对照 RAW 与 MAP → 导出地图与验收报告。移动物体检测退出主流程（后端链路保留，入口移出）。

**当前阶段是纯点云里程计 + 体素地图融合，不是完整 SLAM**：无 IMU、无回环检测、无位姿图、无全局优化、无重定位、无运动去畸变。

## 三种视图

| 视图 | 内容 |
|---|---|
| `RAW` | 原始坐标系叠加，不含运动补偿，用于暴露拖影 |
| `ODOMETRY` | 后端生成的位姿与分段轨迹，失败帧以状态颜色标在 3D 中 |
| `MAP` | 后端体素融合地图 + 当前帧，地图分块按视野流式读取 |

失败帧不隐藏：时间轴上按状态着色（绿=接受、黄=质量不达标、橙=几何退化、红=跟踪失锁、灰=未处理），可点击跳转；右侧列出失败帧与原因。

## 建图接口

`progress` 为 0–100。`POST /api/captures/{id}/registration` 与 `GET` 同名接口保留为兼容外壳，内部是同一个 mapping job。

| 接口 | 行为 |
|---|---|
| `POST /api/mapping/jobs` | 创建任务，body `{"capture_id": "...", "config": {...}}`，返回 202 |
| `GET /api/mapping/jobs` | 任务列表（`capture_id` 可过滤） |
| `GET /api/mapping/jobs/{id}` | 任务记录：状态、进度、接受/拒绝/失锁帧、关键帧、地图点数与边界、版本、耗时、峰值内存 |
| `POST /api/mapping/jobs/{id}/cancel` | 取消任务 |
| `GET /api/mapping/jobs/{id}/trajectory` | 分段轨迹与逐帧位姿 |
| `GET /api/mapping/jobs/{id}/frames/{index}` | 单帧质量：状态、原因、重叠率、RMSE、平移/旋转增量、迭代次数 |
| `GET /api/mapping/jobs/{id}/map` | 地图元数据（体素分辨率、融合点数、体素数、分块索引、边界、各段体素数） |
| `GET /api/mapping/jobs/{id}/map/chunks` | 按 `ids` 或 `min_x..max_z` 取分块，`limit` 限制单次返回体素量 |
| `POST /api/mapping/jobs/{id}/export` | 生成证据包 zip |
| `GET /api/mapping/jobs/{id}/export/{name}` | 下载导出文件 |
| `GET /api/captures/{id}/mapping/jobs` | 该导入记录下的任务 |

## 结果目录

```text
web/data/<capture_uuid>/mapping/
  index.json
  <job_id>/
    job.json  config.json  trajectory.json  trajectory.csv
    frame_quality.jsonl  keyframes.json  map_metadata.json
    map_chunks/index.json  map_chunks/<cx>_<cy>_<cz>.jsonl
    map.ply  map.pcd  validation.json
    export/mapping-evidence.zip  export/manifest.json
```

任务完成即落盘，服务重启后 WebUI 直接读取，不重算。`map.ply`／`map.pcd` 是体素融合结果：每个体素一个质心，含平均强度、观测次数、来源关键帧 ID。

## 阈值

Sweep、SE(2) 候选、配准与地图阈值集中在 `backend/mapping.yaml`（`sweep_*`、`se2_*`、`gicp_*`、`ndt_*`、`voxel_size`、`max_points`、`max_range`、`min_points`、`correspondence_distance`、`max_iterations`、`min_overlap`、`max_rmse`、`max_translation_step`、`max_rotation_step`、`keyframe_translation`、`keyframe_rotation`、`submap_keyframes`、`map_voxel_size` 等）。`config_version` 是配置内容哈希，随任务记录。

这些数值目前是**可运行基线，不是验收阈值**：必须先用静止噪声基线与真实采集统计确定，才能用于判定通过。

## 真实数据现状

固定 100 包已不再作为里程计算法的扫描单位。`backend.sweeps` 使用包级时间和原始角码按角域覆盖组装 sweep；原 100 包 frame 只为 WebUI/API 向后兼容保留。四份 2026-09-14 操场 PCAP 的覆盖驱动 sweep 在 0.3 m 下相邻覆盖中位数为 0.465–0.591，固定 100 包仅为 0.213–0.255；G1 已通过。分析命令如下：

```powershell
cd D:\Code\radar
.\web\.venv\Scripts\python.exe tools\analyze_sweep_coverage.py `
  2\9.14_caochangzhong2.pcap 2\9.14_caochangzuo.pcap `
  2\9.14_caochangyou.pcap 2\9.14_caochangyidong.pcap `
  --output-dir Plan\evidence\mapping-repair\20260914-sweeps
```

受约束 SE(2) 的 point-to-plane、三层 robust GICP、两层 NDT 与逐自由度可观测性诊断已实现并通过合成回归，并显式选择非地面结构避免地面主导。12 对抽样能观察到左正/右负的候选 yaw，但左 243 对/右 230 对全量相邻复跑的正式接受数仅 0/3，候选符号不连续；扩大到 10 个 sweep 跨度仍无改善。G2 保持 `IN_PROGRESS（真实回归 FAIL）`，这些候选尚未接入 mapping job，也没有产生可融合轨迹。完整数值与下一项采集实验见 `../Plan/evidence/mapping-repair/20260914-sweeps/development-report.md`。

`1.pcap` 与 `4.pcap` 的历史真实任务同样未通过：首帧之后第一个待配准帧即判为几何退化，随后跟踪失锁，地图只含参考帧。放宽对应距离只会制造虚假重叠。历史证据见 `../Plan/evidence/mapping-repair/20260911-repair/`。

当前 PCAP 已另外生成一张“已观测覆盖地图”：先按每个 sweep 的局部地面 z 过滤相对高度 `[-1.5 m, +4.5 m]`，再对 zhong2/zuo/you 做体素融合，产物在 `../Plan/evidence/mapping-repair/20260914-observed-map/`。这张图用于查看现有数据覆盖，不代表完整操场、R7 或 SLAM 通过；左/右文件的录制内时变 yaw 仍需补偿。

真实静态环境「无拖影/无重影」验收尚未完成，Jetson Orin Nano / Humble 验证尚未进行。以下运动分析内容为历史功能说明。


## 建图修复与实验边界（2026-09-11）

当前 mapping 后端已修复地图坐标融合和时间预测，ODOMETRY 点云通过后端 `/api/mapping/jobs/{id}/frames/{index}/cloud` 读取地图坐标；失败帧不显示无约束位姿。静止初始化 API 配置需要 `initialization_mode=stationary_window`、起止帧及 `stationary_declaration`。默认仍为 single_frame。

WebUI 建图卡片提供“静止初始化帧数”：确认录制开头静止时填入帧数（例如 300），系统会把该窗口作为有声明的初始地图；填 0 使用单帧模式。未确认静止时不要填写正数。

初始化计数与正式配准计数分开；“处理完成”不表示真实建图通过。006 经用户确认是全程静止基线，不是移动验收数据；当前没有新的真实移动 PCAP，不能宣称无拖影验收。开发计划与证据见 Plan/MASTER_开发与验收总计划.md 和 Plan/evidence/mapping-repair/20260911-repair。KISS-ICP / GLIM 仅在独立实验环境评估，没有替换生产接口。


## 2026-09-12 采集预检工作台

采集模式、检查项与路线记录自动保存；上传诊断和任务快照写入证据。预检与任务完成均不等于真实定位通过。104 项后端测试通过，构建通过，mapping 浏览器 2 passed / 1 skipped（真实用例未启用）。本轮测试服务使用 localhost:8767；原 8765 后端尚未重启。P3 几何评价仍缺真实数据、ROI 与冻结容差。

详细命令、文件与限制见 [交付证据](../Plan/evidence/mapping-repair/20260911-repair/preflight-delivery-20260912.md)。R7 等待真实移动采集，R8 延后。


P3 几何准备补充：`tools/measure_mapping_geometry.py` 读取现有任务 map_chunks，按用户给定的固定结构 ROI、平面法向、坐标段和冻结标准测量 P95–P05 厚度及双层峰候选，保存合同与地图分块 SHA-256。不同段分开测量，候选不等于固定物体语义真值或 R7 PASS。未提供真实标注合同，真实几何评价仍未运行。

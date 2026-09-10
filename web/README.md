# NA70 点云工作台

Windows 本机 Web 应用：导入经典 PCAP，按项目 ROS2 驱动解码、组帧，并在浏览器查看与回放三维 XYZI 点云。数据和缓存均保存在本机。

## 启动

需要 **Python 3.12**、**Node.js 22.12+（本次验证为 24.14.0）** 和开启硬件加速的桌面浏览器。

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
2. 就绪后自动显示首帧。左键旋转、滚轮缩放、右键平移，工具栏提供俯视、正视、侧视、复位和全屏。
3. 底部控制播放、暂停、逐帧和时间轴跳转，支持 0.5×／1×／2×。
4. 右侧调整高度、反射率或距离着色，点大小、透明度和距离范围。默认保留完整测距范围（0–1200 m）。远处离散回波会拉大自动视野；观察近场时可将最大距离改为 100 m，再点击「复位视角」适配当前可见点云。
5. 选择最近 5／10／20 帧叠加以观察静态结构。叠加没有运动补偿，移动物体可能有拖影。
6. 渲染点数预算为 10／20／40 万点，默认 40 万；超出时等间隔抽样。零距离点不绘制，但后端仍返回完整原始点序列。HUD 分别显示渲染点数与原始点数。
7. 导入记录在重启后保留；点击文件条目可重新打开。垃圾桶只删除本应用的导入副本和索引，不删除原文件。

「运动状态」固定显示**未分析**，不代表检测到或未检测到运动。实时雷达与运动分析页签标为规划中，未提供假分析结果。

## 解码边界

唯一参考是 `ROS2/src/double_wedge_driver/src/inno_device_lidar_node.cpp` 和合帧节点 `test_lidar_driver.cpp`，不使用 `baseline/radar_protocol.py` 的早期推测格式。

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
| `DELETE /api/captures/{id}` | 取消导入或删除导入副本和缓存 |

上传采用原始流而非 multipart，避免重复落盘；客户端预先生成 UUID，可以在上传完成前取消。后台索引最多两个任务并行；原始点云不会全部展开到内存。

帧二进制格式 v1：

```text
uint32 little-endian：JSON 头长度
UTF-8 JSON：补空格至 4 字节边界
N × 4 float32 little-endian：[x, y, z, intensity]
```

JSON 包含 `version`、`fields`、`dtype`、`index`、`capture_time`、`end_time`、`time`、`device_time`、`device_start_time`、`frame_id`、`point_count`、`packet_count`、`complete` 及角度偏移。前端验证版本和二进制长度。

前端仅缓存当前帧附近最多 23 帧，预取后两帧；跳转或换文件时取消旧请求，释放旧 GPU BufferGeometry。显示参数不会修改解码缓存。

缓存位于 `web/data/<uuid>/`：`capture.pcap`、`meta.json`、`frames.json`。可用 `NA70_DATA_DIR` 指定其他目录。该目录仅应由本应用管理；已导入的捕获文件应视为不可变。如需换文件，请移除后重新导入。

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

离线运动分析以现有 `na70_motion_detector`／背景减除主线接入统一帧数据，增加任务状态、移动点和事件时间段；真实性能与效果需真实数据验收。当前项目规划已将 MapMOS 退出主线。

实时接入由雷达侧服务接收 UDP 或订阅 ROS2，再以 WebSocket 发送统一帧数据。浏览器画布复用，实时队列另行限制并显示断流与延迟。本版不含这两项实现。

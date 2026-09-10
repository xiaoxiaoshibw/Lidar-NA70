# 首版实测记录

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

当前仅经典 Ethernet PCAP、NA70 目标端口 2368、正常安装配置。pcapng、IP 分片、其他雷达协议需要后续扩展。点云叠加不包含运动补偿、网格重建或 SLAM；模型分析、目标框与实时连接尚未实现。显示预算与距离过滤只影响画面，不影响原始帧接口。

# Qt 双流录制与自动关联交付证据

日期：2026-11-05（Asia/Shanghai）。状态：**DEVELOPMENT / SYNTHETIC_CAPTURE_TESTED / REAL_ARCHIVE_ASSOCIATED**。未进行新的真实硬件录制；设备采样同步、真实外参及 Jetson 验收均未通过。

## 实现与环境

入口 `启动雷达IMU录制.bat` → `tools/start_na70_qt.ps1` → `tools/na70_qt_capture.py`。Qt 后台线程复用 `CaptureSession`、`CaptureRunner`、`ImuRecorder` 与 `UdpLidarCapture`；关联只通过正式 `backend.decoder.scan_pcap` 获取包时间和源偏移。原始会话与派生关联目录分开，不改已有原始数据。

环境：Windows 11 x86_64（10.0.26300），Python 3.12.10，PySide6-Essentials/shiboken6 6.12.0，pyserial 3.5，NumPy 2.5.3。Git 基线 `751faab3efcc7fadba9f264cf68cd364d616416f`，工作区已有未提交改动，本次保留。

关键文件 SHA-256：

| 文件 | SHA-256 |
|---|---|
| `tools/na70_qt_capture.py` | `3f69eecee47643331eff4fe4d82c3a64db093ee66d270c50e0d5595c40f4bd53` |
| `tools/na70_associate.py` | `57f6d2a49cb7dbb5132824c4801f422f505b6b0d4afe8adcd3deafe29666eb9d` |
| `tools/na70_qt_capture.yaml` | `83fc96d6d2daec95c9a1373cf03a821166e6291e10efdc7fff1ba1388dccb15f` |

实际门限与配置：默认录制 60 s、最大 1800 s、磁盘最低 2 GiB、IMU 460800 波特、UDP 2368、Qt 工作线程轮询 0.25 s、主机批次关联门限 0.1 s。配置随新会话记录在 `operator.json`。0.1 s 是开发用空洞检测门限，不是同步误差容限或采样误差上界。

## SYNTHETIC 回归

在 `D:\Code\radar\web` 执行：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_na70_qt_capture.py tests/test_local_capture_dual_stream.py tests/test_na70_capture.py tests/test_lidar_udp.py -q
```

最终 **58 passed in 8.15 s，进程退出码 0**。新文件 10 项测试验证：逐包保留来源、完整批次与边界包；部分关联状态；范围外、间隔大、序号中断、跨 segment；NaN/布尔值/倒退 IMU 时间；倒退雷达时间；目录不覆盖与原始 manifest 不改；真实录制核心配模拟串口和 loopback UDP 自动结束；关闭窗口请求停止、收尾和自动关联；启动失败保留文件并恢复按钮。

首次测试曾出现“断言通过但进程结束时 Windows access violation”：Qt 测试 fixture 在 QApplication 退出前未显式释放待销毁窗口。补齐 top-level widget 的 close/deleteLater 与 DeferredDelete 事件清理后，重新运行子集与完整相关集合退出均正常。未把此前的非零退出当通过。

## Windows 原生窗口与旧真实会话

在仓库根目录执行：

```powershell
web\.venv\Scripts\python.exe Plan\evidence\slam\20261105-qt-capture\verify_native.py
```

使用 Windows 原生 Qt 平台，显示窗口、读取已完成真实会话、后台生成关联、更新按钮与指标、保存截图、正常退出，进程退出码 0。结构化结果见 `native-workflow.json`；图像为 `qt-ready-native.png`、`qt-associated-native.png`。Qt 的 Windows offscreen 渲染曾显示方框字形（`qt-ready.png`），原生窗口的中英文均正常；可视检查以原生截图为准。

输入旧真实会话 `radar_capture_data/na70_sessions/20261104-170341`：

| 指标 | 实测 |
|---|---:|
| 有效雷达包 | 83,351 |
| 雷达 100 包分组（含不完整尾组） | 834 |
| IMU 帧 | 20,052 |
| 不同主机接收时刻／批次 | 164 |
| 异常雷达记录 | 0 |
| 主机端点交叠 | 99.639859 s |
| 主机括取且批次跨度 ≤0.1 s | 68 包 |
| 主机批次跨度过大 | 82,712 包 |
| IMU 范围外 | 571 包 |
| 最终关联状态 | `PARTIAL_HOST_ASSOCIATION` |

该旧会话反映修复前串口批量到达问题。未放宽门限，未按标称 IMU 频率补造时间。原始质量仍为 `degraded`，保留内核丢包计数未知的事实。命令行同一会话返回 3，表示部分关联，不表示程序崩溃。

输入 SHA-256：

- `raw/lidar.pcap`：`e0e4d9f73d4332f0ac40aad18f264d26d1cbb1888756c2d837ba828a71bc3128`，122,359,292 B。
- `imu_frames.jsonl`：`c661d5510be9e797e4d5fecd5dcf812bb0ab676f6b80957ddf629c454adee92b`，5,334,244 B。

原始会话不改动，派生 CSV 放在被 Git 忽略的 `radar_capture_data/na70_sessions/associations/`；报告保留实际路径、输出哈希和源文件哈希。

## 边界与下一步

这次交付验证软件录制流程与主机时间关联。当前本机串口枚举无可用 IMU，未强行开启新硬件录制。连接设备后需用新程序完成一次双路计数增长的短录，确认实际串口、雷达目的 IP、磁盘收尾与新接收批次间隔。设备时钟、缓冲/传输延迟与空间外参仍未知；报告保留 null，不生成冻结标定、不升级为可信动态 LIO。

# 雷达与 IMU Qt 一键录制

2026-11-05，Windows 本机桌面程序。复用 `na70_core.capture` 录制器与唯一正式 PCAP 解码器 `web/backend/decoder.py`，无需启动 WebUI、ROS2 或 Jetson 服务。

## 启动与操作

1. 双击仓库根目录 **`启动雷达IMU录制.bat`**。首次启动自动安装 Qt 依赖；优先复用 `web/.venv`，没有时用 Python 3.12 创建 `.venv-qt`。需能联网安装依赖。
2. 接上 IMU USB 和雷达网线，点击“刷新串口”。程序优先选上次串口或存在的 COM12；仅一个串口时自动选中；多个候选时请核对 IMU 端口。默认 460800 波特。
3. 确认雷达 UDP 目的 IP 是电脑与雷达相连的有线网卡地址，端口 **2368**。界面列出本机 IPv4 候选；多网卡时需核对，程序不发送设备配置命令。关闭占用 IMU 串口或 UDP 2368 的其他采集程序。
4. 默认录制 **60 秒**，可改为 1–1800 秒。点击“一键开始录制”，确认 IMU 帧数和雷达包数都增长。
5. 到时自动停止，也可点“停止并保存”。程序等待写盘与哈希完成，随后自动生成逐包关联表。关闭窗口也会先请求停止，保存、关联完成后再关闭。
6. 用“打开原始会话目录”和“打开关联结果目录”查看文件。可以通过“关联已有会话”处理含 `manifest.json` 的已完成会话；不会覆盖原文件。

默认原始目录：`radar_capture_data/na70_sessions/<session>/`。含雷达 `raw/lidar.pcap`、IMU `raw/imu.bin`、接收块 `raw/imu_chunks.jsonl`、`imu_frames.jsonl`、主机时间 CSV、质量与 SHA-256 清单。独占会话锁、磁盘检查、串口断开、写线程失败和 UDP 绑定失败均沿用已有核心；失败文件保留。

Windows UDP 接收的 PCAP 网络头为合成，原始载荷保留；内核丢包数未知，因此采集质量可能为 `degraded`。这不等于已确认丢包，也不等于同步或标定通过。

## 自动关联输出

关联结果写入独立目录 `radar_capture_data/na70_sessions/associations/<session>-<time>/`，不修改原始会话清单。每次处理建立新目录，已有目录禁止覆盖。

| 文件 | 内容 |
|---|---|
| `packet_imu.csv` | 每个有效雷达包一行，保留有效包索引、100 包帧索引、帧内包号、PCAP 载荷字节偏移、主机时间、前后 IMU 批次索引及 JSONL 行号范围 |
| `imu_batches.csv` | 同一主机到达时间的完整 IMU 批次、segment、首末 tid、帧数与序号连续性 |
| `association.json` | 来源路径与 SHA-256、输出文件哈希、包数、批次数、主机端点重叠、各类关联计数、异常记录及能力边界 |

IMU 行号从 **1** 开始；包、帧、批次索引从 **0** 开始。正式解码器按源顺序每 100 个有效包分组，最后不足 100 包也保留。一个批次内多帧共用主机接收时间，因此保留整批范围，不任挑一帧、不插值、不按标称 200 Hz 伪造时间。`previous` 与 `next` 是离线双侧括取，`next` 可晚于雷达包时间，不能作为实时因果输入。

| 包状态 | 含义 |
|---|---|
| `host_bracketed` | 前后主机批次存在，序号与 segment 连续，批次跨度在配置门限内 |
| `outside_imu_range` | 雷达包早于或晚于 IMU 范围；缺少的侧留空，不外推 |
| `host_gap` | IMU 前后主机批次跨度超过门限 |
| `segment_break` | 前后批次跨重连 segment |
| `sequence_break` | 批次内部或两批之间的 IMU 序号不连续 |

总状态区分 `HOST_ASSOCIATED`、`PARTIAL_HOST_ASSOCIATION`、`NO_USABLE_BRACKETS` 与 `failed`。主机时间倒退或非有限值直接失败并留报告；异常 PCAP 记录由正式解码器保留计数，不能因有效包可关联而忽略。

默认主机批次间隔门限为 **0.100 秒**，只是发现主机数据空洞的开发参数，不是经过验证的同步误差界。可在界面修改；其余配置集中在 `tools/na70_qt_capture.yaml`（JSON 兼容的 YAML），实际录制配置写进 `operator.json`。界面记住串口、时长与保存目录。

**当前自动完成的是同次会话管理与主机时间粗关联。设备采样时间、时钟偏移、漂移与真实空间外参仍待独立验证，报告中 `R_IL`、`t_IL_m` 与采样误差界保持 null。** 若需要自动解算外参，仍需按[标定工作流](NA70_CALIBRATION_WORKFLOW.md)取得可观测的标定数据；任意短录或单轴转动不能证明完成标定。

## 命令行与验证

```powershell
web\.venv\Scripts\python.exe tools\na70_qt_capture.py
web\.venv\Scripts\python.exe tools\na70_associate.py --session radar_capture_data\na70_sessions\<session>
```

关联 CLI 返回 0 表示所有有效包满足主机关联条件且无异常雷达记录；返回 3 表示部分关联或无可用括取；异常返回非零且保留失败报告。此退出码不证明硬件同步。

```powershell
cd web
.venv\Scripts\python.exe -m pytest tests/test_na70_qt_capture.py tests/test_local_capture_dual_stream.py tests/test_na70_capture.py tests/test_lidar_udp.py -q
```

Qt 测试需要安装 `tools/requirements-qt.txt`；没有 Qt 时仅跳过 GUI 测试，关联核心测试仍执行。模拟采集证据标为 **SYNTHETIC**，不替代硬件验收。界面与真实旧会话复核见[交付证据](../Plan/evidence/slam/20261105-qt-capture/REPORT.md)。

# NA70 SLAM 软件准备开发与验证

日期：2026-10-30。范围：T1–T7 软件准备。真实 NA70＋USB IMU 同步、标定、LIO/SLAM 与 Jetson 验收均未通过本报告升级。

## 1. 已交付代码

- `packages/na70_core/na70_core/lio.py`：时钟映射、原始 IMU CSV/单位/顺序/缺口/饱和检查、外参正逆、包级相对时间与源点引用、声明的时间误差预算。
- `tools/prepare_lio_session.py`：复用唯一 PCAP decoder 与既有包窗，生成不可覆盖的输入诊断包；缺时不回退主机时间，缺 IMU 不合成补齐。
- `tools/lio/preflight_default.yaml`：未知同步、外参、包时间误差、量程等保持 null，默认拒绝动态输入就绪。
- `tools/lio/upstreams.repos`、两份构建脚本、公开数据下载/回放和启动检查工具：源码固定、依赖隔离、日志追加、checksum/归档检查、无 GUI 回放。
- `web/tests/test_lio_inputs.py`：13 项 SYNTHETIC 契约用例，含真实 CLI 正/负路径、坏时间、单位转换、外参方向和非覆盖输出。

没有接入实际 USB 设备、没有增加第二套 Web 配准 API，也没有修改既有 PCAP 解码协议。NA70 的专用 ROS2 帧内时间 handler/数据桥接仍属后续工作；当前导出的是带元数据的输入数组，不宣称已经接通 NA70 动态 SLAM。

## 2. 验证结果

| 项目 | 环境与结果 | 证据 |
|---|---|---|
| 输入契约 | Windows 本机 Python 3.12，13 passed | `lio-input-tests.xml` |
| 后端全量回归 | Windows 本机，420 passed / 2 skipped / 2 既有依赖弃用警告，183.90 s | `pytest-final.xml` |
| 旧 NA70 `1.pcap` | 末帧 50、0.1 s 因果窗口；101 包 / 20,200 射线，2,204 点在 50 m 范围内；缺同步 IMU/标定等，正确 BLOCKED | `old-pcap-input-final/` |
| FAST-LIO2 构建 | WSL2 Ubuntu 22.04/Humble x86_64，Livox driver＋FAST-LIO 两包成功 | `fast-build.log` |
| FAST-LIO 启动/退出 | 独立 ROS domain、无输入，初始化成功、SIGTERM 正常退出 0 | `fast-startup/report.json` |
| GLIM CPU 构建 | WSL2/Humble，GLIM＋ROS2 wrapper 两包成功，CUDA/viewer 关闭 | `glim-build.log` |
| 公开数据 30 s 冒烟 | Ouster OS1-128＋IMU，进程退出 0，4 份 TUM 轨迹各 270 行、11 子地图，约 6.64 s | `public-smoke/report.json`、轨迹与 graph.txt |

代码、配置、命令及输入哈希见 [manifest.json](manifest.json)。未改前端，不重复做 UI 验收。合成检查只证明输入/拒绝契约，公开数据只证明当前环境与上游流程，均不能替代本套硬件验证。

## 3. 旧 PCAP 拒绝证据

`1.pcap` SHA-256：`b17d7c17926e670b3d89ba0774c4140feb810e4935971ab57760f4bdd00636cd`。

阻断项：`lidar_clock_unverified`、`imu_clock_unverified`、`packet_time_uncertainty_unknown`、`extrinsic_unknown`、`calibration_unverified`、`specific_force_not_confirmed`、`imu_missing`。

输出保留 XYZI、真实包级相对时间、原帧/包/点引用和原捕获/设备时间。`deskew_applied=false`，硬件同步、标定、LIO 和 SLAM claims 均为 false。旧 PCAP 只用于诊断；没有创建配套的假 IMU。

复现（仓库根目录；新输出路径）：

```powershell
web\.venv\Scripts\python.exe tools\prepare_lio_session.py `
  --pcap radar_capture_data\1.pcap --end-frame 50 --duration 0.1 `
  --evidence-kind REAL_PCAP_DIAGNOSTIC --output Plan\evidence\slam\new-old-pcap-audit
web\.venv\Scripts\python.exe -m pytest web\tests -q
```

## 4. WSL2 软件与公开数据

构建工作区：`/home/radar/na70_lio_ws`，均位于 Linux 文件系统。固定源码见 `tools/lio/upstreams.repos`，FAST-LIO 的 ikd-Tree 子模块精确版本见 `fast-submodules.txt`。实际安装的 GTSAM 4.3.0 / gtsam_points 1.2.2 / Iridescence 1.0.2 及 ROS PCL 版本见 `linux-packages.txt`；未安装 CUDA，也未改变现有 ROS 工程源码。

宿主机 CPU：AMD Ryzen AI 9 H 365（10 核 / 20 逻辑处理器）；WSL 可见内存约 20 GB。GLIM 回放设置 `OMP_NUM_THREADS=2`，CPU 里程计配置为 2 线程；这与 Orin Nano 4GB 的资源条件不同。

构建命令：

```bash
bash /mnt/d/Code/radar/tools/lio/build_fast_lio.sh /home/radar/na70_lio_ws
bash /mnt/d/Code/radar/tools/lio/build_glim.sh /home/radar/na70_lio_ws
```

公开数据来自 GLIM 作者 quickstart 链接的 [Zenodo 7233945](https://zenodo.org/records/7233945)，ROS2 下采样包 426,164,255 bytes。发布方 MD5 为 `75ef57e84010217ab8a5f8da511ff2ea`，下载 SHA-256 为 `c9ef22dfbeded4e3731a98804c0c9d4360f5039b418d7e2f5a19e85b55da7f94`，校验成功。整包时长 115.2107 s，IMU 11,525 条、点云 1,153 条，本次仅运行起始 30 s；不是全包或完整回环验收。

回放程序使用 CPU 里程计＋passthrough 子地图＋pose-graph 后端、作者示例外参和原点云/IMU topic，关闭扩展 GUI。精确参数及生成配置保存在 `public-smoke/`。输出完整约 25 MB 地图/图结构留在 Linux `public-smoke-30s-final/dump/`；仓库保存轨迹、子图计数、日志、配置和哈希，不复制原始 bag 或构建二进制。

本次公开数据进程报告有一条：

```text
not enough IMU measurements integrated between 508.4654704 and 508.56578615. N=0
```

所以 `input_quality_status=REQUIRES_REVIEW`，准确度 `NOT_EVALUATED`。没有把输出轨迹等同于几何精度或回环通过；也没有调整阈值来删除该警告。后续正式对照需定位该样本覆盖边界并与原始 IMU 时序对账。

`resources.txt` 的 GNU time 最大 RSS 为 634,052 KiB，是该次命令的 Linux 高水位口径，不是整个系统内存，也不是 Jetson 结果；约 6.64 s 为回放进程执行耗时（含初始化/优化/保存），不包括下载和编译，不代表设备到显示的实时延迟。

## 5. 初次失败与修正

1. 首次 Livox driver 构建缺少 `package.xml`。当前固定版提供 `package_ROS2.xml`；构建脚本在校验原源码后生成包清单，并传入 ROS2/Humble 参数。未使用会删除 build/install 的厂商 build.sh；初始失败日志保留在追加的 `fast-build.log`。
2. 原 WSL ROS 镜像返回 403。通过工作区临时 apt source 使用 USTC 镜像和同一 ROS 签名密钥安装缺失的 `ros-humble-pcl-ros`，未关闭签名检查，未更换用户原默认源。
3. GLIM PPA 初次 NO_PUBKEY：公钥位于不可由 `_apt` 穿越的 home 目录。核实指纹 `A0DAE9CF75CFEF37A509FCD3C2C44E47B79E8F3A` 后，放入可读系统 keyring，并继续使用 signed-by 验证；未允许 unsigned 包。
4. 作者备用下载地址只返回 78,524,908 bytes，不完整 gzip。失败文件保留在 Linux `public-example/example.tar.gz`，哈希见 `failed-download-sha256.txt`。改用 Zenodo 版本并加入发布方大小/MD5 校验后成功，不覆盖首次下载。
5. 初次经 ros2 launcher/timeout 的 SIGINT 启动检查到达初始化，但超时被强制结束；原日志保留在 `fast-startup/first-sigint-attempt.log`。改为直接运行固定二进制并以 SIGTERM 检查，初始化和正常退出 0 均通过。此处不宣称已解决上游所有 SIGINT 生命周期问题。

## 6. 当前边界与下一批

软件准备 T1–T7 完成；S0–S5 的真实硬件门禁不变。后续先实现经过门控的 ROS2 时间字段桥接，再接实际 USB IMU 协议/时钟和现场标定；新数据到位后，按“结构丰富小区域 → 基本动作 → 操场边缘 → 完整覆盖与回环”验证。

当前不能交付 NA70 实测轨迹、200 m 精度、完整操场地图或 Jetson 性能结论。

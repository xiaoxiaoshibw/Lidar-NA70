# NA70＋IMU 输入预检与上游构建

2026-10-30 开发版。输入预检不估计轨迹，不自动标定，不执行运动补偿。

## 任务与入口

[任务清单](../Plan/NA70_SLAM无数据开发任务.md)与[主计划](../Plan/NA70_雷达IMU_SLAM开发计划.md)分别记录软件准备和现场门禁。纯 Python 输入逻辑位于 `packages/na70_core/na70_core/lio.py`，只依赖 NumPy，不依赖 ROS。

已实现：设备时钟仿射映射；gyro/原始比力单位转换；IMU 顺序、缺口、饱和、窗口覆盖检查；刚性外参正逆转换；NA70 包级相对时间与原始 `(frame, packet, point)` 引用导出；时间误差预算检查。不能从输入中的“已声明”证据推断设备确实同步或已完成标定，因此所有硬件/SLAM 验证 claims 始终为 false。

## 输入格式

USB IMU 型号尚未知，先由实际设备驱动输出规范 CSV：

```csv
device_time_s,gx,gy,gz,ax,ay,az
```

一行一个原始采样，时间严格递增；不自动排序、填补、去重或从 USB 到达频率反推采样时间。源行号从 0 开始保存。`gyro_unit` 只支持 `rad/s` 或 `deg/s`；`accel_unit` 为 `m/s^2` 或 `g`，必须是包含重力响应的原始比力。设备接收日志另行保留，不能代替设备采样时间。

复制 `tools/lio/preflight_default.yaml` 后填写参数。文件使用 YAML 兼容的 JSON 语法，和现有项目配置一致。默认同步误差界、包时间误差、外参、标定依据、量程和比力确认均未知，因此默认必定拒绝准备就绪。

- 时钟：`reference = (device - source_origin_s) * rate + reference_origin_s`；两个设备必须映射到同一个参考时钟段，误差界单位为参考时钟秒。`evidence` 是依据标识，不是自动验证结果。
- 外参：`T_imu_lidar` 为 LiDAR → IMU 的 4×4 刚性矩阵；导出 FAST-LIO 正向矩阵及 GLIM 所需逆矩阵，不提前旋转原始点。
- `motion_envelope` 是声明的工作范围，速度/角速度未在预检中实测。按最大距离过滤输入点并统计过滤数，原始编号不变。
- 包内采样时间仍未知时，只能标记 `PACKET_APPROXIMATION`。同一包各点时间相同，不生成递增的假逐点时间。

## Windows 离线预检

从仓库根目录运行（示例输出目录必须不存在）：

```powershell
web\.venv\Scripts\python.exe tools\prepare_lio_session.py `
  --pcap radar_capture_data\1.pcap --end-frame 50 --duration 0.1 `
  --evidence-kind REAL_PCAP_DIAGNOSTIC --output Plan\evidence\slam\my-input-audit
```

没有 IMU 时仍可检查旧 PCAP 的源引用和包时间，但结果为 `BLOCKED`。有配套 IMU 后增加 `--imu-csv <file>` 和 `--config <file>`。`--evidence-kind` 必须明确为 `SYNTHETIC`、`PUBLIC_DATASET` 或 `REAL_PCAP_DIAGNOSTIC`。

输出：`manifest.json`（命令/输入与代码哈希/配置/环境）、`report.json`、`REPORT.md`，以及合法诊断云的 `inputs.npz`。后者包含 XYZI、相对时间秒、源点引用、原设备/捕获时间，以及有 IMU 时的标准单位样本/源行号。禁止 pickle 加载；使用 `np.load(path, allow_pickle=False)`。

状态 `INPUT_CONTRACT_PASSED` 仅表示所提供声明和当前输入通过契约检查；仍不代表同步、标定、LIO 或 SLAM 验收。缺失或非法输入返回 `BLOCKED`，CLI 退出码 2；准备就绪返回 0。已有输出目录直接拒绝，不覆盖历史记录。

## Humble 构建

固定源码版本记录在 `tools/lio/upstreams.repos`；独立 Linux 工作区示例为 `/home/radar/na70_lio_ws`。先准备 ROS2 Humble、vcs/PyYAML、PCL/Eigen 和 ROS PCL 依赖。GLIM CPU 还需要与固定源码兼容的 GTSAM、gtsam_points、Iridescence、METIS；实际版本随证据记录，不能将“最新版”当可复现锁定。

```bash
bash /mnt/d/Code/radar/tools/lio/build_fast_lio.sh /home/radar/na70_lio_ws
bash /mnt/d/Code/radar/tools/lio/build_glim.sh /home/radar/na70_lio_ws
```

脚本在 Linux 文件系统构建，校验源码 commit/remote/工作树，不重置已有改动。Livox SDK 安装到工作区前缀，不写系统 SDK；ROS2 `package.xml` 从上游模板生成，不调用会递归清理目录的上游 `build.sh`。构建日志追加保存，依赖包安装与构建分开执行。

公开示例下载：

```bash
python3 /mnt/d/Code/radar/tools/lio/fetch_public_example.py /home/radar/na70_lio_ws/public-zenodo-example
```

构建后在已加载 Humble/工作区环境的 Linux shell 中验证：

```bash
source /opt/ros/humble/setup.bash
source /home/radar/na70_lio_ws/install/setup.bash
export ROS_LOCALHOST_ONLY=1 OMP_NUM_THREADS=2
python3 /mnt/d/Code/radar/tools/lio/check_fast_lio_startup.py \
  --workspace /home/radar/na70_lio_ws --output /home/radar/na70_lio_ws/new-startup-check
python3 /mnt/d/Code/radar/tools/lio/run_glim_example.py \
  --workspace /home/radar/na70_lio_ws \
  --bag /home/radar/na70_lio_ws/public-zenodo-example/bag/os1_128_01_downsampled \
  --output /home/radar/na70_lio_ws/new-public-smoke --duration 30.0
```

回放工具复制配置到独立输出目录，使用作者示例外参、CPU 里程计/位姿图、关闭 GUI；保存配置哈希、日志、资源统计、轨迹和质量警告。公开示例当前有一条 `not enough IMU measurements ... N=0` 警告，故输入质量仍需复核，不能将进程正常退出当精度通过。运行结果见[开发证据](../Plan/evidence/slam/20261030-development/REPORT.md)。

当前固定 Zenodo Ouster OS1-128＋IMU 的 ROS2 下采样示例，下载后验证发布方 MD5/大小并保存 SHA-256，再检查归档路径和展开大小。这是上游环境对照，不是 NA70 非重复扫描验收；FAST-LIO/GLIM 的 NA70 实测接入与现场标定仍需后续完成。所有构建/回放证据标记 `WSL2_HUMBLE_X86_64`，不替代 Jetson/ARM。

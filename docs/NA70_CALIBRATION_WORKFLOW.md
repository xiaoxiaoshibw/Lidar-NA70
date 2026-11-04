# NA70 标定与场景重建离线工具链

2026-10-30 开发版。所有命令在仓库根目录用 `web\.venv` 运行。当前状态：软件与 SYNTHETIC 回归通过；NA70/YESENSE 现场标定、时间同步、SLAM 与真实场景准确度仍为 `NOT_EVALUATED`。

这套工具只做“检查、拟合、装配、出报告”。原始采集仍由采集服务完成；阈值仍集中在 `tools/lio/static_check.yaml` 与 `tools/lio/preflight_default.yaml`，不在代码里写死。任何一步失败都保留失败记录，不自动放宽阈值。

## 0. 数据前提与会话结构

一次采集会话至少包含：

```text
<session>/
  manifest.json          # 采集任务、设备、窗口、文件哈希
  imu_frames.jsonl       # t_mono, t_unix, segment, accel, gyro, ...
  imu_host_receive.csv   # 仅主机接收时间，不冒充设备时间
  raw/imu.bin
  raw/imu_chunks.jsonl
  raw/lidar.pcap
  quality.json
  events.jsonl
```

`t_mono` 是主机单调时钟；`segment` 在断流/重连时递增。没有设备采样时间时，所有速率、窗口和 Allan 结果都是主机口径，不得写成设备口径。跨 segment 的连续运动不能直接拼接。

## 0.1 一键标定批采入口（等待现场数据）

现场数据通过两个双击入口采集；两者都要求先用 `启动标定连接.bat`（或 `tools/start_capture_tunnel.ps1`）把本机 8791 转发到 Jetson 8792 采集服务。采集期间数据只落 Jetson，结束后自动 `scp` 回 `radar_capture_data/na70_sessions/`：

- `开始外参采集.bat` → `tools/capture_na70_rotation.ps1`：单次 80 s `CAL-ROT`（正对大平墙的慢速三轴转动，节奏见屏幕提示）。**墙面距离 ≥8 m、建议 9–15 m**：实测有效回波最近只有 7.488 m（距离码 468 下限，四段不同数据一致），2–5 m 的墙拿不到任何点，见[近距下限审计](evidence/slam/20261030-near-range-floor/REPORT.md)。
- `开始全量标定采集.bat` → `tools/capture_na70_calibration_batch.ps1`：`CAL-STATIC` 120 s + `CAL-ROT` 80 s + 六面各 25 s，逐段确认后统一回传，写批次清单 `calibration-batch-<时间>.json`（schema `na70-calibration-batch-v1`，含各会话本地路径与会话 id）。

采集服务接受 3–1800 s。任一会话以服务端 `failed` 终态结束时脚本立即报错退出（不自动重试、不挂死），Jetson 上的会话保留给 AI 检查。六面朝向以轴符号记录在任务名（`CAL-FACE-+z` 等）；用户按[采集卡](../Plan/NA70_YESENSE_用户采集卡.md)的实体朝向摆放，不需要自己判断轴符号。

## 1. IMU 运行检查（B1 试录 / B2 静置）

```powershell
web\.venv\Scripts\python.exe tools\analyze_na70_imu_run.py `
  radar_capture_data\na70_sessions\<session>\imu_frames.jsonl `
  --output Plan\evidence\slam\<run>\imu-run.json
```

输出：

- `static_windows`：按 10 s 窗口给出的 `STATIC_LIKE / REJECTED / INSUFFICIENT_DATA`，含失败项。
- `segments`：样本数、主机跨度、`median_positive_dt_s`（只对正间隔取中位）、`effective_arrival_hz`、`duplicate_host_times`、最大间隔、超过 1.5 倍中位间隔的缺口数。
- `allan`：仅当所选 segment 的所有窗口都 `STATIC_LIKE` 且跨度 ≥ 60 s 时计算。时间严格递增时按采样序列计算；主机时刻重复时改用批次平均序列，标 `HOST_BATCH_AVERAGED`（批次约 50 ms，只反映主机尺度慢漂移，不能当设备噪声密度）。不满足条件时为 `NOT_EVALUATED` 并写明原因（运动段、短录或批次数不足）。

USB 批量到达会让多帧共享同一主机时刻。此时原始中位间隔为 0，不能据此推断设备采样周期；工具改报正间隔中位与重复时刻数。已实测 60 s 工程会话：12,018 帧中 10,838 个重复主机时刻，正间隔中位 50.9 ms（约 19.6 次到达/秒），说明主机接收时间不能用于毫秒级对齐。

退出码 0 只表示 `STATIC_LIKE`，不表示物理静止、同步或标定通过。短录的 Allan 表不能当偏置稳定度指标。

若录制开头有拿起/放置动作，可用 `--start-s`（相对首样本秒）跳过；切片后仍按同一阈值检查，不能靠跳过把运动段变成静置段。长静置时可用 `--end-s` 去掉结束前的触碰。

旧的单文件检查仍保留：

```powershell
web\.venv\Scripts\python.exe tools\check_na70_imu_static.py <imu_frames.jsonl>
```

### 1.1 时间基审计

```powershell
web\.venv\Scripts\python.exe tools\audit_na70_imu_timebase.py `
  --frames radar_capture_data\na70_sessions\<session>\imu_frames.jsonl `
  --chunks radar_capture_data\na70_sessions\<session>\imu_chunks.jsonl `
  --output Plan\evidence\slam\<run>\timebase.json
```

报告：帧数、唯一主机时刻（批次数）、每批帧数、批次间隔中位/P95/最大、是否出现 `sample_us`/`ready_us`、tid 断号与估计缺失帧、时间等级。

实测 60 s 工程会话：12,018 帧 / 1,180 批，每批 10–11 帧，批次间隔中位 50.9 ms、最大 53.0 ms，无设备时间字段，时间等级为 **C（仅主机接收）**。按 `e ≈ (v + r·|ω|)·δt`，53 ms 在 `v=0.5 m/s, r=50 m, ω=10°/s` 下约 0.49 m，不能支撑动态 B5 或移动 SLAM 的时间对齐。静态 B1–B4 不受毫秒级同步阻挡，但也不能给出时间偏移。

### 1.2 设备时间 CSV 导出（仅当 0x51 存在）

当前固件不输出 0x51/0x52，所以**现在不应生成** `imu_device.csv`。若以后固件或厂家上位机配置开始输出采样时间，采集侧会自动把 `sample_time_field` 标为 true，时间等级记为 `device_field_present_unverified`（仍需验证语义，不等于同步）。此时用：

```powershell
web\.venv\Scripts\python.exe tools\export_na70_imu_device_csv.py `
  --frames radar_capture_data\na70_sessions\<session>\imu_frames.jsonl `
  --segment 0 `
  --output radar_capture_data\na70_sessions\<session>\imu_device.csv
```

输出严格七列 `device_time_s,gx,gy,gz,ax,ay,az` 和同名 `.meta.json`。拒绝条件：

- 任一所选帧缺少 `sample_us`；
- 多个 segment 未用 `--segment` 指定；
- 32 位回绕之外的倒退，或展开后时间不严格递增。

`device_time_s` 以该 segment 首个 0x51 样本为原点，不是主机时间；陀螺/加速度值按采集单位原样复制，不做换算。导出的 CSV 可交给 `tools/prepare_lio_session.py --imu-csv`，但仍需在配置里声明单位、时钟模型和不确定性。

### 1.3 量程与饱和诊断

```powershell
web\.venv\Scripts\python.exe tools\audit_na70_imu_ranges.py `
  --frames radar_capture_data\na70_sessions\<session>\imu_frames.jsonl `
  --gyro-range-dps 200 --accel-range-m-s2 16 `
  --output Plan\evidence\slam\<run>\ranges.json
```

量程未知时省略两个 `--*-range`，只报告逐轴 min/max/p01/p99/max_abs、数值满量程（±2147.483648）和重复极值平台；报告会明确“不能证明物理饱和”。已知量程时按 98% 阈值找连续段。实测 60 s 工程会话无饱和事件：加速度 z 约 9.35–10.16 m/s²，角速度最大约 2.24 °/s；静置窗口拒绝与饱和无关。

### 1.4 一键会话诊断

```powershell
web\.venv\Scripts\python.exe tools\report_na70_session.py `
  --session radar_capture_data\na70_sessions\<session> `
  --output Plan\evidence\slam\<run>
```

一次运行时间基、量程/饱和、静置窗口、陀螺单位一致性、PCAP 主机端点重叠，输出 `report.json` 与 `REPORT.md`。报告给出 `time_level`、`static_baseline_usable`、`may_be_used_for_dynamic_calibration` 和原因；不会因为端点重叠或某个窗口通过就升级同步/标定结论。实测 60 s 工程会话：`REJECTED_NOT_STATIC`，时间等级 C，端点重叠 59.977 s，6 个窗口全部拒绝；见 [会话诊断](evidence/slam/20261030-session-report/REPORT.md)。

### 1.5 陀螺单位一致性

```powershell
web\.venv\Scripts\python.exe tools\verify_na70_gyro_unit.py `
  --frames radar_capture_data\na70_sessions\<session>\imu_frames.jsonl `
  --output Plan\evidence\slam\<run>\gyro-unit.json
```

用设备四元数增量与同区间平均陀螺对照：比例≈1 支持 deg/s，≈57.3 提示 rad/s。实测 60 s 振动会话为 `INCONCLUSIVE`：0.051 s 间隔比例 0.284、方向余弦 0.269；拉到 0.255 s 后余弦 0.479、比例仍 0.261。融合姿态明显被平滑，不能作为原始陀螺基准；陀螺单位仍是推断 deg/s。该工具应在 B5 平滑转动数据或独立角速度基准上重跑。

## 2. 六面加速度计检查（B3）

先录六段朝向稳定的窗口，再写一个窗口清单：

```json
{
  "windows": [
    {"face": "+z", "segment": 0, "start_s": 0.0, "end_s": 20.0},
    {"face": "-z", "segment": 0, "start_s": 20.0, "end_s": 40.0}
  ]
}
```

`start_s` / `end_s` 是相对于该 segment 第一个样本的主机秒。六个面为 `+x/-x/+y/-y/+z/-z`；`z+` 这类写法也接受。

```powershell
web\.venv\Scripts\python.exe tools\solve_na70_six_face.py `
  --frames radar_capture_data\na70_sessions\<session>\imu_frames.jsonl `
  --windows Plan\evidence\slam\<run>\six-face-windows.json `
  --output Plan\evidence\slam\<run>\six-face.json
```

模型为 `a_meas = M @ (g * d) + b`，`d` 是该面朝上的机体方向。求解给出 `M`、`bias_m_s2`、尺度、轴间项、残差和设计矩阵秩/条件数。

拒绝条件（任一即 `INSUFFICIENT_DATA`）：

- 任一面窗口不足 9 s 或样本数不足；
- 任一面窗口运动检查失败；
- 六个方向不全、重复或设计矩阵秩不足；
- 残差超过 `max_residual_m_s2`（默认 0.5 m/s²）。

结果只覆盖加速度计。陀螺尺度/轴间误差需要独立角速度基准，不能由六面得到。

六面来自批采的独立会话时，不需要窗口清单，直接用 4.2 节的 `solve_na70_six_face_sessions.py`。

## 3. 墙面测距与平面一致性（B4）

先在点云里确认目标墙面的 ROI（LiDAR 系，米），再对帧区间拟合：

```powershell
web\.venv\Scripts\python.exe tools\analyze_na70_lidar_planes.py `
  --pcap radar_capture_data\na70_sessions\<session>\raw\lidar.pcap `
  --start-frame 0 --end-frame 100 `
  --roi=-5,5,-5,5,-3,3 `
  --reference-distance-m 10.0 `
  --output Plan\evidence\slam\<run>\plane-10m.json
```

`--roi` 必须以 `=` 连接，否则负号会被 PowerShell/argparse 当成选项。输出：

- 平面法向、质心、法向距离、内点率、残差 RMSE/P95、切向跨度；
- 若给参考距离，给出 `measured - reference` 误差。

注意：法向距离包含雷达光学原点未知量。原点未独立测量时，截距不能单独解释为测距零偏；低残差也只证明点云自洽。

多距离拟合：先对每个距离各跑一次上面的命令，再把多份 JSON 交给：

```powershell
web\.venv\Scripts\python.exe tools\fit_na70_range_model.py `
  --planes Plan\evidence\slam\<run>\plane-5m.json Plan\evidence\slam\<run>\plane-10m.json Plan\evidence\slam\<run>\plane-20m.json `
  --output Plan\evidence\slam\<run>\range-model.json
```

少于 3 个距离返回 `NEEDS_MORE_DATA`；3 个及以上给出斜率、截距、标准误和残差。若各 JSON 含 `normal_unit`，还输出同靶面法向两两夹角的最大/平均值——这是角域一致性，不是绝对角度校准。截距仍混合光学原点，不能单独称为测距零偏；本拟合也不是内部参数重标定。

## 4. 旋转外参与时间偏移（B5 前置）

这一步的输入是**已对齐的两路角速度**，单位为 rad/s：

```text
pairs.npz
  t_lidar (N,)      omega_lidar (N,3)
  t_imu   (M,)      omega_imu   (M,3)
```

LiDAR 角速度必须来自可追溯的配准、转台或真值，不能凭空生成。若当前只有原始 PCAP，应先完成配准/里程计，或使用合成数据做数学验证。

```powershell
web\.venv\Scripts\python.exe tools\solve_na70_lidar_imu_rotation.py `
  --pairs Plan\evidence\slam\<run>\pairs.npz `
  --output Plan\evidence\slam\<run>\rotation.json
```

模型：`omega_imu(t) = R_IL @ omega_lidar(t - tau) + b_g`。

- 旋转用 Wahba/SVD 闭式解；
- `tau` 先在 `[-max-offset, +max-offset]` 粗搜，再在最优附近细搜；
- 报告角度、四元数、欧拉角、残差、匹配样本数、时间偏移及网格分辨率；
- 角速度散布矩阵的最小/最大特征值比低于 0.02 时判 `INSUFFICIENT_MOTION`，不输出可用外参。

纯旋转观测不到 `t_IL`。不要从该结果推断平移。

### 4.1 平面法向 R_IL＋时间偏移求解器（Windows 本地，等待大平墙数据）

墙距提醒：有效回波最近 7.488 m，采集时墙面至少放 8 m、建议 9–15 m（[近距下限审计](evidence/slam/20261030-near-range-floor/REPORT.md)）。

不需要预先配准：对每个选定 LiDAR 帧做 RANSAC 平面拟合得到 LiDAR 系法向，用设备四元数（wxyz）插值到帧时刻，联合求解 `R_IL` 与时间偏移。要求 ≥6 个可用平面、法向三方向散布足够、IMU 时间覆盖帧区间：

```powershell
web\.venv\Scripts\python.exe tools\calibrate_na70_rotation_planes.py `
  --pcap radar_capture_data\na70_sessions\<session>\raw\lidar.pcap `
  --imu-frames radar_capture_data\na70_sessions\<session>\imu_frames.jsonl `
  --frame-step 5 --max-offset 0.2 --step 0.01 `
  --output Plan\evidence\slam\<run>\rotation-planes.json
```

- 状态门禁：`SOLVED_CANDIDATE` 要求残差 RMS ≤1° 且法向散布特征值比 ≥0.02；主导平面跳变计入 `plane_jumps`。
- 静止会话正确判 `INSUFFICIENT_MOTION`：v4 60 s 会话 10 个平面、axis_coverage 1.1e-05，见 [result.json](evidence/slam/20261030-plane-extrinsic-static/result.json)。
- SYNTHETIC 回归：合成墙面＋真值 R_IL／零偏移可恢复（R 角误差 <0.5°、时间偏移落在网格步长内），覆盖在 `web/tests/test_na70_calibration_batch_report.py` 的端到端用例中。
- 时间偏移是 LiDAR 帧时刻相对 IMU 四元数时刻的公共偏移，分辨率即 `--step`；会话时间等级仍是 C（主机接收），解出的偏移不升级时间同步结论。
- 平移 `t_IL` 一律 null，必须机械测量；本工具不生成冻结标定。

### 4.2 批次本地处理与六面分会话求解

```powershell
web\.venv\Scripts\python.exe tools\report_na70_calibration_batch.py `
  --batch radar_capture_data\na70_sessions\calibration-batch-<时间>.json `
  --output Plan\evidence\slam\<run>
web\.venv\Scripts\python.exe tools\solve_na70_six_face_sessions.py `
  --batch radar_capture_data\na70_sessions\calibration-batch-<时间>.json `
  --output Plan\evidence\slam\<run>\six-face-sessions.json
```

- `report_na70_calibration_batch.py` 读取批次清单，依次跑静置（第 1 节）、六面（第 2 节，按分会话）与旋转（4.1 节），输出 `summary.json`/`static.json`/`six-face.json`/`rotation.json`/`REPORT.md`。整批状态：任一阶段 `INVALID_DATA` → `INVALID`；旋转 `SOLVED_CANDIDATE` → `ROTATION_READY_TRANSLATION_PENDING`；否则 `PARTIAL`。旋转阶段缺 `raw/lidar.pcap` 记 `MISSING_PCAP`。
- `solve_na70_six_face_sessions.py` 与第 2 节同一 `a = M(g d) + b` 模型，但每个面来自独立会话（批次清单 `sessions.FACES`）；任一面不足 9 s、非静置或方向缺失，整批判 `INSUFFICIENT_DATA` 并列出原因。
- 两者都不填平移、不生成冻结标定；输出目录已存在时拒绝写入（`INVALID_DATA`）。

## 5. 外参候选合成

平移必须来自光学原点到 IMU 测量原点的实测（或以后独立验证的估计）：

```powershell
web\.venv\Scripts\python.exe tools\compose_na70_calibration.py `
  --rotation Plan\evidence\slam\<run>\rotation.json `
  --translation-mm 43,0,-60 `
  --translation-uncertainty-mm 2 `
  --source-note "卡尺测量，光学窗中心到 IMU 板卡标记" `
  --output Plan\evidence\slam\<run>\calibration_candidate.json
```

输出 `T_imu_lidar`（LiDAR→IMU）与逆矩阵，状态为 `CANDIDATE`。只有 A8 的留出动作和墙面/立柱对照通过后，才允许另存 `calibration_frozen.yaml`；本工具不会自动生成冻结文件。

## 6. 场景重建（位姿装配）

输入位姿 CSV，列顺序固定：

```csv
t_s,tx,ty,tz,qx,qy,qz,qw
0.000,0,0,0,0,0,0,1
0.100,0.05,0,0,0,0,0.0087,0.99996
```

- `t_s` 必须与 PCAP 的 `packet_capture_time` 同一时钟；时钟映射由位姿生产者负责并在报告里声明。
- 四元数为 `x,y,z,w`，表示 `T_map_body`。
- `--pose-frame imu` 时必须给 `--calibration`；`--pose-frame lidar` 时不需要外参，也不接受外参。

```powershell
web\.venv\Scripts\python.exe tools\build_na70_scene.py `
  --pcap radar_capture_data\na70_sessions\<session>\raw\lidar.pcap `
  --poses Plan\evidence\slam\<run>\trajectory.csv `
  --pose-frame imu `
  --calibration Plan\evidence\slam\<run>\calibration_candidate.json `
  --voxel-size 0.05 --max-gap-s 0.2 `
  --output Plan\evidence\slam\<run>\scene
```

输出 `map.ply`、`trajectory.csv`（实际使用的位姿，TUM 列序）、`report.json`、`REPORT.md`。每个地图点保留 `source_frame`（来源帧号）和体素观测数；体素代表点是该体素内首个实测点，不是质心。

明确边界：

- 本工具不做配准、回环或位姿优化；位姿错误会原样进入地图。
- 逐点时间由包接收时间复制，是 `POSE_INTERPOLATED_AT_PACKET_TIME`，不是已验证的逐点设备时间。
- 超出轨迹覆盖或跨缺口的点被丢弃并计数，不插值外推。
- 地图准确度在独立控制点对账前为 `NOT_EVALUATED`。

### 6.1 地图质量评估

```powershell
web\.venv\Scripts\python.exe tools\evaluate_na70_map.py `
  --map Plan\evidence\slam\<run>\scene\map.ply `
  --reference Plan\evidence\slam\<ref-run>\scene\map.ply `
  --planes Plan\evidence\slam\<run>\control-planes.json `
  --control-points Plan\evidence\slam\<run>\control-points.csv `
  --threshold-m 0.10 `
  --output Plan\evidence\slam\<run>\map-evaluation.json
```

三类参考可任选其一或组合：

- `--reference`：另一份同坐标系地图，做双向最近邻一致性；低值只说明两次重建一致，不排除共享系统误差。
- `--planes`：`[{"name":"ground","point":[x,y,z],"normal":[nx,ny,nz],"bounds":{"minimum":[...],"maximum":[...]}}]`，做法向残差；**每个平面应给独立 bounds**，否则其它墙面/地面点会被算进该平面（合成验证中未给 bounds 时 ground P95 从 0.010 m 变成 3.66 m）。参考平面的来源与不确定度必须记录。
- `--control-points`：CSV `x,y,z`，计算每个控制点到最近地图点的距离；控制点有限时不代表整图精度。

工具不做配准、不缩放；两侧必须已在同一坐标系。结果只写 `REFERENCE_CONSISTENCY`/`PLANE_RESIDUALS`/`CONTROL_POINT_DISTANCE`，不写绝对准确度通过。

## 7. LIO 扫描包导出

将一段 PCAP + IMU 帧导出为 LIO 适配器可读的包：

```powershell
web\.venv\Scripts\python.exe tools\export_na70_lio_scans.py `
  --pcap radar_capture_data\na70_sessions\<session>\lidar.pcap `
  --imu-frames radar_capture_data\na70_sessions\<session>\imu_frames.jsonl `
  --start-frame 1 --max-frames 50 `
  --calibration Plan\evidence\slam\<run>\calibration_candidate.json `
  --output Plan\evidence\slam\<run>\lio-package
```

产物：

- `scans.npz`：`points`(N,4)、`point_time_s`（相对首包）、`frame_index`、`packet_index`、`point_index`、`frame_start_s/frame_end_s`、`frame_offsets`；可用 `na70_core.lio_scans.load_npz`（禁用 pickle）。
- `imu.npz`：窗口内主机时间、`gyro_deg_s`、`accel_m_s2`、`segment`、可选的 `device_time_s` 与 `has_device_time`。
- `manifest.json`：时间质量 `PACKET_APPROXIMATION`、时间源、外参、阻塞项、文件哈希。
- `REPORT.md`。

关键纪律：

- 每包 200 点复制同一包时间；点数不等于 `200 × 包数` 直接拒绝，不补点、不截断。
- `lio_ready` 恒为 false；`block_reasons` 会列出 `time_level_C`、`calibration_missing`/`calibration_candidate_unverified`、`motion_envelope_unverified`、`imu_does_not_bracket_scan`、`packet_gap_over_limit`。
- 真实 60 s 会话前 5 帧 smoke：100,000 点、500 包、最大包间隔 1.147 ms；IMU 首样本比首雷达包晚 **19.204 ms**，因此该段判不覆盖，见 [LIO 包 smoke](evidence/slam/20261030-lio-package-smoke/REPORT.md)。
- 导出包不是 SLAM 结果；消费方仍须实现 NA70 专用 handler 并验证时间/外参/运动包络。

WSL2/Humble 原型已把该包接到固定 FAST-LIO（补丁 `ROS2/src/na70_lio/patches/fast_lio_na70_handler.patch`）：FAST-LIO 能消费 5 帧真实包并完成 IMU 初始化与一次地图更新，但仅 1 行轨迹、无 PCD，FastDDS 发现失败需 CycloneDDS，短包投递跑次间不稳定，时间等级 C/单位外参，状态 `PROTOTYPE_CONSUMED_5_FRAMES / NO_VALID_TRAJECTORY / NOT_EVALUATED`。详见 [原型报告](evidence/slam/20261030-na70-fastlio-prototype/REPORT.md)。

## 8. SYNTHETIC 回归证据

```powershell
web\.venv\Scripts\python.exe tools\run_na70_calibration_synthetic.py `
  --output Plan\evidence\slam\20261030-calibration-synthetic
web\.venv\Scripts\python.exe tools\run_na70_scene_synthetic.py `
  --output Plan\evidence\slam\20261030-scene-synthetic
```

两份证据都显式标记 `SYNTHETIC`，只验证数学与装配路径，不是设备能力或现场精度。

## 9. 测试

```powershell
cd web
.\.venv\Scripts\python.exe -m pytest tests\test_na70_calibration_core.py tests\test_na70_calibration_tools.py tests\test_na70_calibration_batch_report.py tests\test_na70_six_face_sessions.py tests\test_na70_plane_extrinsic.py tests\test_na70_scene.py tests\test_na70_scene_tool.py tests\test_na70_map_quality.py tests\test_na70_imu_static.py tests\test_na70_host_overlap.py -q
```

新增阈值仍放入 YAML/JSON 配置；任何失败都保留原始输出，不通过“调宽阈值”把失败改成通过。

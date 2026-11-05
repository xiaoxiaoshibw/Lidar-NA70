# 20261104-170341 双流只读审计

## 实测事实及口径

- 主机会话 `2026-11-04 17:03:41` 开始，`quality.json` 给采集进程运行时长 100.343 s，进程运行窗口交叠 100.281 s；它们均不是有效数据端点时长。采集状态为 `degraded`：UDP 内核丢包计数不可得。两个 `received` 和 `captured` 数均来自应用接收，不能用于推算丢包。
- [清单](inventory.json) 独立计算全部 10 个文件大小和 SHA-256，其中 9 个由 manifest 列出且全部一致；manifest 自身未列入，已另计算哈希。[完整性审计](integrity.json) `ok` 仅表示文件对账合格。源码版本、解码器和工具哈希与环境见 [provenance.json](provenance.json)；本记录不改动源会话。
- [IMU 原始重放](imu-replay.json) 将 1,343,488 字节和 164 个块重放：20,052 帧逐项匹配、校验错误 0、载荷错误 0、序号断裂 0、重连 0；20052 × 67 = 1,343,484 字节，末尾尚有 4 字节解析器残留，不把它解释为已完整采样或零丢帧。该复核复用采集解析器，不是独立协议认证。质量文件的 4 discarded bytes 与重放的 4 pending bytes 是停止时残留的两种统计口径。
- [标准解码器扫描](observations.json) 83,351 个有效 UDP 载荷、0 异常、0 忽略；按代码每 100 个包机械分组为 833 组完整、1 组 51 包。所谓完整仅指分组内计数，不能证实设备物理扫描完整，更不能证明网络无丢包。PCAP 是 UDP 应用载荷加程序合成 Ethernet/IP/UDP 头，不是真实网卡链路层抓帧。
- 从第一个到最后一个有效雷达包的主机时刻跨度 100.306704 s；首至末 IMU 块跨度 99.639859 s；二者端点相交 99.639859 s。雷达主机首末 Unix 秒 `1793783021.648630`、`1793783121.955334`；IMU 接收块首末 `1793783022.269308`、`1793783121.909167`，见 [overlap.json](overlap.json)。端点交叠不证明期间连续有效或采样同步。
- 雷达主机时间按首次包开始的每个完整秒计数 800–857 包，100 个完整秒及末不足一秒的计数见 `observations.json`。按 `(N-1)/(末包-首包)` 计算为 830.951 包/s；相邻接收间隔 P50/P95/P99/最大为 1.004/2.437/3.544/27.931 ms，有 8,113 个相邻接收时间相等、0 个倒退，超 5 ms 176 次、超 10 ms 13 次。主机调度及缓冲会影响这些数。
- 同一 Windows UDP 采集方式的 `20261104-161220` 短录，9,552 有效包、端点速率 916.287 包/s；旧 Jetson 录制也有约 1000 包/s 的观察。不同会话的设备状态、网络负载和接收路径未控制，因此速率、间隔及差值均不能给出丢包数或原因。应以后续同设备同配置下，使用网卡/驱动计数和旁路抓包对照发包及接收路径。
- 驱动将 1410 字节载荷末尾的 8 字节按设备内部秒及毫秒解析。本次全部包可解析为 28.494→128.807 s，未见倒退；相邻尾字段差 1 ms 69,289 次、2 ms 11,657 次、3 ms 1,980 次，最大 8 ms，分布见 `observations.json`。字段解析成功不等于已核实传感器采样时刻、绝对纪元或与主机/IMU 时钟的映射。驱动里的逐点 `offset_time` 由点序号推算，不能作为已验证的逐点设备时间。

## IMU、动作和时间的限制

- [修正后的时间基结果](timebase-corrected.json)：20,052 帧仅 164 个主机接收时刻，19,888 个相邻帧时刻重复；每批 122–123 帧，批次正间隔中位 0.609 s、最大 0.625 s（`t_mono`），块 Unix 时间最大间隔 0.615829 s。0x51 `sample_us` 和 0x52 `ready_us` 均未出现，等级 `C_HOST_RECEIVE_ONLY`。先前工具输出 [timebase.json](timebase.json) 曾把批次间隔称为“保守上界”，该说法需要额外假设采样只落在相邻到达间隔、串口无更早缓冲和延迟有界；本会话没有验证这些假设，不能将其视为无条件的采样时间或同步误差上界。旧结果保留用于追溯，已修正工具并另存 [一键汇总](session-report-corrected/report.json)。
- 雷达的主机接收时间和 IMU 批量接收时间均是主机时间，可作约秒级窗口上的粗关联；无法确认单帧采样时刻、可信的两设备时间偏移或毫秒级同步，不能支持可信运动补偿和动态 LIO。未生成批内插值时间或把主机时间充当采样时间。
- 用户说明拍摄操场，大致做了旋转；IMU 角速度/设备姿态变化支持存在角运动的诊断，特别是主机接收窗口约 80–100 s。没有独立位姿参考，不足以判断是否严格原地、是否平移，也不足以据此断言标定可观测性。10 个静态诊断窗口全部拒绝（包括信号波动和主机时间间隔），见 [imu-static.json](imu-static.json)；无法由此把设备故障作为原因，也不能建立静置偏置或 Allan 基线。
- [ranges.json](ranges.json) 未检测到数值满量程或重复极值；用户确认品牌为 WHEELTEC（轮趣），仓库 `tools/imu_live/photos/top.png` 可见丝印，具体产品子型号及物理量程未核实，不能排除实际饱和。质量清单将加速度记为 m/s² 并标 `verified`、角速度和欧拉角单位分别标推断 deg/s、deg；本审计没有独立仪器复验轴向、单位或量程。陀螺与设备四元数对照因批量时间间隔超默认 0.2 s，结果 [INSUFFICIENT_DATA](gyro-unit.json)，不得据此宣称单位已验证。

## 可复现命令

在仓库根目录的 PowerShell 中，创建一个不存在的新输出目录，将以下示例 `$out` 替换成该目录；所有命令只读原始会话：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$s = 'radar_capture_data/na70_sessions/20261104-170341'
$out = 'Plan/evidence/slam/NEW-AUDIT'
New-Item -ItemType Directory $out
.\web\.venv\Scripts\python.exe tools\audit_na70_session.py $s > "$out/integrity.json"
.\web\.venv\Scripts\python.exe tools\audit_na70_imu.py $s > "$out/imu-replay.json"
.\web\.venv\Scripts\python.exe tools\audit_na70_overlap.py "$s/raw/lidar.pcap" "$s/raw/imu_chunks.jsonl" > "$out/overlap.json"
.\web\.venv\Scripts\python.exe tools\audit_na70_imu_timebase.py --frames "$s/imu_frames.jsonl" --chunks "$s/raw/imu_chunks.jsonl" --output "$out/timebase.json"
.\web\.venv\Scripts\python.exe tools\audit_na70_imu_ranges.py --frames "$s/imu_frames.jsonl" --output "$out/ranges.json"
.\web\.venv\Scripts\python.exe tools\verify_na70_gyro_unit.py --frames "$s/imu_frames.jsonl" --output "$out/gyro-unit.json"
.\web\.venv\Scripts\python.exe tools\analyze_na70_imu_run.py "$s/imu_frames.jsonl" --output "$out/imu-static.json"
.\web\.venv\Scripts\python.exe tools\report_na70_session.py --session $s --output "$out/session-report"
```

`verify_na70_gyro_unit.py` 和 `analyze_na70_imu_run.py` 分别返回 1，表示数据不足和静置拒绝，结果已保留而非修阈值。补充统计脚本 [inspect_observations.py](inspect_observations.py) 使用当前标准解码器生成 `observations.json`、`inventory.json` 和 `provenance.json`；若重跑，应先复制脚本至新的证据目录，避免覆盖本记录。

时间基上界表述修正后，`web\.venv\Scripts\python.exe -m pytest web\tests\test_na70_imu_timebase.py web\tests\test_na70_session_report.py -q`：**7 passed in 1.06s**（SYNTHETIC）。原 `provenance.json` 记录的是修正前工具哈希；修正后工具哈希另见 [corrected-code-sha256.json](corrected-code-sha256.json)。

## 未验证事项

设备采样时间、串口与网络端到端延迟、真实时间偏移、IMU 轴定义和角速度单位、传感器六自由度外参、测量量程及雷达真实丢包情况均未得到独立验证；位置/地图绝对精度、ATE、RPE 均 `NOT_EVALUATED`。当前可继续诊断性工具与输入验证，但不能将此会话判为同步标定或可信动态 LIO 数据。

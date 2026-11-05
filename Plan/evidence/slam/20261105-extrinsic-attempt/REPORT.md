# NA70 + IMU 真实外参标定尝试

状态：`POOR_FIT / NOT_CALIBRATED`。2026-11-05，Windows 本机 Python 3.12；输入会话 `20261105-162734-639900`，保留原始 PCAP 与 IMU 数据，哈希记录在 rotation.json。用户描述前 20 s 静止，随后水平转动约 180°。

## 命令

```powershell
$env:OPENBLAS_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
web/.venv/Scripts/python.exe tools/calibrate_na70_rotation_planes.py --pcap radar_capture_data/na70_sessions/20261105-162734-639900/raw/lidar.pcap --imu-frames radar_capture_data/na70_sessions/20261105-162734-639900/imu_frames.jsonl --start-frame 165 --end-frame 565 --frame-step 10 --output Plan/evidence/slam/20261105-extrinsic-attempt/rotation.json
```

选取 40 帧位置，37 个平面进入拟合，主导平面相邻跳变超过 45° 的次数为 10；该求解器假设同一固定平面，整体不满足。优化角残差 RMS 42.01°，门槛 1°，状态 POOR_FIT。求解器所附矩阵和 -0.17 秒仅为失败拟合中的数值，不能使用或作为候选；平移未求解。平面法向散布有数值不等于固定平面假设成立，也不等于三轴旋转充分。

另以 IMU 前 20 s 角速度均值为短时偏置参考，20–70 s 去均值后角速度 RMS [0.323, 0.245, 4.252] °/s，二阶矩最小/最大特征值比 0.000725；运动主要沿 IMU Z 轴。此统计不构成外参求解或独立标定，见 imu-excitation.json。IMU 设备采样时间缺失，主机时间误差界未知。平面求解器本身不能求解平移；机械模型仅近似，不能冒充测量原点。

## 补采

雷达与 IMU 固定连接，在能持续看见的大平墙前（距离至少 8 m，建议 9–15 m）采集约 100–120 s；预留电缆，保持传感器尽量固定在同一位置、避免遮挡：前 10 s 静止；随后分别绕自身竖直轴、左右轴、前后轴缓慢正反转，每轴约 ±10–15°、各 20–30 s；末尾 10 s 静止。各轴分段，运动平滑且墙面始终留在视野中；如看不到墙则减小角度。追加独立重复和留出数据可用于验证；真正的 t_IL 仍需机械测量或可观测多轴平移与适用的联合标定方法。

墙面、设备坐标、运动范围以实地可操作条件为准；任何时延估计仍为主机时间上的候选，不能直接作为设备同步验收。采集完成后执行相同数据审计与平面一致性检查，再决定能否输出旋转候选。

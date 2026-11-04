# 平移雷达 PCAP 的球门识别基线（REAL_PCAP / DEVELOPMENT / FAIL）

输入为 `radar_capture_data/2/9.14_caochangyidong.pcap`，944 帧、94.517 秒，SHA-256 `02d55634baec13211fcc251e2f77135caf983c10f4893560fe69ea1ed3423f9c`。现场数据说明为雷达缓慢平移；本次没有逐帧位姿、IMU 或球门移动轨迹。Windows 11、Python 3.12.10，本机离线回放，不是 Jetson 性能验收。源码和配置哈希、完整命令在 [`summary.json`](summary.json)。

```powershell
.\web\.venv\Scripts\python.exe tools\run_goal_demo_offline.py --pcap radar_capture_data\2\9.14_caochangyidong.pcap --capture-id f939a3cc-b0b4-48be-9fe1-6587d73de9ce --run 20260924-yidong-facade-moving-944 --strategy facade --no-roi --motion-status moving
.\web\.venv\Scripts\python.exe tools\score_goal_detection.py --run Plan\evidence\goal-demo\20260924-yidong-facade-moving-944 --capture f939a3cc-b0b4-48be-9fe1-6587d73de9ce --reviewed-only --output Plan\evidence\goal-demo\20260924-yidong-facade-moving-944\validation
```

20 帧原始窗口、0.5 m 体素、50–130 m 距离、`facade` 全场提案，检测阈值未变。`--motion-status moving` 只声明传感器运动，用于禁止不可靠的窗口地面回退；它不提供位姿，也不对点云做运动补偿。

| 项目 | 实测 |
|---|---:|
| SEARCHING / CANDIDATE / LOCKED / TRACKING | 830 / 114 / 0 / 0 帧 |
| 非空累积候选帧 / 被选择的当前测量帧 | 59 / 20 |
| 旧框附近 5 m 内的当前测量帧 | 158、162、163、258、273、278、281、395、402 |
| 旧框标注区间 frame 238–257 的接受候选 | 0 |
| 本机 frame_ms p50 / p95 / max | 200.90 / 380.42 / 476.95 ms |

与未显式声明运动的同配置全片运行 `20260924-yidong-facade-full-944` 逐帧比较：`goal`、`accumulated_best`、`selected_measurement` 均 0 帧变化。该修改仅使 944 行运动状态真实可见，并让窗口地面回退在 357 帧明确记录 `moving_ground_window`；没有制造识别增益。

## 旧框窗口的原始证据

旧 `f939a3cc-...json` 在 238–257 帧有一条 box-only 球门标注，但状态为 `PENDING_REVIEW`。其坐标和高度来自旧版框，不能当成这 20 个单帧的逐帧真值。按相同 `(88..101, -28..-15, -3..6) m` ROI 与原始 `(frame_index, point_index)` 引用切成 0.5 秒段：

| 帧 | 原始 ROI 点 | 绝对 z>0.3 m 点 | 观察 |
|---|---:|---:|---|
| 238–242 | 123 | 0 | 该段只见低层回波，无法测量门框 |
| 243–247 | 64 | 64 | 稀疏局部回波 |
| 248–252 | 72 | 72 | 稀疏局部回波 |
| 253–257 | 239 | 239 | 有较高结构，但各扫描帧覆盖不同高度与位置 |
| 258–262 | 53 | 53 | 稀疏局部回波，frame 258 出现当前测量候选 |

逐帧着色的 [238–257 三视图](../20260924-yidong-motion-timeline/views-238-257.png) 与 [原始引用/时间审计](../20260924-yidong-motion-timeline/audit.json) 显示这些回波在不同帧占据不同位置与高度。该图是未补偿的传感器坐标叠加，不能从合成后的门形推定任何单帧已观测到完整球门。frame 238–275 的 [原始点云与算法视频](../20260924-yidong-facade-baseline-300/movement-238-275.mp4) 可逐帧检查；视频来源的未声明运动运行与本运行的算法框逐帧相同。

frame 238–257 的累积检测失败主要记录为 `edge_insufficient_points` 47 次、`insufficient_points` 38 次，另有 `no_boundary_support`、`no_contiguous_boundary` 各 7 次。这些计数是各区域/阶段的拒绝次数，不是失败帧数。不能为了让零星候选成轨而直接放大确认间隔或降低立边门限。

## 评价与下一步

[`validation/validation.json`](validation/validation.json) 为 `NOT_EVALUATED`：`--reviewed-only` 下本录制 0 个 reviewed 正窗口、0 个 reviewed 背景窗口；944 帧都在 UNKNOWN 桶，不能计算真实召回或误检率。当前移动录制 **没有完成可靠球门识别，G4 运动门禁仍 FAIL**。

已让 `run_goal_demo_offline.py` 支持 `--motion-status moving` 并逐帧写入状态，让 `audit_goal_static.py` 可不声明静止而审计移动窗口、在三视图按来源帧着色；未改生产几何和跟踪阈值。`web` 目录执行 `python -m pytest tests/test_goal_ground.py tests/test_goal_facade.py -q`，32 passed。

下一项应在原始点三视图中复核移动录制的球门可见段与背景段，给出每段部件原始引用；若要在平移过程中融合稀疏回波，需要与 PCAP `device_time` 对齐的 `time, x, y, yaw` 传感器位姿序列、坐标系定义及时间偏移。仅有 IMU 朝向不足以补偿平移。先验证静止对照不引入假运动，再比较补偿前后同一窗口。现有 PCAP 本身没有可采信的逐帧位姿，先前纯点云 SE(2) 探针未通过真实数据门禁。

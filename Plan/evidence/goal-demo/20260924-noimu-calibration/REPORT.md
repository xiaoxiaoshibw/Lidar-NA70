# 无 IMU 移动雷达球门标定预开发（REAL_PCAP / DEVELOPMENT）

## 问题模型与输入

录制时移动的是雷达，球门和背景均可视为场景中的静态结构；它们在逐帧 `lidar_link` 坐标下共同移动。输入仅有 PCAP，没有与设备时间对齐的位姿或 IMU。未对齐的多帧点不能当成同一刚性球门，也不能把其中一个当前帧支撑点解释为整个框属于当前帧。

主要输入 `radar_capture_data/2/9.14_caochangyidong.pcap`：944 帧、94.517 s、SHA-256 `02d55634baec13211fcc251e2f77135caf983c10f4893560fe69ea1ed3423f9c`。静止对照 `9.14_caochangyou.pcap` 前 200 帧（近侧球门已有 reviewed 可见段），SHA-256 `ceca9fc08758f3c35f7b8b1c024bdab90e65f49ab65e39102cdd58dc0c5292db`。运行环境 Windows 11 / Python 3.12.10；完整命令、输入、源码和配置哈希在各运行 `summary.json`。

## 完成的代码

1. 地面参考范围与球门搜索 ROI 分离：`goal_detection.z_leveling.reference_roi` 新增 `detection`（原行为，默认）和 `full_field`。后者只用**当前原始帧**的全场有效点拟合地面，球门仍在人工 ROI 内测量；移动窗口地面回退仍被拒绝。
2. 离线 runner 增加 `--roi` 和 `--ground-reference`，将移动多帧的诊断候选及原始点引用写入 `motion_diagnostic_candidates`。ROI 与地面范围、运动声明均进入配置版本与逐帧记录。
3. 对显式 `moving`、来源超过一帧、且没有位姿补偿的 facade 窗口增加 `motion_window_gate`。几何候选保留为 `UNCOMPENSATED_MOTION`、`coordinate_frame=unaligned_sensor_window`、`reject_reason=uncompensated_moving_window`；不送入 tracker 确认。单帧候选仍可进入 tracker。
4. 新增只读背景配准探针 `tools/probe_goal_ego_motion.py`：排除球门区域，复用现有 point-to-plane / GICP / NDT SE(2) 实现和原质量门禁，保存静止与移动对照的候选变换及拒绝原因。没有将不合格变换接入生产检测。

## 真实数据对照

人工 ROI 是开发者以旧 `PENDING_REVIEW` 框选择的位置范围，不能当独立真值。下表中的“候选”是测量算法输出；`TRACKING` 计数还包含没有当前测量的帧。

| 运行 | 范围 / 地面 | 结果 |
|---|---|---|
| `20260924-yidong-manual-roi-300` | 宽 ROI / ROI 内地面 | 300 帧全部 `SEARCHING`，地面参考不可用，0 候选 |
| `20260924-yidong-manual-roi-full-ground-300` | 宽 ROI / 当前全场地面 | 21 帧有累积候选、12 帧被选为当前测量；无锁定 |
| `20260924-yidong-tight-roi-full-ground-300` | 窄 ROI / 当前全场地面，**门禁修复前** | 33 候选帧、frame 131 出现锁定；因几何由移动多帧拼成，锁定不具备当前帧坐标有效性，明确撤回为检测成功证据 |
| `20260924-yidong-noimu-calibration-final-944` | 窄 ROI / 当前全场地面，运动门禁开启 | 54 个多帧门形诊断候选被标记拒绝；没有可信单帧球门测量或锁定 |

门禁修复前 frame 131 的诊断框中心约 `(94.81, -22.20)` m、宽 5.42 m、高 2.61 m，307 个原始支持点来自 9 个不同帧（frame 113–131），其中包含 frame 131 的点。这是**未经补偿的混合坐标窗口**，只能作为人工复核提案。逐来源帧三视图见 [frame 112–131](moving-goal-windows/views-112-131.png)，原始引用与时间见 [审计 JSON](moving-goal-windows/audit.json)。最终运行的 `goal_run.jsonl` 保留候选框和支持引用；另导出 [54 个候选清单](diagnostic-candidates.json) 及 [frame 131 完整支持引用](candidate-frame131-raw-refs.json)，均明确标注为非真值。`summary.json` 保留确切源码版本。

静止近侧球门对照使用相同宽 8 m 的人工 ROI 与原 51 条 reviewed 可见段（派生的 [近侧筛选标注](static-near-reviewed.json)，没有新增人工复核）。默认地面范围的逐帧候选命中为 **19/77**；当前全场地面为 **47/77**，同一评分配置、同一正样本，锁定从 frame 123 提前到 frame 33。此对照只证明窄 ROI 的地面范围分离改善了已知目标的测量覆盖；标注集中没有 reviewed 背景窗口，不能据此报告真实精度或 G2/G4 通过。对应评分文件在两个 `you-static-tight-roi-*` 运行目录的 `validation-near/validation.json`。

## 无 IMU 背景位姿探针

使用 20 帧非重叠背景窗口、排除球门区域、最大距离 130 m。两组静止对照的候选 yaw 接近 0°，但最近邻残差约 0.32 m，高于现有质量门限；四组平移相邻窗口的重合率约 0.28–0.53，均被拒绝，候选平移近乎 0。用 `caochangyou` 开头 200 帧作为静止背景地图对齐其 frame 300–419，后几段重合率降到 0.23、0.085、0.034，也没有可接受位姿。详见 [静止对照](../20260924-noimu-ego-probe/static-control.json)、[平移对照](../20260924-noimu-ego-probe/moving.json)、[静止参考到运动段](../20260924-noimu-ego-probe/static-reference-to-motion.json)。这些候选不能用于补偿或标定球门世界坐标。

## 验证与边界

- `web` 目录目标测试：`python -m pytest tests/test_goal_ground.py tests/test_goal_facade.py tests/test_goal_evidence.py tests/test_goal_lock.py -q` → **63 passed**。
- 后端全量：**346 passed、1 failed**。唯一失败在既有转台导出竞态：测试枚举到 `job.json.tmp` 后，后台原子替换了该文件；单独重跑该测试 **1 passed**。失败事实保留。
- 移动录制 `--reviewed-only` 仍为 `NOT_EVALUATED`：没有 reviewed 运动球门/背景窗口。静止对照不替代运动真值，也不提供雷达位姿。
- 当前可交付的是球门位置附近的**诊断性几何提案**与安全的拒绝门禁。无 IMU 的真实移动段仍未获得可信自运动变换，因此 G4 移动识别保持 FAIL；不采用未经验证的配准候选，也不靠降低立边或确认阈值制造锁定。

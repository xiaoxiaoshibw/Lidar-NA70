# 全场球门跟踪重获门限修复（REAL_PCAP / DEVELOPMENT）

**2026-09-23 真值更正**：用户确认 `(92.7,+29.1)` 也是球门。先前把该处记为非球门而算出的精度变化已撤回；本修复的有效结论是阻止相距约 55 m 的两处球门共用 ID，并消除没有观测支撑的中间框。当前 reviewed 背景为 0，双球门逐目标指标待补算。

输入：`radar_capture_data/2/9.14_caochangyou.pcap` 前 210 帧，SHA-256 `ceca9fc08758f3c35f7b8b1c024bdab90e65f49ab65e39102cdd58dc0c5292db`。Windows 11、Python 3.12.10，`facade` 全场模式，窗口 20 帧、体素 0.5 m，前 20 s 标为现场确认静止。`summary.json` 记录完整配置和源码哈希。

命令：

```powershell
.\web\.venv\Scripts\python.exe tools/run_goal_demo_offline.py --pcap radar_capture_data/2/9.14_caochangyou.pcap --capture-id c4cc19fb-3020-4ccc-8407-1521f71b7212 --run 20260923-reacquire-gate-210 --max-frames 210 --strategy facade --no-roi --confirmed-static-until 20
.\web\.venv\Scripts\python.exe tools/score_goal_detection.py --run Plan/evidence/goal-demo/20260923-reacquire-gate-210 --capture c4cc19fb-3020-4ccc-8407-1521f71b7212 --reviewed-only --output Plan/evidence/goal-demo/20260923-reacquire-gate-210/validation
```

## 根因与修复

旧跟踪器在 `REACQUIRE` 中调用预锁定阶段的高置信度越门限逻辑。frame 48 把中心约 `(94.3,-26.3)` 的近侧球门测量与约 `(92.7,+29.1)` 的对面球门轨迹并入同一 ID；Kalman 输出 `(93.4,+4.4)`，在两者之间形成没有物理观测支持的框。frame 56、65、188 也有同类中间框。

现在旧 ID 的重获以旧轨迹中心为门控锚点，不允许高置信度候选越过 `2 × max_center_jump`。远处结构只能在旧轨迹 LOST 后重新确认并取得新 ID；近处重获仍保留原 ID。对应回归见 `web/tests/test_goal_lock.py`。

## 同窗对照

对旧全场运行 `20260921-fable-facade-fullfield` 的前 210 帧与本运行作行为对照。原一正一负评分在下表之外已撤回：

| 指标 | 旧版 | 修复后 |
|---|---:|---:|
| frame 48 轨迹中心 | (93.4,+4.4)，无实际结构 | (92.7,+29.1)，保持原轨迹 REACQUIRE |
| frame 188 轨迹中心 | (93.6,-0.7)，无实际结构 | (94.3,-26.3)，新 ID 的 REACQUIRE |

**未解决**：frame 27 首先锁定对面球门，这是有效检测；两处球门同时可见时单目标 tracker 仍无法同时维持两个 ID。运动段与独立录制尚未验收。后续需补足 reviewed 背景窗口，并在逐目标评分下验证 G2/G3/G4。

## 可视化（真值更正后）

`full-cloud-entire-capture-two-goals.mp4` 含原始 1211 帧（121.1 s）。左、中、右依次显示完整原始回波、操场主体和固定球门区域放大。静止标注窗口 0–199：绿色为两处已复核球门（实线=本帧可见，点线=已知位置但本帧未见），黑色为算法跟踪框，橙色虚线为算法候选；200 帧以后明确显示“球门真值：未标注”，不把固定标注外推到雷达移动段。画面由 `tools/render_goal_full_cloud_video.py` 生成。

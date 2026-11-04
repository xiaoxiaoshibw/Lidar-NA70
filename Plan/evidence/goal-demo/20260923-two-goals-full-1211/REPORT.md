# 两处球门真值更正后的全片回放（REAL_PCAP / DEVELOPMENT）

输入：`radar_capture_data/2/9.14_caochangyou.pcap`，1211 帧，SHA-256 `ceca9fc08758f3c35f7b8b1c024bdab90e65f49ab65e39102cdd58dc0c5292db`。Windows 11、Python 3.12.10。`summary.json` 保存配置、源码哈希、设备时间和性能；`goal_run.jsonl` 保存逐帧算法结果。

```powershell
.\web\.venv\Scripts\python.exe tools/run_goal_demo_offline.py --pcap radar_capture_data/2/9.14_caochangyou.pcap --capture-id c4cc19fb-3020-4ccc-8407-1521f71b7212 --run 20260923-two-goals-full-1211 --strategy facade --no-roi --confirmed-static-until 20
.\web\.venv\Scripts\python.exe tools/render_goal_full_cloud_video.py --pcap radar_capture_data/2/9.14_caochangyou.pcap --run Plan/evidence/goal-demo/20260923-two-goals-full-1211 --annotations Plan/evidence/goal-demo/annotations-reviewed/window-truth-c4cc19fb-20260923.json --start 0 --end 1210 --fps 10 --output Plan/evidence/goal-demo/20260923-two-goals-full-1211/full-cloud-with-tracking.mp4
```

## 为什么上一版 20 秒后没有框

上一版 `20260923-reacquire-gate-210` 运行时设置了 `--max-frames 210`，而点云视频渲染到 frame 1210。frame 210 以后只有原始点云，没有对应的算法行；画面没有框是导出范围不一致，不能解释为算法失锁。本次重跑去掉帧数限制，处理完整 1211 帧，并重渲整段。

## 全片行为

状态计数：SEARCHING 22、CANDIDATE 5、LOCKED 1、TRACKING 854、REACQUIRE 239、LOST 90。第 199/210 帧均为 REACQUIRE；第 250 帧 TRACKING，第 300 帧 LOST，第 400 帧 REACQUIRE，第 600 帧以后多为 TRACKING。frame_ms p50 227.21、p95 380.49（本机 Windows，离线逐帧口径）。

**状态不是身份真值**。静止标注窗口 0–199 有两处 reviewed 球门；200 帧以后雷达运动，尚无可靠目标位姿/人工逐帧身份，不能把 `TRACKING` 自动解释成“持续跟住球门”。例如第 600 帧后框长期位于约 `(80.2,5.9)`，身份未核实。视频对此明确标记“球门真值：未标注”和“算法跟踪·身份未核实”。

绿色实线/点线分别表示 reviewed 球门本帧可见/已知静止位置本帧未见；黑框是算法轨迹，橙色虚线是检测候选。前 200 帧两处球门均为正目标；没有 reviewed 背景窗口。G0/G3/G4/G5 门禁仍未通过。

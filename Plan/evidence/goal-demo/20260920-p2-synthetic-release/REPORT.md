# P2 证据：可重复 Synthetic 场景与失败评价

日期：2026-09-20。结论：P2 工程门禁 PASS；当前算法指标 FAIL，作为 P3 输入。

## 交付

- `web/backend/goal_detection/synthetic.py`：独立世界真值、传感器位姿和点云生成器；真值不传入 Pipeline。
- `tools/run_goal_synthetic_suite.py`：按场景/seed 运行真实 `GoalDemoPipeline`，输出逐帧真值、结果和指标。
- `web/tests/test_goal_synthetic.py`：19 项契约测试，覆盖确定性、场景完整性、消失/重入、稳定误锁、ID switch 和 seed 冻结。
- manifest 冻结开发 seeds 0–19、验证 seeds 1000–1049、默认 200 帧，并明确它是几何近似而非物理 NA70 仿真。

## 场景与发布运行

15 类场景：static、translation、yaw、translation_yaw、pitch_roll、density_drop、missing_crossbar、single_post_occlusion、disappear_reenter、tree_wall_fence、similar_pair、multi_candidate、fov_edge、empty_nan、time_jump。

发布烟测运行开发 seed 0 与验证 seed 1000，每场景 200 帧，共 30 case / 6000 帧。summary 保存 generator、runner、YAML 哈希。完整 seed 集保留给 P3 冻结参数后的批量门禁，避免用验证集逐 seed 调参。

评价指标包括 precision/recall、中心/宽/高 P95、ID switches、最长正确连续段、目标不可见期间错误确认帧数。测试证明评价器会把“长期稳定跟错树”判为 0 hit，并能检出人为 ID 切换。

## 新发现的失败事实

- static：seed 0 recall 0.005，seed 1000 recall 0.775，随机采样位置造成极大不稳定。
- translation_yaw：seed 0 recall 0.865，seed 1000 仅 0.225，泛化不足。
- density_drop 和 missing_crossbar：两个 seed 基本零检出。
- pitch_roll：recall 0.53–0.575，高度 P95 误差约 0.92–1.00 m。
- disappear_reenter seed 1000：目标不可见期间仍有 5 帧处于 LOCKED/TRACKING，暴露 R04。
- fov_edge：recall 0.335 / 0.8，存在错误输出。
- 当前命中样本 ID switches 为 0，但大量漏检意味着该数字不能单独证明跟踪稳定。

最关键的结构性原因：Accumulator 先把静止的跨帧重复点合并成一个体素质心，BEV 随后用“体素数 / 窗口帧数”估密度。静止目标的重复观测被压成低密度；轻微运动和 yaw 产生更多体素，反而更容易达到密度阈值。这解释了部分“移动比静止好”的反常结果，列为 P3 首项修复。

## 验证

- Synthetic 契约测试：19 passed；
- 全量后端回归：249 passed；
- P1 WebUI 构建和浏览器验收继续通过；
- 发布结果显式标 `SYNTHETIC`，未替代真实 PCAP 或 Jetson 证据。

P2 的通过只表示生成器、真值与评价器能够可靠暴露失败。当前算法没有达到计划中的 95% Synthetic 指标，P3 必须先修复观测计数、关联和失锁语义，再运行完整冻结 seed 门禁。


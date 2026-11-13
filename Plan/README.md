# NA70 当前开发计划与路线记录

2026-11-11 Jetson 实时点云观察：在 Orin 的 `~/na70-live-web` 部署本机监听的 NA70 Web 页面，通过 SSH 转发访问；UDP 2368 接收连续 100 包组成一帧，支持最近 1/5/10/20/30 帧滑动叠加，默认 10 帧。现场浏览器已显示真实回波并验证叠加切换；先前 30 秒录制的真实 PCAP 因回波极少不代表后续可见点云。该工具未做运动补偿或检测，不能作为细线识别、定位同步或 Jetson 性能验收。部署、数据位置和验证边界见[现场记录](evidence/slam/20261111-jetson-live-view/REPORT.md)，使用方式见[工具说明](../tools/na70_live_web/README.md)。

2026-11-06 新路线决策：按用户最新目标，当前感知主线切换为 **NA70 搭载移动载体时实时检测并持续跟踪细线**，以[细线回波验证与逐级开发计划](细线回波验证与逐级开发计划.md)为执行入口；这覆盖并取代 2026-09-21 的足球门近期优先级。阶段门仍按 W1 真实细线回波与独立真值 → W2 可用距离/时长 → W3 线段提取 → W4 全视场提议 → W5 运动补偿、连续跟踪和实时部署推进。现有 W1 真实细线归属仍为 `NOT_EVALUATED`，W2–W5 未通过。运动识别的关键前置条件是可信逐点时间、雷达/IMU 外参和同步位姿；目前 IMU 只有主机接收时间（时间等级 C）、陀螺单位和真实外参未确认，动态 LIO 仍 `DIAGNOSTIC_ONLY`。因此本次切换是目标/路线决策，不是实时运动识别已实现或已验收。首轮应使用有独立位置真值的受控细线场景，先核实其真实回波和距离/积累窗口，再开展移动测试；最终实时性能仍需 Jetson Orin Nano/Humble 实测。

2026-11-05 执行 [NA70 LIO 算法优化 AI 执行计划](NA70_LIO算法优化AI执行计划.md)（T1–T3 全部交付）：`lio_scans.py` 新增声明式逐点时间插值模式 `POINT_INTERPOLATED_UNIFORM_PACKET_GAP`（`--point-time-mode interpolated`，默认 packet 行为回归不变，gap≤0 退化共享、末包 fallback 计数）；新增 FOV 实测工具 `tools/measure_na70_fov.py`，两个独立工程会话实测 pitch 跨度一致为 34.959°（码 2133–3488）、azimuth ~360°，据此把 `tools/lio/na70_fast_lio.yaml` 修正为 `fov_degree: 35.0`、`filter_size_surf/map: 0.15`、`blind: 7.4`（近距下限证据），WSL2 同段数据新配置重放对比仅记录内部统计（路径 43.5→30.3 m 等，不归因）。SYNTHETIC 全量回归 590 passed / 2 failed（缺 `1.pcap`/`2.pcap` 环境性预存）/ 3 skipped。**插值是声明近似非实测逐点时间；设备时间戳、陀螺单位、外参未解决；动态 LIO 保持 `DIAGNOSTIC_ONLY`**。证据 [20261105-lio-point-time-and-config](evidence/slam/20261105-lio-point-time-and-config/REPORT.md)、[重放对比](evidence/slam/20261105-lio-config-replay/REPORT.md)。

2026-11-05 新增 [NA70 LIO 算法优化 AI 执行计划](NA70_LIO算法优化AI执行计划.md)（PLAN_READY，未执行）：两项纯软件任务——`lio_scans.py` 声明式逐点时间插值（`POINT_INTERPOLATED_UNIFORM_PACKET_GAP`，默认 packet 行为不变）与 FOV 实测工具＋FAST-LIO 配置修正（fov_degree/filter_size/blind）。不解决设备时间戳、陀螺单位、外参；动态 LIO 保持 `DIAGNOSTIC_ONLY`。

2026-11-05 Qt 本机录制入口：按用户要求新增 `启动雷达IMU录制.bat`，独立 Qt 窗口复用已有双流采集核心，支持串口枚举、定时/手动停止、安全退出、质量提示与录完自动逐包主机时间关联；不需启动 Web 服务。`packet_imu.csv` 保留每包源索引与前后 IMU 完整接收批次的行号，间隔过大、序号中断、跨 segment 与范围外均标记，时间倒退拒绝。设备采样同步、真实外参仍未验证；未知矩阵与误差界保持 null。用法见 [Qt 录制说明](../docs/NA70_QT_CAPTURE.md)，测试及旧真实会话复核见 [Qt 证据](evidence/slam/20261105-qt-capture/REPORT.md)。

2026-11-04 后续离线复核：没有更新的现场会话；修复 LIO 包审计遗漏帧边界包间隔的问题，合成回归及真实包复算见[连续性复核](evidence/slam/20261104-lio-continuity-audit/REPORT.md)。全 FF 原码与回放突发风险分别保存在该报告引用的独立证据中；厂家语义、实际 FAST-LIO 接收完整性、真实同步与外参仍未验证，动态 LIO 保持 `DIAGNOSTIC_ONLY`。

2026-11-04 会话 `20261104-170341` 审计与采集修复：有效雷达包 83,351，IMU 20,052 帧，主机数据端点交叠 99.640 s；IMU 无设备采样时间且批量到达，动态同步、真实外参与 LIO 精度仍未通过。修复未来串口读取与合成 UDP 头的未知目标地址记录；真实硬件复测仍待进行。详见[会话工作流报告](evidence/slam/20261104-session-workflow/REPORT.md)。更正下文及历史报告将“最大主机批次间隔”称为采样时间误差上界的说法：缺少缓冲和传输延迟界时该上界未知；旧 53 ms 只是观察到的批次间隔，不是经验证的时间误差上界。

2026-11-04 本机最小双流接收：按用户要求，WebUI 提供 IMU 串口与 NA70 UDP 2368 的开始、停止及状态，数据写本机会话目录，后续处理另行进行。原始 IMU 字节与雷达 UDP 载荷保留；本机 PCAP 帧头为合成，UDP 内核丢包数未知，质量记录为 `degraded` 而非零丢包。SYNTHETIC 采集与浏览器模拟测试已运行；真实连接短录会话 `20261104-161220` 保存了 IMU 2078 帧和雷达 9552 包。时间同步和现场验收尚未验证。使用说明见 [标定与场景工具链](../docs/NA70_CALIBRATION_WORKFLOW.md#01-windows-本机双流接收)。

2026-10-30 近距下限审计（22:20 补充）：实测四段不同来源的 PCAP（60 s 工程会话、a2 会话、151019 会话、经典户外 `1.pcap`，各 40 帧 80 万射线）**最小有效距离一致为 7.488 m，距离码 1–467 从未出现**（码 468 = 7.488 m）；60 s 会话全量 600 帧直方图在 6 m 以下同样 0 点。该下限属于数据/设备侧，不是解码器或工具的过滤（`decoder.py` 只按 `距离码 × 16 mm` 换算，`calibrate_na70_rotation_planes.py` 的 `min_range=1.0 m` 不构成门槛）。**影响：外参 ROT 的墙距指导由 2–5 m 改为至少 8 m、建议 9–15 m**（2–5 m 会零回波，平面求解必失败）；STATIC 与六面批次不受影响。已同步采集卡、两个采集脚本的屏幕提示、工作流文档（0.1/4.1 节）。证据 [近距下限审计](evidence/slam/20261030-near-range-floor/REPORT.md)；另补 60 s 会话全帧叠加三视图 [20261030-v4-60s-fullstack](evidence/slam/20261030-v4-60s-fullstack/REPORT.md)（有效回波 1,241,651 点，场景静止、无正对平墙）。是否可配置（起始距离 vs 固件下限）未判定，未向设备发送任何配置命令。

接续 19:45 条目：

2026-10-30 一键标定批采与本地批处理（19:45 补充）：新增 `开始外参采集.bat`→`tools/capture_na70_rotation.ps1`（单次 80 s CAL-ROT：正对大平墙慢速三轴转动、录完自动 scp 回本机）与 `开始全量标定采集.bat`→`tools/capture_na70_calibration_batch.ps1`（CAL-STATIC 120 s＋CAL-ROT 80 s＋六面各 25 s，统一写 `na70-calibration-batch-v1` 批次清单）；新增 `tools/solve_na70_six_face_sessions.py`（分会话六面 `M,b`）与 `tools/report_na70_calibration_batch.py`（本地整批处理：静置/六面/旋转三阶段，状态 INVALID / ROTATION_READY_TRANSLATION_PENDING / PARTIAL，平移一律不填）。修复两个采集脚本在服务端 `failed` 终态挂死的问题（现在立即报错并保留 Jetson 会话；duration 上限 1800 s）。新增 SYNTHETIC 测试 `web/tests/test_na70_six_face_sessions.py`、`web/tests/test_na70_calibration_batch_report.py`——后者含平面法向 `calibrate()` 端到端用例（合成墙面＋真值 R_IL/零偏移恢复：R 角误差 <0.5°、时间偏移在网格步长内）；标定子集 45 passed / 1 skipped，全量后端 547 passed / 3 skipped。用法见 [标定与场景工具链](../docs/NA70_CALIBRATION_WORKFLOW.md)（0.1/4.1/4.2 节），采集操作见 [用户采集卡](NA70_YESENSE_用户采集卡.md)。以上均为 SYNTHETIC/工程验证；仍等待现场批采数据，B1–B5、真实外参、时间同步与 Jetson 验收结论不变。

2026-10-30 标定与场景离线工具链：新增 `na70_core/calibration/`（静置/Allan、六面加速度计、平面测距、`R_IL`＋时间偏移、外参候选合成）、`na70_core/scene.py`（位姿插值、逐点变换、体素地图、PLY 导出）及 6 个 CLI；SYNTHETIC 回归通过，见[标定证据](evidence/slam/20261030-calibration-synthetic/REPORT.md)与[场景证据](evidence/slam/20261030-scene-synthetic/REPORT.md)（10 Hz 位姿插值＋合成噪声下，地图到已知平面 P95 约 1.1 cm）。工具用法见 [标定与场景工具链](../docs/NA70_CALIBRATION_WORKFLOW.md)。真实会话 smoke 另发现 12,018 帧中 10,838 个相邻主机时刻相同、正间隔中位 50.9 ms（约 19.6 次到达/秒），主机接收时间不能用于毫秒级 IMU—LiDAR 对齐，见 [smoke 报告](evidence/slam/20261030-calibration-tools-smoke/REPORT.md)。新增时间基审计：该会话 1,180 批、每批 10–11 帧、批次间隔最大 53.0 ms、无设备时间字段，时间等级 **C**；53 ms 上界在 0.5 m/s、50 m、10°/s 下约 0.49 m，动态 B5/SLAM 时间门禁不能通过，见 [时间基报告](evidence/slam/20261030-timebase-audit/REPORT.md)。采集质量报告改为动态识别 0x51（出现时标 `device_field_present_unverified`），新增设备时间 CSV 导出（缺字段/倒退拒绝，处理 32 位回绕）。新增一键会话诊断，汇总时间基/量程/静置/PCAP 端点重叠：该会话判 `REJECTED_NOT_STATIC`、时间等级 C、端点重叠 59.977 s、6 窗口全拒绝，见 [会话报告](evidence/slam/20261030-session-report/REPORT.md)。新增 LIO 扫描包导出（保留包级时间与帧/包/点源引用，`lio_ready` 恒 false）；真实前 5 帧 smoke 为 100,000 点、最大包间隔 1.147 ms，IMU 首样本比首雷达包晚 19.204 ms，判不覆盖，见 [LIO 包 smoke](evidence/slam/20261030-lio-package-smoke/REPORT.md)。新增陀螺单位自检：用设备四元数增量对照陀螺，60 s 振动会话 `INCONCLUSIVE`（比例 0.284、余弦 0.269），融合姿态被平滑，陀螺单位仍为推断 deg/s，见 [单位检查](evidence/slam/20261030-gyro-unit/REPORT.md)。静置分析增加主机批次平均 Allan（只报告批次尺度慢漂移）；10 分钟长录 120,017 帧的 60 个窗口全拒绝、无饱和，见 [长录审计](evidence/slam/20261030-long-imu-audit/REPORT.md)。新增地图质量工具：对参考地图/已知平面/控制点做距离统计，不做配准、不宣称绝对准确度；场景构建同时落盘 `map.ply` 与实际使用的 `trajectory.csv`。WSL2 原型已把 LIO 包接到固定 FAST-LIO：50 帧（5 s、1,000,000 点）真实包被连续处理约 45 次更新，记录 27 个位姿（最大半径 0.199 m）与 1,326 点最新配准云；但时间等级 C、单位外参、无独立真值、无完整 PCD，轨迹只作诊断，状态 `PROTOTYPE_CONSUMED_50_FRAMES / DIAGNOSTIC_TRAJECTORY_ONLY / ACCURACY_NOT_EVALUATED`，见 [原型报告](evidence/slam/20261030-na70-fastlio-prototype/REPORT.md)。v4 60 秒会话已 scp 回本地：PCAP 600 帧/59.996 s、0 无效包；IMU 12,020 帧、时间 C、6 窗口全拒绝、无饱和；场景无大平面（残差 P95 1.52 m），不能用于外参，见 [v4 审计](evidence/slam/20261030-session-audit-v4/REPORT.md)。Windows 平面法向 `R_IL`/时间偏移求解器已就绪，等待正对大平墙的慢速三轴转动采集。仍无同步现场数据：B1–B5、真实外参、真实操场重建与 Jetson 验收均未通过，未生成冻结标定。

2026-10-30 修复版 10 分钟工程会话 IMU JSONL 回传哈希对账通过，60 个 10 秒窗口均超出历史静置开发阈值；无温度记录、无外部姿态真值，不能从该结果归因热漂移或判硬件故障。详见 [长录证据](evidence/slam/20261030-capture-fixed-long/REPORT.md)。依用户说明保留工作振动和热态，后续用可控条件和实测温度评估可用性，不放宽阈值冒充验证。

2026-10-30 用户明确雷达工作会振动、升温会影响 IMU；标定应覆盖工作条件而非假设完全无振动或冷态长期有效。新增 [IMU 波动频谱诊断](evidence/slam/20261030-imu-vibration-diagnostic/REPORT.md)，两段工程数据均有约 53.52 Hz 的名义峰（按 200 Hz 帧率估计、设备采样时间未知，不可归因或排除混叠）。采集页新增可选实测温度、测点和工具说明，不把外壳温度冒充芯片温度；SYNTHETIC 页面测试通过。温度系数、同步与标定均未验证。

2026-10-30 修复版长录 PCAP 解码补验：Jetson 用经源码哈希对账的唯一解码器完整扫描会话 `20261030-160920`，599950 有效包、0 异常、0 忽略、6000 帧（1 帧不完整），结果见 [长录报告](evidence/slam/20261030-capture-fixed-long/REPORT.md)。计数差和同步仍未定论。V4 60 秒工程录制文件审计通过，但静置诊断六窗口均拒绝，已询问用户当时是否振动或运动。

2026-10-30 V4 接入与实机浏览器：Jetson 8792 切换至 V4，60 秒双流工程会话 `20261030-163303` 采集报告、文件审计及 IMU 原始重放均为 `ok`；含 36 未解析字节、计数差 108 包未定因。静置诊断六窗口被拒绝，是否振动/运动未知；工程数据不是 B1。新增 B1 俯视摆放示意并暂禁后续批次，生产 WebUI 在 Windows 本机启动；详见 [V4 证据](evidence/slam/20261030-capture-v4-browser/REPORT.md)。A2 其他动作卡、用户操作闭环及设备时间/标定仍未验收。

2026-10-30 静置分析准备：新增 `tools/check_na70_imu_static.py` 与开发阈值配置 `tools/lio/static_check.yaml`，分窗口检查加速度、角速度和主机时间，短尾合并、异常字段拒绝；SYNTHETIC 相关测试 3 passed。实际 60 秒工程会话 `20261030-155552` 的 6 个窗口均超出历史静置开发阈值，数据不作静置标定基线；原因未确认，详见 [报告](evidence/slam/20261030-capture-a2-browser/REPORT.md)。结果不证明同步、标定或真实物理静止，B1 尚未派发。

2026-10-30 Jetson V4 录制中 SIGTERM 实测：工程会话 `20261030-162935` 收尾落盘、提前停止判 `degraded`，无遗留 tcpdump，文件审计通过；详见 [退出测试](evidence/slam/20261030-sigterm-test/REPORT.md)。已增加正常信号退出收尾和提前停止降级；不证明 SIGKILL、断电或磁盘满时完整性。V4 为独立测试实例，原 8792 服务尚未升级。

2026-10-30 新增 `tools/audit_na70_overlap.py`：用实际 PCAP 包和 IMU 接收块的主机时间检查端点重叠与最大间隔，拒绝时间倒退；SYNTHETIC 测试通过。对工程会话 `20261030-155552` 两个已核对哈希的文件复查，端点重叠 59.976969 s，最大雷达包接收间隔 0.001411 s，最大 IMU 接收块间隔 0.053003 s，详见 [证据](evidence/slam/20261030-capture-a2-browser/host-overlap.json)。主机接收时间不能证明设备采样时间、同步或端点间数据持续有效。

2026-10-30 IMU 启动异常复测：Jetson V3 连续三次 5 秒双流工程采集，质量与原始字节重放均未再发现校验错误；一次起始有 49 字节未解析。详见 [重复诊断](evidence/slam/20261030-imu-startup-repeat/REPORT.md)。样本不足以确定先前校验错误原因，不能宣称零丢帧；B1 尚未派发。

2026-10-30 V3 Jetson 真实浏览器工程测试：两旧服务空闲后停止，部署 V3；60 秒浏览器录制/刷新流程通过，同根目录第二进程锁争用被拒绝。但会话 `20261030-162203` 质量 **degraded**：首个接收块 1 次 IMU 校验错误，原始字节审计复现；首个有效帧起于原始偏移 78，原因尚不确定。文件哈希与 12026 帧对账通过。详见 [V3 浏览器证据](evidence/slam/20261030-capture-v3-browser/REPORT.md)。不能把 UI 测试通过当采集质量通过；B1 暂不派发。

2026-10-30 修复版 10 分钟长录 `20261030-160920` 已完成：120017 IMU 帧、PCAP 880726624 字节；文件哈希、IMU 块/帧对账与原始字节重放审计均为 `ok`。未解码长录 PCAP，captured/received 差 169 包未定因，时间仍 `host_receive_only`。详见 [长录证据](evidence/slam/20261030-capture-fixed-long/REPORT.md)。该实例不含后续锁、恢复、倒计时和刷盘错误修复；B1、同步与标定状态不变。

2026-10-30 写盘故障注入：针对真实采集线程与 runner（SYNTHETIC 串口）分别注入原始字节写入失败和结束刷盘失败；两种情况均生成 failed 会话。修复结束时 flush/close 错误被忽略的问题，错误写入质量报告；Windows 采集相关测试 46 passed。此为模拟文件错误，不等同于 Jetson 实际磁盘写满；正在运行的 `20261030-160920` 不含该新修复。

2026-10-30 增加 Windows 采集连接脚本 `tools/start_capture_tunnel.ps1` 与双击入口 `启动标定连接.bat`：通过 SSH 将本地端口转发至 Jetson 采集服务，默认候选 Wi-Fi/有线地址，既有本地端口被占用时拒绝并保留原进程。实测独立本地 18791 到 Jetson 8792 可读取进行中的长录；占用 8791 拒绝分支通过，未影响长录。该入口依赖已有 SSH 密钥、Jetson 服务和独立启动的 WebUI，地址可能随 DHCP 变化；当前仍为工程入口，未开放现场 B1。

2026-10-30 增加采集前 3 秒可取消倒计时；启动请求网络异常时提示检查会话状态，避免假定请求未到达。Playwright SYNTHETIC 验证取消后立即重启只发一次请求，1 passed。增加 SYNTHETIC 进程强杀测试：已写入的测试字节保留、未完成会话可见、进程锁释放；Windows 44 项采集测试通过，WSL2 同项 1 passed。首次 Windows 测试因换行符假设失败，修正后重跑通过。这不是 Jetson 真实断电或持续采集强杀测试，界面和锁尚未部署到运行中的长录实例。

2026-10-30 会话恢复界面：没有完整 manifest 但留有会话证据的目录现在列为 `unfinalized`，不将它误判为已完成；由于其他进程可能仍在录制，页面明确写“可能中断或仍在录制”。可点击历史会话查看文件。Windows 采集相关测试 43 passed，Playwright 模拟浏览器 1 passed；尚未部署到正在运行的 Jetson 长录，也没有完成真实断电/强杀验证。

2026-10-30 增加 `tools/audit_na70_imu.py`，按原始接收块重放帧解析并与 JSONL 的时间、段号、序号及数值逐项对账，能检测分片和导出错配；合成故障检查及 Windows 32 项采集核心测试通过。Jetson 原旧版 10 分钟会话 120016 帧、修复版 20 秒会话 4023 帧均逐项一致，证据见各自报告。此工具复用采集解析器，不独立验证协议或同步。修复版长录 `20261030-160920` 仍在运行，待结束后执行同样检查。

2026-10-30 修复版 10 分钟长录 `20261030-160920` 已启动，尚未结束；启动后双流持续写盘。并行开发跨进程采集锁：相同会话根目录下的新版本采集器互斥，释放进程后可重试，Windows 41 项采集测试通过，WSL2 Ubuntu 22.04 跨进程锁测试 1 passed。首次 Windows 测试因错误假定 stderr UTF-8 而失败，修正断言后通过。锁未部署至正在运行的实例，且不同根目录或不遵守锁的旧进程不受其保护；部署前须统一入口并停止旧版，不能宣称已解决所有串口争用。

2026-10-30 修复版 Jetson 20 秒双流工程会话 `20261030-160803`：所有清单文件的哈希及 IMU 4023 帧对账通过，备注已持久化，独立审计 `ok`；仍不代表同步或标定通过。见 [修复版验证](evidence/slam/20261030-capture-fixed/REPORT.md)。修复版尚未完成长录、浏览器和故障注入验收，B1 暂不派发。

2026-10-30 10 分钟工程长录完成：120016 IMU 帧、880726624 字节 PCAP。旧版报告降级的 2 次断号均为实测 `60000→1` 回绕；独立审计仍因旧版 events.jsonl 哈希错误而失败，其他文件哈希和 IMU 块/帧对账通过。详情见 [长录报告](evidence/slam/20261030-capture-long/REPORT.md)。修复版已在独立端口启动短录验证，未改变标定或 B1 状态。

2026-10-30 浏览器工程会话回放检查：PCAP 回传哈希与清单一致，解码 59995 有效包、0 异常包、600 帧；与 tcpdump captured 相符，但 captured/received 差异仍未解释。真实 host-receive IMU CSV 被 LIO 预检正确拒绝为缺少设备时间，见 [预检报告](evidence/slam/20261030-lio-host-time-rejection/REPORT.md)。发现旧服务未保存操作备注，现已修复并测试；此前 session 标记 B1 只是工程测试，不作现场 B1。长录仍运行旧版，未覆盖。

2026-10-30 IMU 序号实测：长录 `20261030-155659` 中同一 segment 相邻帧 `60000→1`，共享主机接收时间 1793347166.123712；历史监视器亦接受此回绕。旧采集器按 16 位回绕误记一次 sequence_break，新版识别该已观察边界，同时保留常规 16 位回绕及其他断号检查；这不是零丢帧证明。只读审计增加块/帧单调时间和段号检查、序号异常及该回绕计数。Windows 采集测试 39 passed。长录仍运行旧代码，完成后保留原有错误计数并进行独立审计，不修改原始证据。

2026-10-30 会话完整性审计：发现旧版在计算文件清单后写入结束事件，导致 `events.jsonl` 哈希必然失配；已修正结束顺序并添加只读 `tools/audit_na70_session.py`。对 60 秒浏览器工程会话独立审计结果为 **failed**，仅 events 日志大小/哈希不符，IMU 原始块覆盖和 12018 帧对账通过；未检查 PCAP 解码或时间同步，旧 manifest 不改写。Windows 采集测试 **38 passed**。正在运行的长录仍用旧版，预计同样会暴露该问题，须实测确认；B1 不派发。

2026-10-30 长录期间本地可靠性修复：IMU 接收窗口的读写增加同步；质量检查纳入载荷错误、重连和停止异常；采集过程中抓包进程提前退出或 IMU 工作线程故障将提前收尾并判失败。页面显示设备状态，报告明确进程窗口重叠不证明数据连续重叠，并将角速度/欧拉角单位标为推断。Windows 采集测试 **37 passed**。这些更改尚未部署到正在运行的长录实例；长录 `20261030-155659` 继续使用此前版本，不能用新检查追溯判定。

2026-10-30 A2 真实浏览器工程诊断：通过 SSH 本地转发和 Chromium 实际启动 Jetson 60 秒双流录制、刷新并观察完成，1 项浏览器测试通过；IMU 12018 帧、PCAP 88,072,684 字节，tcpdump captured/received 差 94 包未解释。详见 [浏览器诊断](evidence/slam/20261030-capture-a2-browser/REPORT.md)。10 分钟长录 `20261030-155659` 已启动，尚未完成验证；B1 仍未派发。

2026-10-30 A2 更新版经 Wi-Fi 在 Jetson 独立端口做 15 秒双流工程诊断，IMU 3014 帧、199.8 Hz，PCAP 22,080,212 字节；captured/received 差 26 包未解释。新增 IMU 工作线程异常传播并通过 35 项相关测试。详细数据、哈希、失败测试记录见 [A2 诊断](evidence/slam/20261030-capture-a2-stage/REPORT.md)。此结果不代替真实浏览器闭环、长录、时间同步与标定验收；B1 仍未派发。

2026-10-30 A2 入口开发：主工作台新增“标定采集”导航及页面，通过本机 8791 端口转发嵌入 Jetson 采集控制台；新增 B1 静置提示，页面明确仍处于开发验证。`npm run build` 通过，采集 Python 测试 33 passed；Playwright **SYNTHETIC** 模拟状态与请求验证入口、开始录制及刷新后恢复，1 passed。此验证不连接真实 Jetson，不代表采集、长录或标定通过；实际设备和浏览器闭环仍未完成，B1 暂不可派发。

2026-10-30 采集可靠性续修：控制台支持启动期间请求停止，并在收尾完成前阻止新会话；sudo 抓包停止仅向当前采集进程的 tcpdump 子进程发信号。空 PCAP、未知内核丢包计数和磁盘不足提前停止不再判为正常。Windows `web/.venv` 执行 `pytest tests/test_na70_capture_service.py tests/test_na70_capture.py -q` 为 **33 passed**，包含模拟启动停止和质量边界，尚未验证真实设备长录与浏览器闭环，A2/B1 状态不变。

2026-10-30 采集入口核查：工作区已有独立 A2 控制台原型，尚未完成主 WebUI 集成、动作卡及真实浏览器采集验收。修复采集任务异常时的设备回收和失败会话落盘，并约束文件读取的会话路径；Windows `web/.venv` 执行 `pytest tests/test_na70_capture_service.py tests/test_na70_capture.py -q` 为 29 passed。该测试使用模拟采集服务，不证明实际设备或浏览器闭环；B1 仍待 A0–A2 门禁完成。

2026-10-30 标定 A0/A1 进展：两路发流实测（雷达 `192.168.1.66:7000 → 192.168.1.129:2368`、1410 字节、1000 包/秒、10 Hz；IMU 67 字节 YESENSE 帧、199.8 Hz、加速度 m/s² 已核实、角速度 deg/s 为推断、**无设备采样时间戳**），硬件事实写入 [hardware_contract.json](evidence/slam/20261030-hardware-contract/hardware_contract.json)。新增 `na70_core/capture/` 与 `tools/na70_capture_session.py` 实现持续双流落盘，20 项测试通过；Jetson 15 秒真实会话 `20261030-151019` 判为 ok（IMU 3015 帧、PCAP 22 MB、回传哈希 MATCH、解码 151 帧 0 异常包）。证据与缺陷修复见 [A1 REPORT](evidence/slam/20261030-capture-a1/REPORT.md)。**未升级**：时间仍 `host_receive_only`、雷达工厂状态字 -20.48° 语义未核实、captured 与 received 差 66 包未定位、A2 用户入口与 B1 未开始；同步/标定/SLAM/球门/导线/转台结论一律不变。

2026-10-30 路由器侧补充确认：用户提供 yahboom 已连 Xiaomi 网络，当前 Wi-Fi DHCP 地址 192.168.31.168、路由器显示 MAC DC:4A:9E:DF:DC:AA，观察时连接 3 分 23 秒。已补入[部署记录](NA70_Jetson采集网络部署记录.md)及独立 router-observation.json；本次仅记录，未修改网络或升级采集/标定验收。

2026-10-30 Jetson 采集网络已配置：按用户授权，enP8p1s0 经交换机保留雷达接收地址 192.168.1.129/24 与 192.168.127.100/24，新增 192.168.3.129/24，默认网关 192.168.3.1；wlP1p1s0 已连 Xiaomi_52BF，当前 DHCP 地址 192.168.31.168/24，仅作管理链路。两侧网关与新有线 SSH 已验证，自动连接已保存，旧配置保留；未重启验证。详见[部署记录](NA70_Jetson采集网络部署记录.md)。Jetson USB 已识别 ttyACM0，但用户权限待处理；5 秒 UDP2368 监听 0 包，实际雷达发流/持续双流采集尚未验收。标定计划后续以 Jetson 同机采集、Windows 控制分析推进 A0–A2，不升级同步/标定/性能状态。

2026-10-30 标定分工与执行入口：用户明确“AI 干软件与计算，用户负责测数据”。新增[NA70＋YESENSE 标定 AI 执行计划](NA70_YESENSE_标定AI执行计划.md)与[用户采集卡](NA70_YESENSE_用户采集卡.md)，作为本轮 S0–S2 标定任务入口。AI 先完成 A0–A2（硬件合同、持续原始双流采集、用户入口），实际闭环验证后才派发 B1 的 60 秒静置试录；之后逐批进行静置/六面、雷达测量几何、时空外参与留出验证。当前为 PLAN_READY，A0–A8 未因本计划创建而完成；不把独立原型的有限 CSV 缓冲当持续录制，不升级同步、标定或 SLAM 状态。

2026-10-30 YESENSE 本机接入与安装布局：已记录用户确认的 90×130×110 mm 外壳、尾部顶面中线 IMU 和 +Y 侧视右下角大接口；COM12/460800/8N1 实测约 200 Hz。保存[布局与原始证据](evidence/slam/20261030-imu-mounting/REPORT.md)及[内外参测试指南](../docs/NA70_YESENSE_CALIBRATION_GUIDE.md)，保留串口失败、静置一致性符合和运动干扰拒绝。模型坐标不是实测外参，T_IL/时间偏移未知，雷达内参、同步、完整标定及 NA70 LIO 仍 NOT_EVALUATED；本地监视器是独立工具，未集成主 WebUI。

2026-10-30 SLAM 软件准备首批开发：按用户“列出任务直接开发”完成[任务清单](NA70_SLAM无数据开发任务.md) T1–T7 的软件准备范围。新增 `na70_core/lio.py`、PCAP＋规范化 IMU CSV 输入预检/导出 CLI、13 项输入契约测试和固定版本构建/公开回放工具。Windows 全量后端 **420 passed / 2 skipped**；WSL2/Humble 完成 FAST-LIO2 与 GLIM CPU/ROS2 构建，GLIM 作者 Ouster＋IMU 公开数据前 30 s 输出 270 行轨迹、11 子地图。1 条 IMU 覆盖警告与初次构建/下载失败均保留，准确度 `NOT_EVALUATED`。旧 `1.pcap` 缺同步 IMU/标定时正确拒绝。详见[开发报告](evidence/slam/20261030-development/REPORT.md)和[用法](../docs/NA70_LIO_INPUT_PREPARATION.md)。NA70 专用 ROS2 时间 handler、实际 USB 驱动、同步标定、真实移动 SLAM 和 Jetson 验收尚未完成。

2026-10-30 非重复扫描 SLAM 代码调研：用户说明暂无新数据，要求先搜索相关开源实现。已核对 FAST-LIO2、GLIM、Point-LIO、Voxel-SLAM 等项目及选定源码，保存[代码调研](../docs/NA70_NONREPETITIVE_SLAM_CODE_SURVEY.md)和[固定版本清单](evidence/slam/20261030-code-survey/source-manifest.json)。保留 FAST-LIO2 ROS2 为局部前端候选，新增 GLIM CPU 为完整建图对照；先评估现成后端再决定是否开发回环。可先做独立 Humble 构建、作者公开数据回放和输入契约验证，**本轮未构建、未运行、未下载数据**；现场同步、标定及 NA70/Jetson 验收状态不变。

2026-10-29 雷达＋IMU SLAM 设计：用户确认 **NA70＋USB 输出的 IMU，手持或小车在操场/室外建图**，要求先设计下一步 SLAM 计划。新增本轮执行入口 [NA70＋IMU SLAM 开发计划](NA70_雷达IMU_SLAM开发计划.md)，状态 **DESIGN / NOT_RUN**。移动建图采用“同步采集与标定 → NA70 时序适配/运动补偿 → FAST-LIO2 ROS2 局部里程计 → 回环验证与位姿图优化 → WebUI/Jetson 验收”，首批实施仅 S0–S2；IMU 型号、USB 协议、原始输出与采样时间及同步能力待核实，USB 连接本身不作为同步证据。允许 WSL2/Humble 做 LIO 离线算法验证，Jetson/Humble 仍是最终性能目标；不恢复无 IMU 点云里程计，不将现有推算 offset_time 当实测逐点时间。本轮只更新设计，未接入或运行 SLAM；球门、导线和转台的既有状态及证据保留，以下旧“当前主线”表述按其记录日期理解。

2026-10-03 高压线分支启动：按用户「在静止 PCAP 中识别 200 m 外高压线」新增[执行计划](高压线识别执行计划.md)，作为独立候选路线并行；**足球门 G0/G1 仍是当前主线**，原有路线决策不变。本日已交付：
- **P0** 候选 catalog + S0 150/200 m 扫描-机会扩展（`Plan/evidence/hv-line/20261003-p0/`），明确 catalog 不是真值。
- **P1** `packages/na70_core/na70_core/windows.py` 增量因果包级时间窗，与 `thin_wire.select_window` 字节级一致；10 项契约 tests。
- **P2** `packages/na70_core/na70_core/wire/` 手动 ROI 线段提取 pipeline（拟合固定 seed、退化过滤、有限支持验证、evidence 分桶），独立 `wire_default.yaml`，10 项 tests；离线运行器 `tools/run_wire_w3_offline.py` 落盘 manifest/stages/candidate/REPORT。
- **W3 受控 smoke**：8 个真实 PCAP + 合成 fixture 通过流程并产生 6 个 MEASURED_CURRENT 候选 + 2 个 INSUFFICIENT_SUPPORT 拒绝；评估报告 [W3_EVAL_REPORT.md](evidence/hv-line/20261003-p0/W3_EVAL_REPORT.md) 明确这些**不是**高压线身份（几何更符合场景物体），wire_return_status 保持 NOT_EVALUATED。
- **依赖**：新增 `pyransac3d==0.7.0`（Apache-2.0），numpy2 兼容、`warnings.catch_warnings` 屏蔽其空样本除零警告。全量后端 tests 381 passed；前端 `npm run build` 通过。
- **合成矩阵**：`na70_core/wire/synthetic.py` 12 场景确定性夹具（真线/稀疏 200 m 线/垂度/交叉/遮挡 + 树冠/围栏/屋檐负例/时间跳变）+ runner `tools/run_wire_synthetic_matrix.py`，证据 `Plan/evidence/hv-line/20261003-synthetic-matrix-v2/`（首轮 10/12 锁定两弱点：树冠团块伪线、150 m 垂度直线接受；均已记录，无阈值放松）。8 项契约测试加入全量。
- **弱点加固（同日第二轮）**：`validation.py` 增 SPARSE_SUPPORT/EXCESSIVE_SAG 两个真实测量门（新拒绝码在 `constants.py` 显式注册，`min_support_density_per_m` / `max_sag_m` 入 `wire_default.yaml`），`proposal_full_fov.py` 全 FOV angular-slab 连通体管道（起 `--enable-full-fov` 开关），离线 runner `tools/run_wire_w4_full_fov_offline.py`。真实 zhong2 1 s 窗 full-FOV smoke：`NO_CURRENT_MEASUREMENT`——78k 点在 2°×2° slab 上 6-邻接连成 682×393×63 m 大连通体，**angular-slab 模型在真实薄回波上不工作**，已如实记录 `20261003-w4-zhong2-f600-fullfov/W4_FINDINGS.md`，P4 改走 Hough/PCA 对照。合成矩阵 `20261003-synthetic-matrix-v3` strict 全过（含两弱点被新门拒绝）。全量测试 392 passed。
- **W4-A 提案对照（同日第三轮）**：新依赖 `opencv-python-headless==4.10.0.84`；新增 `na70_core/wire/proposal_hough.py`、`na70_core/wire/proposal_pca.py`、统一对照 runner `tools/run_wire_w4_proposal_compare.py`、SYNTHETIC 两新场景 `two_wires_close_50m`/`wire_in_canopy_100m`、离线 W4 实测路径 `--enable-full-fov`。真实 zhong2 f600/1 s 对照：**slab 0 提案（基线出局）、hough 25 提案/18 评估/一个 MEASURED_CURRENT（span 20.36 m，nearest 76.53 m，UNKNOWN_LINE）、pca 2 提案全被 finite_support 拒（INSUFFICIENT_SUPPORT/LOW_COVERAGE/SPARSE_SUPPORT）**。SYNTHETIC 五场景三方各有胜负（hough 在 syn 默认参数下 0 evaluated 是参数粒度问题，非 bug，如实记录）。证据 `Plan/evidence/hv-line/20261003-w4a-compare-{synth,real-zhong2}/`（含 W4A_FINDINGS.md）。`tests/test_na70_core_wire_w4a.py` 6 项加入全量（**398 passed**）。
- **本轮收尾（同日第四轮）**：`na70_core/wire/tracking.py` 预测/测量/历史分桶占位补完；`validation.py` 结构化 `reject_counts`；pipeline `source_ids_sha256` 填充；`na70_core/evidence/scoring.py` reviewed-only 评分器 + 离线 `tools/score_wire_run.py`（unreviewed truth → 如实 NOT_EVALUATED）；`/api/wire/detect/runs` W3/W4 REST 服务（`WireDetectionRuns` 持久化、restarted 状态恢复、资源上限 400k 点/运行）+ 前端 `/wire/` 工作台 W3/W4 面板（`WireDetectionPanel.tsx`）；`requirements.lock` 插入 pyansac3d/opencv 保持字母序；新增 evidence/W4-A/detect API 测试，全量 **406 passed**（1 个 integration skip）。
- **二次 review 收尾（同日第五轮）**：W1 scorer 一对一契约修复（同一 detection 不再重复 TP；同 truth 命中多 detection 取最佳仅 1 个 TP、其余 FP，`detectio_detections.index(row)` 绕 index 全程）；pipeline 增 `proposals_outcomes` stage（per-proposal reject、reject_counts、category、input_count 全部保留）；前端 `openDetail` 加 try/catch/notice；`pytest_markers` 注册 `evidence_anchored` 并把 W4-A artifact pin 的测试标注。3 个 scorer 一对一新测试全过。全量 **408 passed**（1 integration skip）；前端 build 通过。
- **未实现**：`/api/wire/w5` 运动模式前端/路由（与路线地图一致 NOT_RUN）；P3 200 m 独立实测数据；W4-B 一次 frozen 评分（等 P3 reviewed truth 到位）。按执行计划推进。

2026-09-27 固定传感器静态场景原型：按用户提出的静态环境方向，建立独立固定传感器建图分支；**足球门 G0/G1 仍是当前主线**。Windows 本机 Python 3.12 WebUI（`127.0.0.1:8766`）对 `radar_capture_data/2/9.14_caochangzhong2.pcap`（SHA-256 `2caa68d6491cb2ba6fd9bae86e04104038fbbf4c7a4e349fa6dcc211adc743ce`）完成首 10 秒帧 0–99 的持久化原型任务 `447b6663-e435-4c5c-b9e9-2feea4cd3dd6`：100 固定观测、0 拒绝、0 配准，610,518 个融合回波、114,341 体素、257 地图块、7.533 秒；另有全 1,298 帧未持久化核心冒烟（1,297 固定观测、1 拒绝、7,967,144 融合回波、277,116 体素、0 丢弃）。标记 `DEVELOPMENT / OBSERVED_ONLY`，几何准确度 `NOT_EVALUATED`；没有 IMU 或实测转台角度，不代表完整场景重建或多视角拼接验收。已在 MAP 视图查看体素地图与每体素回波数着色，并导出 5,081,554 字节证据包。Windows Python 3.12 后端全量 `pytest tests -q` 为 361 passed / 2 warnings，前端 `npm run build` 通过；Jetson 未运行。详见 [Web 验证记录](../web/VALIDATION.md)。帧 0–99 与 100–199 的分段一致性观察见[报告](evidence/static-scene/20260928-zhong2-split/REPORT.md)，不构成准确度或 ghosting 验收。

2026-09-26 导线分支前期开发：用户要求在没有新 PCAP 时先完成可独立验证的工程准备，执行[无新 PCAP 前期开发清单](NA70_无新PCAP前期开发清单.md)。已建立 `na70_core` 观测契约并共用正式解码/S0 的角码变换，W1 新运行增加方向质量计数；现有 1/2.pcap 只用于协议回归，不是导线真值。R0/R1/R2 仍部分完成，W1 归属保持 `NOT_EVALUATED`；这项前期开发不改写足球门近期主线。

2026-09-26 WebUI 第一批重构：四工作台导航、主回放与球门 Demo 身份校验、共享点云解析、独立导线 W1 持久化诊断 API 和页面已落地；真实导线归属仍 `NOT_EVALUATED`，W3–W5 接口未启用。后端 356 项通过；真实密集回放与 `2.pcap` 1× 全片播放在预览点数修复后定向通过，初跑失败及复测口径见 [Web 验证记录](../web/VALIDATION.md)。这不代表导线架构计划中的 R1 观测核心迁移或 R3 真实回波门禁完成，也不改变足球门近期主线。

2026-09-25 架空导线分支架构设计：按用户要求，新增[重构执行计划](NA70_架空导线感知重构执行计划.md)、[软件架构](../docs/NA70_WIRE_ARCHITECTURE.md)、[算法设计](../docs/NA70_WIRE_ALGORITHMS.md)和[调试验收契约](../docs/NA70_WIRE_DEBUG_ACCEPTANCE.md)。这是方案与工作区设计记录；W1 真实回波归属仍 `NOT_EVALUATED`，W2–W5 未运行。既有足球门主线与阶段状态保持原记录，后续实际切换或并行实施时按执行计划更新路线决策。

2026-09-24 无 IMU 移动雷达球门预开发：将当前单帧地面参考范围与人工球门 ROI 分离，静止近侧 reviewed 可见帧的候选命中从 19/77 增至 47/77；仅有 PCAP 的平移录制得到 54 个可复核门形提案，但多帧源坐标未经补偿，现全部由 `motion_window_gate` 拒绝参与跟踪。球门区域外的背景 SE(2) 探针未通过静止残差与运动重合率门禁，G4 移动识别仍 FAIL。见 [无 IMU 标定预开发报告](evidence/goal-demo/20260924-noimu-calibration/REPORT.md)。

2026-09-24 平移雷达录制基线：`9.14_caochangyidong.pcap` 全 944 帧以 `facade --no-roi --motion-status moving` 回放，LOCKED/TRACKING 均为 0；旧 frame 238–257 box-only 标注仍 `PENDING_REVIEW`，该区间无接受候选。已保存逐来源帧三视图、原始引用与 `NOT_EVALUATED` 评分；G0 移动真值和 G4 运动识别仍未通过。见 [平移录制报告](evidence/goal-demo/20260924-yidong-facade-moving-944/REPORT.md)。

2026-09-23 快速移动诊断：用户指出 frame 329–499 运动期间球门失锁，停稳后视觉效果较准确。逐帧 trace 表明地面和区域提案多数仍有效，20 帧叠加后的立边门禁首先失败；5/10 帧短窗在 300–359 帧也没有恢复当前测量，并严重损失静止段覆盖。未更改生产阈值或加入未经验证的 yaw 补偿；G4 运动门禁仍 FAIL。见 [快速移动诊断报告](evidence/goal-demo/20260923-fast-motion-audit/REPORT.md)。

2026-09-23 全片回放更正：先前完整点云视频只叠加了前 210 帧算法结果，后续没有框是运行与渲染帧数不一致。现已对 1211 帧全片重跑并导出带算法框的视频；移动段轨迹身份未核实。见 [全片回放报告](evidence/goal-demo/20260923-two-goals-full-1211/REPORT.md)。

2026-09-23 最新真值更正：用户确认图中红叉 `(92.7,+29.1)` **也是球门**。撤销先前「对面非球门／困难负样本」判断。静止窗口现有近侧与对面两处 reviewed 球门，尚无 reviewed 背景窗口；先前基于一正一负的精度、误检和 G0 门禁结论作废。旧报告保留判断过程，并以本更正为准。修正标注及逐帧证据见 [G0 真值包](evidence/goal-demo/20260923-g0-window-truth/REPORT.md)。

2026-09-23 跟踪修复：全场 `facade` 的 REACQUIRE 曾允许高置信度候选跨越位置门限，使相距约 55 m 的两处球门共用 ID，并产生无观测支持的中间框。现将旧 ID 重获限制在原轨迹门限内；旧版精度对照因对面球门身份更正已撤回。见 [重获门限真实回放报告](evidence/goal-demo/20260923-reacquire-gate-210/REPORT.md)。

2026-09-21 最新路线决策：用户将近期主线恢复为 [足球门识别主线开发计划](足球门识别主线开发计划.md)，目标是先把真实足球门识别成功，用它验证完整感知链。顺序固定为 **G0 可信真值 → G1 高度基准 → G2 人工 ROI 内含网面几何 → G3 全场搜索 → G4 跟踪 → G5 冻结验收**。第一轮只执行 G0–G1；P3 真实算法仍 FAIL / IN_PROGRESS。

2026-09-21 补充（facade 策略尝试）：按 G2 方向实现 `strategy.mode: "facade"`（人工 ROI 内立面几何，不用三维连通），在真实主 PCAP 静止窗口首次获得真实测量驱动的锁定与跟踪（frame 33 锁定，TRACKING 128 帧，LOST 期错误确认 0，ID 唯一），证据见 [facade 运行报告](evidence/goal-demo/20260921-fable-facade/REPORT.md)。评分仍 NOT_EVALUATED（无 reviewed 真值），不改变 G0–G1 优先级，也未宣称 G2 通过。

2026-09-21 补充（facade 全场搜索尝试，2026-09-23 真值更正）：facade 策略在无 ROI 时进入全场提案模式（XY 聚类 → 逐区域立面测量 → NMS）。静止段在近侧 `(94.3,-26.3)` 与对面 `(92.7,+29.1)` 均找到真实球门；先前把对面球门当作背景的结论已撤回。跟踪器跨两处球门错误关联、运动段围栏候选和位姿未证仍需解决；G3/G4 未通过。见 [全场运行报告](evidence/goal-demo/20260921-fable-facade-fullfield/REPORT.md)。

2026-09-23 补充（G0 reviewed 真值包）：近侧球门 51 个可见段、对面球门 40 个可见段（57 帧）已按用户最新身份确认修正；尚无 reviewed 背景。`--reviewed-only` 与 capture_id 对齐机制保留，但旧一正一负评分失效。见 [G0 真值包报告](evidence/goal-demo/20260923-g0-window-truth/REPORT.md)。

2026-09-23 补充（G0/G1 证据）：历史 frame 153/597 复核、近侧球门部件级提案、标注工作台用例和 G1 高度对照证据已完成。因对面结构改判为球门，G0 要求的 reviewed 背景窗口现为 0，G0 门禁重新打开；G1 技术证据保留，阶段退出应待 G0 真值门禁补齐后复核。见 [G1 报告](evidence/goal-demo/20260923-g1-exit-evidence/REPORT.md)。

细线 W1 的工具与证据完整保留：[计划](细线回波验证与逐级开发计划.md)、[工具/验证记录](evidence/thin-wire/20260921-w1-development/REPORT.md)、[现场采集卡](../docs/THIN_WIRE_W1_CAPTURE_CARD.md)。按 2026-11-06 新路线决策，本分支恢复为当前感知主线；W1 真实回波归属仍 `NOT_EVALUATED`，W2–W5 尚未通过，不将足球门或诊断性 LIO 结果外推为运动中细线能力。

足球门当前基线仍为真实全片无 LOCKED/TRACKING、待复核标注 0/11。已确认绝对高度截断、BEV 统计口径和球网宽连通组件三项阻断；第一轮先修真值和高度，不用降低锁定阈值掩盖检测失败。固定点转台保留为独立路线。

## 路线决策与历史记录

2026-09-21 前一版球门分支计划为 [GOAL P3 真实球门检测修复执行计划](GOAL_P3_真实球门检测修复执行计划.md)，继续作为根因细节和历史实验矩阵参考；若与当前执行顺序冲突，以新的足球门主线计划为准。

此前球门计划首轮要求确认雷达与目标静止，对比单帧和 0.5/1/2/5 秒积累，以及原始、过滤、不同体素后的部件支撑。现有按帧工具须扩展时间窗，不能将近似帧数直接当实际秒数。分支交接见 [球门提示词](GOAL_P3_下一窗口交接提示词.md)。运动补偿、处理/显示延迟、观测等待、稳定性与部署分别设门禁；球门通过不代表导线能力通过。

[球门完整开发计划](球门跟踪下一阶段完整开发计划.md)保留 P0–P7 分支安排；[原 P3 计划](GOAL_P3_球门检测与跟踪修复计划.md)保留历史修复与回归记录。下述转台建图为独立路线，不作为当前足球门任务开发顺序。
2026-09-20 补充：加入受限地面平面 z 归一化后，Synthetic `pitch_roll` 双 seed
recall 达到 1.000；真实主 PCAP 标注零命中仍按 FAIL 保留，P3 未退出。

2026-09-20 根因排查补充：[真实球门失败报告](evidence/goal-demo/20260920-root-cause-audit-final/REPORT.md)。已量化绝对高度裁剪、体素后 BEV 密度口径与球网连通组件三处阻断；7.32 米旧框为 UI 默认值，旧“左柱零点”不能单独证明目标缺失或时间错位。修复地面估计裁剪后不足未回退累积云的缺陷，270 项后端测试通过；真实全片仍无锁定，P3 保持 FAIL / IN_PROGRESS。

2026-09-15 路线决策：用户选择“固定点左右扫描操场，采用带角度记录的转台拼接”。当前执行入口改为[固定点转台操场建图实施与验收计划](固定点转台操场建图计划.md)。该路线使用转台实测角度作为位姿来源，只融合停稳后的静止扫描；点云配准仅用于一致性验证和受约束微调，不再以无 IMU 移动雷达纯点云里程计作为近期主线。

历史移动建图状态、失败证据和长期扩展路线仍保留在[MASTER 开发与验收总计划](MASTER_开发与验收总计划.md)。第一至第六阶段的工程修复和回归已完成；2026-09-14 已收到操场旋转/平移 PCAP 与现场照片，移动建图生产链基线正式配准成功帧为 0。覆盖驱动 SweepBuilder 已通过 G1；逐自由度可观测性及受约束 SE(2) 的 ICP/GICP/NDT 在真实数据上未通过 G2。该事实作为转台路线的反向回归和决策依据保留，不再阻塞转台建图 T0–T5。

2026-09-14 补充（诊断）：独立探针实验表明数据本身可用——静止数据 10 s 窗口漂移仅 0.44 m / 0.02°，100 包成帧假设被定量证实为主要失败根因。地面块方位漂移给出 zuo 6.2° / you 10.1° 的航向参考值，但简单 SE(2) 结构探针在多输入对照下噪声地板过高（静止假航向 −2.25°、单窗口跳变 30°），尚不能可靠测出转角；确定结论是结构配准需 ≥1 s 累积覆盖、且接受前必须几何验证。详见 [探针实验证据](evidence/mapping-repair/20260914-playground/kissicp-se2-probe-20260914.md)。

2026-09-11 历史主线调整：当时 WebUI 主流程改为静态环境中移动雷达、无 IMU 的**点云里程计与体素地图融合**，以持久化 mapping job 为唯一实现；移动物体检测退出主验收范围（后端保留、入口移出）。该阶段**不是完整 SLAM**（无回环、无位姿图、无全局优化）。实现边界、真实 PCAP 失败证据和已知限制见 [MASTER_开发与验收总计划.md](MASTER_开发与验收总计划.md)。该记录不再作为当前开发顺序。

> 以下第 1–4 节为旧移动物体检测工作的历史背景，已退出主线，不是当前目标、状态或开发顺序。新增 SLAM 工作按本页顶部 2026-10-29 的雷达＋IMU 计划执行；足球门、细线和固定点转台保留各自路线与门禁。历史采集预检增量见 evidence/mapping-repair/20260911-repair/preflight-delivery-20260912.md。

版本：v2.0
日期：2026-09-10
当前开发环境：Windows 本机 PCAP 离线分析与 WebUI 验收；当前感知目标见本页最新路线决策。

## 1. 历史目标（非当前主线）

从 NA70 连续扫描数据中识别人员移动形成的时空点带，恢复移动时间段、空间路径和速度，并在 WebUI 中进行可复查展示。

```text
NA70 PCAP
  → 正式协议解码
  → 原始扫描射线历史
  → 距离变化候选点
  → 时空点带聚类
  → 时间切片与轨迹拟合
  → 移动点、事件、路径和速度
  → WebUI 叠加显示与导出
```

## 2. 历史进度

- Windows 本机 PCAP 导入、索引、逐帧解码和三维回放已完成。
- 已确认 NA70 的 100 包帧不是完整重复扫描；普通相邻帧体素差分会产生大量扫描伪变化。
- 已实现全片射线历史分析：按原始俯仰码和航向码比较重复扫描方向的历史距离。
- 已实现距离变化候选点、时序窗口、加权 DBSCAN、Kalman／匈牙利关联、事件检测和逐点掩码。
- WebUI 已支持红色移动点、目标框、速度方向、运动事件跳转和 CSV 导出。
- `003.pcap` 已完成首轮分析：44.794 秒数据中识别出 `0.000～26.401 s` 和 `37.101～39.602 s` 两个主要运动时段。
- `003.pcap` 结果已保存到 `../2/analysis_003/`，包括 PLY、CSV、JSON、时间线和分析说明。
- 当前工程测试：Web 后端 20 项测试通过，前端生产构建通过，运动分析浏览器测试通过。

## 3. 历史限制

- 当前结果识别的是移动变化和时空点带，不能直接给出可靠人数。
- 现有轨迹会被双楔镜的稀疏扫描拆成多个片段，需要继续做四维点带合并。
- 尚无逐点人工标注和同步视频时间标注，W2 保持 `PARTIAL`，不能宣称准确率通过。
- 当前分析面向离线 PCAP；实时输入、长期背景更新和断流恢复属于后续工作。

## 4. 历史开发顺序（已停止作为执行依据）

1. 用视频和真实 PCAP 明确拉伸点带的形态与时间对应关系。
2. 将候选点从逐帧聚类升级为 `(x, y, z, t)` 四维点带聚类。
3. 对点带做主方向、时间跨度、位移、连续性和回归误差判定。
4. 沿时间切片恢复瞬时目标位置，合并被扫描稀疏性拆开的轨迹片段。
5. 在 WebUI 中显示完整拉伸轨迹，同时显示当前时间片的位置、速度和目标框。
6. 使用单人、多人、交叉、遮挡、停留和离场数据进行人工标注验收。
7. 再将稳定的离线算法整理为实时接口和嵌入式部署适配层。

## 5. 文档入口

1. [NA70_雷达IMU_SLAM开发计划.md](NA70_雷达IMU_SLAM开发计划.md)：本轮新增 SLAM 工作入口，先设计，首批实施 S0–S2。
2. [足球门识别主线开发计划.md](足球门识别主线开发计划.md)：球门路线入口，G0–G1 门禁和既有状态保留。
3. [GOAL_P3_真实球门检测修复执行计划.md](GOAL_P3_真实球门检测修复执行计划.md)：球门根因细节与历史实验矩阵参考。
4. [GOAL_P3_下一窗口交接提示词.md](GOAL_P3_下一窗口交接提示词.md)：球门分支基线和历史交接。
5. [细线回波验证与逐级开发计划.md](细线回波验证与逐级开发计划.md)：当前运动中细线实时检测路线；W1 工具已完成，真实归属与运动实时能力仍待验证。
6. `固定点转台操场建图计划.md`：独立转台路线、采集、算法、门禁和验收入口。
7. `MASTER_开发与验收总计划.md`：历史移动建图状态、失败证据和后续扩展路线。
8. `当前开发执行手册.md`：无 IMU 移动建图历史执行手册。
9. `05_开发执行规范.md`：代码、测试、证据和表述规则。
10. `evidence/`：历史和当前运行证据，不作为新的开发计划。

当前转台开发版证据：`evidence/turntable/20260915-development-final/`。该目录明确标记 `DEVELOPMENT / DIAGNOSTIC / ESTIMATED_ANGLE`，包含 A 原始基线、B 静止未补偿、C 估计角度、D 受约束微调对照以及 PLY/PCD、来源着色预览和自包含 ZIP。

## 6. 后续部署边界

既有 PCAP 分析、目标检测与 WebUI 工作继续在 Windows 本机完成，WSL2/ROS2 不作为这些任务的前置条件。2026-10-29 新增的雷达＋IMU SLAM 路线允许在 WSL2 Ubuntu 22.04/Humble 上验证 C++ LIO 离线算法；硬件采集在设备访问可靠的 Linux/Jetson 上进行。WSL2 结果不能替代 Jetson Orin Nano/Humble 的 ARM 性能、实时输入和长测验收。

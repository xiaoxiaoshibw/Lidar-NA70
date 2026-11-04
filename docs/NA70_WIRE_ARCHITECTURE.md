# NA70 架空细线感知软件架构（设计稿）

版本：`wire-architecture-v1-draft`  
日期：2026-09-25  
范围：固定雷达原始回波验证、离线导线识别、机载运动扩展和可复查验收。本文主要定义目标结构；2026-09-26 已建立 `na70_core` 的 `RayBatch` 观测契约及共用角码坐标变换，并将方向质量计数接入 W1。包级增量时间窗、StageTrace、真值/评分及检测流水线仍未实现；真实导线回波未检出或确认。实施状态见[无新 PCAP 前期开发清单](../Plan/NA70_无新PCAP前期开发清单.md)。

## 1. 设计依据与边界

- 当前路线仍以 [Plan/README.md](../Plan/README.md) 为准；足球门是现行主线，细线 W1 的工具已完成，但真实回波归属为 `NOT_EVALUATED`。[细线计划](../Plan/细线回波验证与逐级开发计划.md)和[机载可行性计划](../Plan/无人机单根高压线识别场景与可行性计划.md)分别定义 W1–W5、S0–S4。本文是导线分支的架构设计，不自行改写路线状态。
- NA70 协议以 [ROS2 驱动](../ROS2/src/double_wedge_driver/src/inno_device_lidar_node.cpp) 为唯一参考。[Python 解码器](../web/backend/decoder.py)已经实现经典 PCAP 的 1410 字节载荷、200 条记录/包、100 包逻辑帧及工厂偏移。逻辑帧不等于一次完整重复扫描。
- 包内 200 条记录共享包级设备时间。驱动发布时推定的逐点时间偏移不是 PCAP 实测逐点时刻。零距离且俯仰、方位码均为零的记录没有可恢复射线方向；未知方向不计入“确认照射但没有回波”的分母。
- `1.pcap` 的 [W1 工具记录](../Plan/evidence/thin-wire/20260921-w1-development/REPORT.md)只证明协议和工具可运行；[S0 审计](../Plan/evidence/thin-wire/20260924-s0-release/REPORT.md)只给合成导线与可恢复射线的几何交会。两者都不是导线检出率。
- 第一阶段输出是有原始回波支撑的架空线形障碍及其最近距离。仅凭几何不能识别电压等级；类别不确定时输出 `UNKNOWN_LINE`。实时飞控闭环不在本架构首批验收范围。

## 2. 目标结构

```text
经典 PCAP / ROS2 PointCloud2、CustomMsg
          │
          ▼
NA70 输入适配层：协议解码、字段/坐标校验、包时间和原始身份
          │  RayBatch（不可变观测）
          ▼
因果时间窗与质量门：时间段、零距离、方向可恢复性、运动补偿资格
          │  ObservationWindow
          ├──────────────► W1 回波与扫描机会审计
          ▼
导线流水线：候选提议 → 三维模型拟合 → 有限支撑验证 → 关联/预测
          │  LineCandidate / LineMeasurement / TrackOutput
          ▼
统一诊断与证据：StageTrace、原始引用、运行清单、真值和评分
          ├──────────────► 离线命令行与不可覆盖证据包
          ├──────────────► FastAPI 导线 API
          └──────────────► WebUI 导线工作台
```

### 2.1 单一算法核心

目标目录按以下边界建设；迁移次序见[执行计划](../Plan/NA70_架空导线感知重构执行计划.md)：

```text
packages/na70_core/
  pyproject.toml
  na70_core/
    capture.py           # 经典 PCAP 解码与索引；迁移现有 Python 解码逻辑
    observations.py      # RayBatch、原始 ID、有效/方向掩码、坐标与时间质量
    windows.py           # 包级因果窗口与时间段；迁移 W1 已验证逻辑
    wire/
      proposals.py       # 人工 ROI 或全视场提议
      fitting.py         # 可替换的第三方直线拟合适配器
      validation.py      # 有限线段、来源、覆盖、背景和新鲜度验证
      tracking.py        # 后期测量关联与预测，不制造测量
      pipeline.py        # 纯算法编排，返回结果及真实执行 trace
    evidence/
      schemas.py         # 真值、运行、候选、trace 版本化契约
      scoring.py         # 独立录制评分；不参与检测
web/backend/wire_api.py # 会话、回放、标注和证据查询；不内嵌算法
web/frontend/src/wire/ # 导线查看、标注、逐级诊断、运行对比组件
tools/wire/             # 离线回放、评分、证据校验入口
web/tests/wire/         # 协议/契约/算法/API 测试
```

`na70_core` 的算法路径不得导入 FastAPI、Three.js 或 ROS2。Windows 回放和 ROS2 节点调用同一个 Python 核心；只有 Jetson 实测表明拟合热点不满足预算时，才把 `fitting.py` 后端替换为 PCL C++，并使用同一输入、输出与评分样本做一致性对照。Python 核心的依赖与语法必须同时验证 Windows Python 3.12 和 Jetson/Humble 的实际 Python 环境，不能把本地锁文件直接视作 Jetson 可用。`windows.py` 的全片路径使用增量、有界 ring buffer；现有 W1 工具每个指定窗口重解码，继续作为抽样审计入口，不直接承担高频重叠窗回放。

### 2.2 输入契约

`RayBatch` 至少保留：`capture_sha256`、`frame_index`、`packet_index`、`point_index`、原始距离/俯仰/方位/强度码、解码 XYZ/intensity、`valid_range`、`direction_known`、`device_time`、`capture_time`、`time_quality`、`install_direction`、`coordinate_frame`。所有数组按原始射线顺序对齐；筛选只生成 mask 或原始索引子集，不重新编号。`packet_index` 是 PCAP 中有效 NA70 包顺序号，不伪称设备序列号。

`ObservationWindow` 保存请求时长、实际首末包跨度、包/帧范围、时间段、运动状态、位姿来源和窗口质量。包时间缺失、倒退或间隔超限时按[W1 契约](THIN_WIRE_W1_CONTRACT.md)断段；不跨段拼接。未经可信位姿补偿的移动多包窗口标为 `UNALIGNED_MOTION`，不进入当前测量确认。

`LineCandidate` 包含生成方式、原始支撑引用、暂定几何和拒绝原因。`LineMeasurement` 额外包含有限支撑区间、三维线段/可选曲线、正交残差、沿线覆盖、最大缺口、独立包/帧数、最新来源时间、最近距离及不确定度。曲线外推部分与实测支撑区间分开表示。

输出分开记录两个维度：`geometry_class=LINE_STRUCTURE|UNKNOWN_LINE`，`evidence_role=MEASURED_CURRENT|PREDICTED_RECENT|EXPIRED_HISTORY|NO_CURRENT_MEASUREMENT|UNOBSERVABLE`。`MEASURED_CURRENT` 必须引用当前允许时间范围内的唯一原始测量；同一来源集合的重叠窗口不能重复充当独立确认。

### 2.3 现有代码如何迁移

| 当前入口 | 目标处理 | 兼容与回归 |
|---|---|---|
| [web/backend/decoder.py](../web/backend/decoder.py) | 协议实现迁入 `na70_core.capture`；原路径暂保留转发接口 | 以 ROS2 驱动和既有 `web/tests/test_decoder.py` 对照字节、角码、XYZ、偏移、时间和异常包；前后输出逐点一致 |
| [web/backend/thin_wire.py](../web/backend/thin_wire.py) | 提取时间窗、原始引用与统计为公共组件；W1 报告入口继续可用 | 既有 W1 NPZ、逐包/逐窗指标和来源 ID 不变；旧产物只读保留 |
| [web/backend/wire_scene.py](../web/backend/wire_scene.py) | 保留为 `SYNTHETIC` 几何和覆盖测试夹具；其角码到射线方向函数与解码核心合并 | 与正式驱动做方向对照，不向真实回波或检测 TP 注入合成点 |
| [goal_detection/observability.py](../web/backend/goal_detection/observability.py)、[GOAL_DEBUG_CONTRACT](GOAL_DEBUG_CONTRACT.md) | 借鉴阶段执行 trace、来源新鲜度和 `UNKNOWN` 评分纪律，抽可共享的展示组件 | 球门阈值、体素融合和目标几何不进入导线算法；球门 API/评分回归保持原语义 |
| [web/backend/demo_api.py](../web/backend/demo_api.py) | 抽会话/运行包/来源查询接口，导线使用独立路由 | 原 `/api/demo` 路由继续运行；不把导线状态写进球门会话 |
| [DemoApp.tsx](../web/frontend/src/DemoApp.tsx)、[AnnotateApp.tsx](../web/frontend/src/AnnotateApp.tsx) | 抽点云视图、时间轴、点选与证据高亮组件，再建独立导线页 | 不复用球门的框标注和类别解释 |

## 3. 工作区与运行数据

- `radar_capture_data/thin-wire/` 保存原始 PCAP 和现场测量文件；PCAP 继续排除 Git。每次导入以 SHA-256 固定身份，原始文件不可因调参而覆盖。
- `web/data/wire/` 保存可重建的本地会话、草稿标注和运行缓存；该目录不是正式验收来源。
- `Plan/evidence/thin-wire/<run-id>/` 保存不可覆盖的门禁证据包：manifest、配置、真值版本、逐窗 trace、评分、图和失败索引。大型原始云可保存在数据区，由哈希和相对产物路径引用；复制到正式证据包的产物必须声明是否抽样。
- `tools/wire/` 只放入口脚本和小型配置/夹具；算法不得同时在脚本、API 和 ROS2 节点各实现一份。
- 当前工作树有大量既有修改和未跟踪证据。实施前生成文件清单、`git status`、输入/源码哈希与基线测试记录；隔离开发工作区必须包含现有 W1/S0 文件，不能直接从缺少未跟踪文件的 HEAD 猜测基线。

## 4. 运行模式与系统接口

1. **固定机位 W1/W2**：只做原始回波与背景对照，算法检测可关闭。WebUI 必须允许查看零距离、方向未知、设备时间质量及原始目标附近回波。
2. **离线 W3/W4**：读取同一 `RayBatch`，按真实设备时间建立因果窗口；人工 ROI 与无 ROI 共用拟合及验证模块，只有提议入口不同。每次运行产生独立 `run_id` 和不可变配置快照。
3. **运动 W5**：外部提供实测外参、位姿及时间同步。只有质量通过的包参与坐标变换；未补偿窗口保留诊断但拒绝测量确认。当前输出与历史预测分层。
4. **实时接口**：ROS2 wrapper 只负责字段按名称和 datatype 映射、时间/坐标契约及发布；输入断流、队列积压、位姿失效和资源超限都有显式状态。Jetson Orin Nano/Humble 是最终性能验收环境。

API 采用现有 FastAPI `APIRouter` 风格，导线路由前缀拟为 `/api/wire`。数据接口按资源拆分为 `captures`、`runs`、`windows`、`candidates`、`trace`、`sources`、`truth`、`scores`；详细请求/响应和界面状态见[调试与验收契约](NA70_WIRE_DEBUG_ACCEPTANCE.md)。运行器以持久化 run 为权威，使用独立限额任务池和可恢复任务状态；现有 demo 的内存最新 snapshot 仅供交互，不承担当次验收记录。前端采用独立导线工作台入口和组件，不改变当前无 WebSocket、HTTP 轮询的项目约束。

## 5. 架构验收条件

- PCAP 离线、Web API 与后续 ROS2 输入映射为同一种原始观测身份；没有第二份协议公式。
- 任一线段输出都能回查原始点、包时间、算法阶段、参数版本及拒绝/接受理由；丢失来源的候选不能被评为当前测量。
- 重跑相同输入、配置和版本可得到相同的窗口、支撑集合、评分和证据摘要；拟合器的随机种子及版本固定。
- 旧球门与 W1 接口通过定向回归；大规模迁移逐阶段实施，失败时可切回旧入口，历史产物不改写。
- W1 真实回波未通过时，系统可以完成审计和失败定位，但不输出导线识别成功、有效距离或实时告警通过结论。

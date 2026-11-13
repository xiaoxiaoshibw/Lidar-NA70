# NA70 激光雷达算法与 WebUI

围绕 NA70 双楔镜激光雷达开展点云解码、观测诊断、目标几何提取、跟踪、建图与雷达／IMU 标定的研究工程。仓库收录 Python 核心算法、React 三维工作台、ROS2 驱动与适配节点、离线工具、阶段计划和实验记录。

进度整理日期：**2026-11-13**。当前感知目标为移动载体上的细线检测与持续跟踪，按原始回波归属、距离与积累时长、线段提取、全视场提议、运动补偿与连续跟踪逐级推进。代码实现、合成验证、现场观测和正式验收分别记录。

## 分类导航

| 分类 | 内容 | 入口 |
|---|---|---|
| 核心算法 | 原始点身份、角度转换、因果时间窗、细线几何、标定、场景拼接、LIO 扫描包、地图质量 | [packages/na70_core](packages/na70_core/) |
| WebUI | PCAP 导入、三维回放、叠加、标注、球门 Inspector、细线诊断、采集与历史建图 | [Web 使用说明](web/README.md) |
| ROS2 | C++17 驱动、运动检测封装、LIO 适配 | [ROS2](ROS2/) |
| 离线与采集工具 | PCAP 分析、算法对照、标定求解、Qt 录制、Jetson 实时观察 | [tools](tools/) |
| 算法进度 | 各路线实现、验证材料、后续工作 | [算法分类与进度](docs/ALGORITHM_PROGRESS.md) |
| 历史路线 | 已结束迭代的方案、代码入口、结果、版本与复现条件 | [历史算法路线与复现索引](docs/ALGORITHM_HISTORY.md) |
| 复现与数据 | 依赖安装、软件检查、数据准备、证据类型 | [复现指南](docs/REPRODUCIBILITY.md) |
| 计划与证据 | 路线决策、阶段门、实验日志与指标 | [Plan/README.md](Plan/README.md) |

## 算法进度概览

| 路线 | 已有实现与记录 | 当前阶段 |
|---|---|---|
| 细线检测 | W1 原始统计与 Web 工作台、W3 人工 ROI 提取、W4 离线提议与对照工具 | 受控目标真值与现场回波归属复核；实时运动链按阶段推进 |
| 球门检测与跟踪 | 地面高度处理、BEV、立柱／柱对、几何打分、时序跟踪、逐阶段 Inspector、离线评分 | 开发实现和真实回放记录已归档，独立真值验收待开展 |
| 标定与 LIO | 双流采集、时间审计、静置／六面／平面法向标定、FAST-LIO 适配、时间插值、地图质量工具 | 合成回归与真实诊断；设备时间、外参与精度现场核验待完成 |
| 建图与配准 | ICP／GICP／NDT、SE(2)、扫描覆盖、固定点转台、多站位合并 | 历史实验与开发工具归档 |
| 运动与低姿态背景 | 射线历史差分、时空点带、聚类关联、背景距离与点身份审核 | 历史路线与独立实验材料归档 |
| Jetson 实时观察 | UDP 收包、XYZI 转换、最近 1／5／10／20／30 帧叠加 | 2026-11-11 现场浏览器观察与切换已记录 |

具体代码、证据与依赖见分类文档。历史报告中的 PASS、FAIL、SYNTHETIC、DIAGNOSTIC_ONLY、NOT_EVALUATED 等状态随实验记录保留。

## 启动 WebUI

环境：Windows、Python 3.12、Node.js 22.12+。双击根目录 `启动WebUI.bat`，或在 PowerShell 中执行：

```powershell
cd web
.\start.ps1
```

默认访问 `http://127.0.0.1:8765/`。主工作台 `/`、标注 `/annotate/`、球门调试 `/demo/`、细线 `/wire/` 共用顶部导航。Jetson 实时观察见 [工具说明](tools/na70_live_web/README.md)，Qt 双流录制见 [采集说明](docs/NA70_QT_CAPTURE.md)。

## 数据与版本

源代码、配置、锁文件、测试和文字证据按 Git 版本保存。原始 PCAP、视频、大型点云、缓存及外部模型按 [复现指南](docs/REPRODUCIBILITY.md) 准备。路线决策见 [Plan/README.md](Plan/README.md) 最新日期条目，历史实现可从 [提交历史](https://github.com/xiaoxiaoshibw/Lidar-NA70/commits/main/) 检索。

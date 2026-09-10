# NA70 固定俯视激光雷达移动目标感知系统

从 NA70 连续点云中找出移动点，将移动点聚类为目标并建立稳定轨迹，再从原始完整点云中裁剪每个轨迹对应的目标区域，识别目标类型。

```text
NA70 PointCloud2
  ├─ 背景减除 → Moving Points → 聚类 → 跟踪 ──────────┐
  └─ 原始完整点云 → 按 Track 裁剪 ROI → 多帧累积 → 分类器 ┤
                                                     ↓
                                  类别 + 位置 + 速度 + 轨迹
```

部署目标：Jetson Orin Nano 4GB + ROS2 Humble；Windows 本机负责代码编辑、离线数据处理与 WebUI 回放。

## 目录结构

| 目录/文件 | 说明 |
|---|---|
| `baseline/` | PCAP 解析与基线分析（协议、布局、检测器、基线结果） |
| `Plan/` | 开发计划：架构、分阶段实施、数据集与分类模型、验收矩阵、执行规范、WebUI 验收 |
| `radar_capture_data/` | 真实 NA70 采集数据（`.pcap` 不入库）与现场录像 |
| `ROS2/` | ROS2 Humble 工作区：驱动、检测节点、坐标与校验文档 |
| `tools/` | 运动分析、PCAP 回放、WSL2 验证等脚本及分析输出 |
| `web/` | Windows 本机 WebUI（中文、PCAP 三维点云回放，独立可运行） |
| `文档/` | 杂项备忘 |
| `NA70_固定雷达移动目标感知系统_开发文档.md` | 系统开发总文档 |
| `点云移动物体检测技术选型调研报告.md` | 技术选型调研 |
| `双楔镜激光雷达ROS2移动物体检测技术调研报告.md` | ROS2 专项调研 |
| `lidar_pcap_replay_research.md` | PCAP 回放调研 |
| `采集准备手册_5楼俯视行人识别.md` | 现场采集手册 |
| `ROS2迁移计划.md` | ROS2 迁移总计划 |

## 快速开始

- **WebUI 回放**（Windows 本机，不依赖 ROS2）：`web/start.ps1`，详见 `web/README.md`。
- **ROS2**：进入 `ROS2/` 按 `ReadMe.md` 构建；WSL2 试跑脚本见 `tools/wsl2_pcap_rviz.*`。
- **基线分析**：`baseline/run_baseline.py`。

## 文档入口

开发计划与验收标准见 `Plan/README.md`；系统全貌见 `NA70_固定雷达移动目标感知系统_开发文档.md`。

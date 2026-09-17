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
| `Plan/` | 开发计划与验收门禁（唯一入口：`Plan/MASTER_开发与验收总计划.md`） |
| `radar_capture_data/` | 真实 NA70 采集数据（`.pcap` 不入库）与现场录像 |
| `ROS2/` | ROS2 Humble 工作区：驱动、检测节点、坐标与校验文档 |
| `tools/` | 运动分析、PCAP 回放、WSL2 验证等脚本及分析输出 |
| `web/` | Windows 本机 WebUI（中文、PCAP 三维点云回放，独立可运行） |
| `文档/` | 杂项备忘 |

## 快速开始

当前主线是固定点转台多视角建图开发版：固定 NA70 旋转中心，按角度日志只融合停稳驻留段，输出估计角度诊断地图。它不是完整 SLAM，也不是正式转台精度验收。实现、模板、运行命令和证据见 `web/backend/turntable.py`、`web/backend/angle_log.example.csv`、`web/backend/turntable_calibration.example.json` 与 `Plan/evidence/turntable/20260915-development-final/`；现有三份操场 PCAP 的结果标签为 `DEVELOPMENT / DIAGNOSTIC / ESTIMATED_ANGLE`。

- **WebUI 回放**（Windows 本机，不依赖 ROS2）：`web/start.ps1`，详见 `web/README.md`。
- **ROS2**：进入 `ROS2/` 按 `ReadMe.md` 构建；WSL2 试跑脚本见 `tools/wsl2_pcap_rviz.*`。

## 文档入口

开发计划与验收标准见 `Plan/README.md` 与 `Plan/MASTER_开发与验收总计划.md`。

# 运动分析参考项目

当前实现没有直接嵌入这些项目的源码；算法结构和参数组织参考了以下公开项目，并针对 NA70 解码器和 Windows Web 后端重新实现。

- [Roadside LiDAR Vehicle Detection and Tracking Using Range and Intensity Background Subtraction](https://github.com/TeRyZh/Roadside-LiDAR-Vehicle-Detection-and-Tracking-Background-Subtraction)，Apache-2.0：固定式路侧雷达背景建模、ROI、前景去噪和聚类流程。
- [hdl_people_tracking](https://github.com/koide3/hdl_people_tracking)，BSD-2-Clause：固定雷达模式、人体候选簇约束和恒速轨迹设计。
- [lidar_obstacle_detector](https://github.com/SS47816/lidar_obstacle_detector)，MIT：点云过滤、聚类、AABB 和匈牙利关联流程。
- [multi_target_kf](https://github.com/mzahana/multi_target_kf)，MIT：恒速运动模型、匈牙利匹配、测量门控和轨迹生命周期。

本项目中的 NA70 协议解码、体素加权 DBSCAN、分析文件格式、FastAPI 任务接口和 Three.js 点级掩码显示是面向本项目的实现。

## 点云里程计与可观测性参考

本项目的 `registration_se2.py` 是 NumPy/SciPy 独立实现，没有复制以下论文或仓库源码。来源用于核对数学模型、退化处理策略与候选后端边界。

- Segal, Haehnel, Thrun, [Generalized-ICP](https://www.roboticsproceedings.org/rss05/p21.html), RSS 2009, DOI `10.15607/RSS.2009.V.021`：两侧局部表面协方差与 Mahalanobis 残差模型。
- Tuna et al., [X-ICP: Localizability-Aware LiDAR Registration for Robust Localization in Extreme Environments](https://arxiv.org/abs/2211.16335), 2022：从配准信息矩阵主方向判断局部可定位性，并抑制不可观测方向更新。
- Magnusson, Lilienthal, Duckett, [Scan registration for autonomous mining vehicles using 3D-NDT](https://doi.org/10.1002/rob.20204), 2007：以体素高斯分布表达目标点云并进行多分辨率匹配。
- [koide3/small_gicp](https://github.com/koide3/small_gicp), MIT：GICP/VGICP 的工程接口、协方差预计算和扫描到局部模型的数据流参考；当前 Windows 候选没有引入该依赖，Jetson 后端选择需另行验证。
- [xuqingyuan2000/LP-ICP](https://github.com/xuqingyuan2000/LP-ICP)：检索时确认仓库目前仅保留说明、没有可复用实现；其 README 同时警示部分可观测方向软约束可能引入波动，因此本项目采用截断特征分解冻结弱方向，不引入软约束。

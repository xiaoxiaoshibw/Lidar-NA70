# 文件内物理 yaw 先验版操场观测地图

该目录是当前三份 PCAP 的最佳诊断拼接候选：

- 高度过滤：相对每个 sweep 地面 `[-1.5 m, +4.5 m]`；
- 跨文件初始变换：左 `yaw=-23°、t=(-0.08,0)`，右 `yaw=+0.5°、t=(0,0)`；
- 文件内物理 yaw 先验：左总变化 `-6.2°`，右总变化 `+10.1°`；
- zhong2 不施加旋转补偿，作为参考坐标系。

这里的 yaw 表示雷达本体在操场中转动造成的整批点云坐标变化，不是前端视角变化。该版本仍标记为 `diagnostic_fixed_transform_hypothesis`，因为 yaw 先验来自地面块方位漂移，尚未通过独立闭环或外部真值验证。

产物：

- [map.ply](/D:/Code/radar/Plan/evidence/mapping-repair/20260914-observed-map-fixed-external-yaw/map.ply)
- [map.pcd](/D:/Code/radar/Plan/evidence/mapping-repair/20260914-observed-map-fixed-external-yaw/map.pcd)
- [预览图](/D:/Code/radar/Plan/evidence/mapping-repair/20260914-observed-map-fixed-external-yaw/stitched-playground-validated.png)
- [验证报告](/D:/Code/radar/Plan/evidence/mapping-repair/20260914-observed-map-fixed-external-yaw/stitch-validation.json)

# 固定变换假设版操场观测地图

该目录保存当前三份 PCAP 的高度过滤后拼接预览。过滤范围为每个 sweep 相对地面 `[-1.5 m, +4.5 m]`；左/中/右跨文件变换使用已有诊断假设：左 `yaw=-23°、t=(-0.08,0)`，右 `yaw=+0.5°、t=(0,0)`。

这张图对应雷达本体坐标随物理 yaw 改变后的统一地图坐标，不是前端相机视角变换。由于左、右文件内部仍有时变姿态，目录中的 `stitch-validation.json` 将 claim 标记为 `diagnostic_fixed_transform_hypothesis`，不能作为 R7、G4 或完整 SLAM 通过证据。

产物：

- [map.ply](/D:/Code/radar/Plan/evidence/mapping-repair/20260914-observed-map-fixed/map.ply)
- [map.pcd](/D:/Code/radar/Plan/evidence/mapping-repair/20260914-observed-map-fixed/map.pcd)
- [地图预览](/D:/Code/radar/Plan/evidence/mapping-repair/20260914-observed-map-fixed/stitched-playground-validated.png)
- [验证报告](/D:/Code/radar/Plan/evidence/mapping-repair/20260914-observed-map-fixed/stitch-validation.json)

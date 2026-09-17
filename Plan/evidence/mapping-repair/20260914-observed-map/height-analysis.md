# 2026-09-14 操场观测地图高度过滤与构建记录

## 结论

现有三份 PCAP 已构建为一张“已观测覆盖地图”。地图只表示 PCAP 实际观测到的区域，不补造未扫描区域，也不宣称完整 SLAM。

高度过滤在每个 sweep 内以有效点的 z 中位数作为局部地面基准，保留相对高度 `[-1.5 m, +4.5 m]`，排除相对地面超过 `4.5 m` 的高点集。该阈值由数据分析确定：中心文件高点比例近乎为零，左/右文件高点比例稳定约 13%–15%，与现场树冠和远处高处非刚性结构相符。

| 文件 | 原始有效点 | 保留点 | 排除高点 | 排除比例 |
|---|---:|---:|---:|---:|
| zhong2 | 4,908,394 | 4,908,385 | 9 | 0.0002% |
| zuo | 4,316,274 | 3,733,407 | 582,867 | 13.50% |
| you | 3,764,467 | 3,215,281 | 546,286 | 14.51% |

## 地图产物

- [map.ply](/D:/Code/radar/Plan/evidence/mapping-repair/20260914-observed-map/map.ply)
- [map.pcd](/D:/Code/radar/Plan/evidence/mapping-repair/20260914-observed-map/map.pcd)
- [map_metadata.json](/D:/Code/radar/Plan/evidence/mapping-repair/20260914-observed-map/map_metadata.json)
- [stitch-validation.json](/D:/Code/radar/Plan/evidence/mapping-repair/20260914-observed-map/stitch-validation.json)
- [stitched-playground-validated.png](/D:/Code/radar/Plan/evidence/mapping-repair/20260914-observed-map/stitched-playground-validated.png)

地图体素数为 182,340，体素尺寸沿用 `mapping.yaml`。当前拼接预览角域约 126.9°，并非完整 360° 操场覆盖。

另生成固定变换假设对照版：左侧 `yaw=-23°、t=(-0.08,0)`，右侧 `yaw=+0.5°、t=(0,0)`。其 [map.ply](/D:/Code/radar/Plan/evidence/mapping-repair/20260914-observed-map-fixed/map.ply)、[map.pcd](/D:/Code/radar/Plan/evidence/mapping-repair/20260914-observed-map-fixed/map.pcd) 和 [预览图](/D:/Code/radar/Plan/evidence/mapping-repair/20260914-observed-map-fixed/stitched-playground-validated.png) 用于查看三份扇形的空间关系，报告 claim 为 `diagnostic_fixed_transform_hypothesis`。

在该版本上又叠加了文件内物理 yaw 先验（左 `-6.2°`、右 `+10.1°`），当前最佳诊断候选位于 [observed-map-fixed-external-yaw](/D:/Code/radar/Plan/evidence/mapping-repair/20260914-observed-map-fixed-external-yaw/)；它仍不是自动定位通过结果。

## 质量边界

跨文件一次性刚体变换没有通过时变姿态检查：`zuo` 的分时段 yaw spread 约 13.6°，`you` 约 30.0°。因此 PLY/PCD 是“当前观测覆盖的诊断地图”，不能作为 R7 通过或完整 SLAM 证据。后续若要升级为可信地图，需要先做文件内分时段 yaw 补偿，再重新进行跨文件重叠验证。

这里的 yaw 是雷达本体在操场中的物理航向变化，点云坐标随传感器姿态整体改变，不是前端相机视角变化。`temporal_yaw.py` 已提供地面方位拟合试验；左/右约有 11.8°/9.9° 的候选变化，但静止基线残差和左右结构残差仍不足以作为生产补偿。

## 重现命令

```powershell
cd D:\Code\radar
.\web\.venv\Scripts\python.exe tools\analyze_playground_height.py `
  2\9.14_caochangzhong2.pcap 2\9.14_caochangzuo.pcap 2\9.14_caochangyou.pcap `
  --output-dir Plan\evidence\mapping-repair\20260914-observed-map\height-analysis
.\web\.venv\Scripts\python.exe tools\validate_playground_stitch.py `
  --center 2\9.14_caochangzhong2.pcap `
  --left 2\9.14_caochangzuo.pcap `
  --right 2\9.14_caochangyou.pcap `
  --min-height -1.5 --max-height 4.5 `
  --output-dir Plan\evidence\mapping-repair\20260914-observed-map
```

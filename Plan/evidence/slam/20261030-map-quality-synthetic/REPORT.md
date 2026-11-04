# 地图质量评估 SYNTHETIC 验证

- 地图：`Plan/evidence/slam/20261030-scene-synthetic/map.ply`（合成场景装配，80,873 点）。
- 参考：三块已知平面 + 5 个控制点；**不是真实操场真值**。
- 命令：

```powershell
web\.venv\Scripts\python.exe tools\evaluate_na70_map.py `
  --map Plan\evidence\slam\20261030-scene-synthetic\map.ply `
  --planes Plan\evidence\slam\20261030-map-quality-synthetic\planes.json `
  --control-points Plan\evidence\slam\20261030-map-quality-synthetic\control-points.csv `
  --threshold-m 0.05 `
  --output Plan\evidence\slam\20261030-map-quality-synthetic\evaluation.json
```

## 结果

| 平面 | 选中点 | 残差 P95 | ≤0.05 m 比例 |
|---|---|---|---|
| ground z=-1 | 11,009 | 0.00976 m | 100% |
| back x=8 | 19,063 | 0.00998 m | 100% |
| side y=4 | 11,103 | 0.01080 m | 100% |

控制点（5 个）：P50 0.0354 m、P95 0.1044 m、最大 0.1109 m；3/5 在 0.05 m 内。控制点不是按地图采样点挑的，所以后两个点距离较大，这是正常现象，也说明控制点验收要覆盖实际结构位置而不是只挑容易点。

## 重要限制

- 这次评估的平面和控制点来自**合成场景**，结果只验证距离统计与 ROI 过滤代码，不代表真实 NA70 地图精度。
- 每个平面必须给 `bounds`；不给时工具会把场景中所有点都算进该平面残差（首次运行 ground P95 3.66 m，就是墙面点被算进地面）。
- `REFERENCE_CONSISTENCY` 只说明两份地图一致，不排除共享系统误差；`PLANE_RESIDUALS` 依赖参考平面来源；`CONTROL_POINT_DISTANCE` 只覆盖控制点附近。
- 工具不做配准或缩放，两侧必须已在同一坐标系。

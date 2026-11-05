# 教室观测表面重建

日期：2026-11-05。Windows 本机 Python 3.12.10，Open3D 0.20.0、Plotly 7.1.0。状态 OBSERVED_SURFACE_PROTOTYPE。

已联网核对官方方法：https://www.open3d.org/docs/release/tutorial/geometry/surface_reconstruction.html 。使用 Ball Pivoting，邻域法向、相机朝向法向、统计离群过滤，三角最长边限制 0.4 m；不补全空洞。参数仅为本次可视化配置，非几何验收阈值，见 config.json。未增加第二套配准算法。输入源及哈希在 metrics.json，原始数据来源可沿上游记录追溯。

旋转＋沿用此前未标定轴向/位置假设，16,728 三角面；固定方向静止面 23,648 三角面；旋转－对照 24,279 三角面。网格顶点均由输入点支持，三角形内部为插值。颜色是参考坐标 Z，非真彩或已验证高度。三种场景独立，没有强行拼成同一地图；三面墙及物体语义没有验证。此结果展示了三维观测表面，但完整教室空间重建尚未完成；网格不能修正源坐标拼接误差。

复现：

```powershell
$env:OMP_NUM_THREADS='2'
$env:OPENBLAS_NUM_THREADS='1'
web/.venv/Scripts/python.exe Plan/evidence/slam/20261105-classroom-surface/build.py
web/.venv/Scripts/python.exe Plan/evidence/slam/20261105-classroom-surface/check-geometry.py
node Plan/evidence/slam/20261105-classroom-surface/check-viewer.cjs
```

新增 Open3D/Plotly 仅装入当前本机 web/.venv；项目依赖清单未修改。首次安装未结束时启动脚本因 ModuleNotFoundError 失败，安装完成后重跑成功；OBJ 导出报告不包含三角面法向，顶点法向已保存。初次 Playwright 缺默认 Chromium 可执行文件，改用本机 Edge 后浏览器检查成功：六个绘制层、三种场景、WebGL 画布、无 pageerror，截图 browser-preview.png。check-geometry.py 验证三份网格顶点与输入点一致、所有三角边不超配置；这些是程序输出检查，不代表几何准确度。

index.html 内嵌 Plotly，可离线打开；本次另启动 localhost:8875 查看服务，停止后仍可直接打开文件。PLY/OBJ 为网格模型，*_points.ply 是过滤后的点云。

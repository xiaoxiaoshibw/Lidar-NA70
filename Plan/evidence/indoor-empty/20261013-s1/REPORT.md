# S1 带家具空场模型更新

2026-10-13 用户指定替换为 S1-empty-with-furniture。

已核对对应会话：D:/Code/ldiar/captures/remote/cap_20261010_175227，304 帧，元数据时长 31.417239 秒，原始 14,931,203 点。会话身份及输入 SHA 与 human_fusion_lab/results/AUDIT.json 的 S1 行一致；生成前检查两份输入哈希，帧数和时长不符则拒绝。

交付 viewer.html（旋转/俯视/侧视、1 m 网格、点到雷达斜距、两点测距）、scene.ply、scene.npz、manifest.json、preview.png 和浏览器检查记录。旧版本保留在相邻 20261013 目录，本轮未修改输入和生产管线。

生成与验证命令（Windows 本机 Python 3.12 / Edge）：

```powershell
python Plan/evidence/indoor-empty/20261013-s1/build_scene.py
python Plan/evidence/indoor-empty/20261013-s1/check_viewer.py
```

延续原版 5 cm 体素和 REF_RT 配平契约；斜距是体素内原始 XYZ 到雷达的距离均值，单位米。持久性为出现至少半数帧（152/304），仅用于观察，不等于背景语义或删除门限。源点完整字段仍在原始会话，NPZ 保存代表源帧及点索引；不补不可见几何。

地面仍是既有配平参考，精度 NOT_EVALUATED；当前照片与 S1 家具逐一对应 UNVERIFIED。换用用户指定的更新空场不构成背景减除或跌倒识别验收。

数值结果见 manifest.json；浏览器结果见 browser_check.json。

## 地面圈线显示更新

按用户截图给近侧地面活动区加 5 px 黄色闭合轮廓，黑色衬线增加对比；轮廓落在现有地面参考 z=0，随视角投影。边界为人工示意，不是审核后的算法 ROI，不改变点云或检测。默认聚焦活动区，按钮支持返回全场。Edge 视角、测距、活动区切换检查通过。

用户更正活动范围仅为新截图黑色四边形内的小块地面。已将轮廓改为对应的较小四边形，保持 z=0 参考地面。顶点是截图视觉近似映射，未经浏览器视角或逐点坐标标定，不能当作精确 ROI。浏览器检查通过。

# P3 地面平面归一化回归

日期：2026-09-20。状态：Synthetic 回归通过；真实 PCAP 零命中仍保持 FAIL。

本轮新增的 `goal_detection.z_leveling` 仅对 detector 输入做鲁棒地面平面 z 归一化：
从有限、范围/ROI 内低分位点拟合 `z=ax+by+c`，斜率超出 YAML 门限时跳过；x/y、
原始 point provenance、宽度/高度/关联阈值均未放宽。归一化 Stage trace 保存斜率、
残差和跳过原因，候选输出再恢复到原始 sensor z 坐标。

运行命令：

```powershell
.\web\.venv\Scripts\python.exe tools\run_goal_synthetic_suite.py `
  --output Plan\evidence\goal-demo\20260920-p3-z-leveling-release `
  --scenario static --scenario translation_yaw --scenario disappear_reenter `
  --scenario density_drop --scenario missing_crossbar --scenario pitch_roll `
  --scenario tree_wall_fence --seed 0 --seed 1000 --frames 200
```

结果（`precision / recall`）：

- static：1.000/1.000（两个 seed）
- translation_yaw：1.000/1.000（两个 seed）
- density_drop：1.000/1.000（两个 seed）
- missing_crossbar：1.000/1.000（两个 seed）
- pitch_roll：1.000/1.000（两个 seed，修复前 recall 0.545/0.585）
- disappear_reenter：1.000/0.933、1.000/0.987；不可见区间错误 measurement=0、错误确认=0、ID switch=0
- tree_wall_fence：1.000/0.980、1.000/0.985

真实标注仍按 `20260920-p3-time-evidence-real-full/validation.json` 原样计为 FAIL，
不因 Synthetic 改善而改写 P3 状态。

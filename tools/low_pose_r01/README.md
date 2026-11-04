# 八段低姿态背景实验 r01

参考会话 `01a1357e-0153-7fc2-b32c-526bb3203e89` 的 pointset r01：冻结空场、FIT/CAL/HOLDOUT、三维最近背景距离、源点索引与 UNKNOWN。只读 D:/Code/ldiar，独立输出到本仓库 Plan/evidence/indoor-empty；未接入统一核心或总控制台。

```powershell
python -B -W error -m unittest discover -s tools/low_pose_r01 -p test_contract.py
# --out 必须是尚不存在的新目录；不覆盖历史证据。
python -B -W error tools/low_pose_r01/run.py --out Plan/evidence/indoor-empty/新的实验目录
python -B -W error tools/low_pose_r01/check_s8.py --out Plan/evidence/indoor-empty/新的实验目录
python -B -W error tools/low_pose_r01/server.py --out Plan/evidence/indoor-empty/新的实验目录 --port 8943
python -B -W error tools/low_pose_r01/check_browser.py --out Plan/evidence/indoor-empty/新的实验目录
```

当前结果：`Plan/evidence/indoor-empty/20261014-low-pose-r01/REPORT.md`，回放 http://127.0.0.1:8943/ 。

近地全保留仅为不确定性对照，不采纳为人体分割。S8部分标签回归与八段无标签录制分别统计；未训练模型、生成解剖关节或确认跌倒。三维最近背景差分仍有身份与物理地面资格缺口。原 registration gate 的 PASS 只是复用名义坐标的隔离数值诊断，不创建正式 VALID artifact。原094333失败保持。

灰/蓝/橙层只抽样用于浏览器显示，完整保留源行在每段 `retained_source_indices.npz` 中；frame-local索引和设备时间均保留。审阅导出只含用户实际选取的显示点，必须填写实际审核人；导出后未自动导入/评分或扩展为整簇标签。

技能实际读取：`C:/Users/30680/.codex/skills/ponytail/SKILL.md`、`C:/Users/30680/.codex/skills/scientific-toolkit-skill/SKILL.md`。无新增依赖。

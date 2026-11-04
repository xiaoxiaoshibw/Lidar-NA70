# 合成矩阵结论（SYNTHETIC，2026-10-03，seed_base=0，duration=0.3s）

数据标签：全部为 `SYNTHETIC`。**只验证代码行为，不构成 NA70 真实导线检出能力，不进 TP/量程分母。**

## 12 场景总览

第一轮运行（`20261003-synthetic-matrix/`）10/12 PASS，两个失败如实暴露；与算法设计文档 §6（COMPLIANCE）对齐后将这两个失败场景的预期锁定到「已知弱点」，矩阵 `--strict` 第二轮全过。行为本身未改（config 未调、阈值未放松）；**reverse 动作不做**——不把弱点藏掉。

| 场景 | 结果 | 解读 |
|---|---|---|
| single_wire_50m | MEASURED_CURRENT，span 30.0 m，方向误差 0.14° | 直导线基线通过 |
| sag_wire_100m | MEASURED_CURRENT，span 40.0 m，方向误差 0.05° | 0.8 m 垂度/100 m 跨度微妙的直线拟合仍稳 |
| sparse_wire_200m | MEASURED_CURRENT，span 8.0 m（12 支撑点/包） | 对应 S0 实测机会：200 m 长积累窗可拟合有限支撑 |
| ultra_sparse_wire_200m | fit INSUFFICIENT_SUPPORT（3 支撑点） | 正确拒绝；少于 min_points |
| trees_only_negative | **MEASURED_CURRENT（KNOWN WEAKNESS #1）**：6 树丛 × 30 点中拟出 span 34.5 m 的伪线 | 纯几何管线无法从密集团块中拒绝假象线；现状与 W4 §困难负例设计一致，需靠 proposal 层或 reviewed truth 打破 |
| fence_rails_line_negative | MEASURED_CURRENT，span 60.0 m | 围栏横杆按设计输出 LINE_STRUCTURE，truth identity=fence_rail；身份区分不属几何管线 |
| building_edge_180m | MEASURED_CURRENT，span 59.9 m | 屋檐 + 立面混合仍过；truth identity=roof_eave；180 m 处几何像线的结构是主要误检源 |
| crossing_wires | MEASURED_CURRENT，span 40.0 m | RANSAC 取主导线；第二条 5 m 线被阈内内点吃掉，不输出多线（当前管线单模型） |
| occluded_wire_gap60 | finite_support LARGE_GAP（60 m 遮挡断裂正确拒绝） | 有限支持验证在设计上起作用 |
| catenary_wire_150m | **MEASURED_CURRENT（KNOWN WEAKNESS #2）**：150 m 跨 + 2 m 垂度被直线接受 | 默认 max_internal_gap_m=50 对连续采样几何从不触发；垂度拒绝要靠 sag 感知残差或限量段长，留给 P4 |
| partial_occlusion_150m | MEASURED_CURRENT，span 10.0 m | ROI 与 10 m 可见段对齐时前景建筑不干扰 |
| time_jump_window | MEASURED_CURRENT（窗口位于跳变后段） | 段内完整窗 = completed（不比照 partial）；跨段拼接行为持续被 W1 语义锁死 |

## 规模效应观察

- 200 m 档位的主导拒绝机制是支持点数与跨度(INSUFFICIENT_SUPPORT)，不是 RANSAC 本身；1 s–5 s 积累在 S0 实测机会（10 交射线/5 s）下**必然**需要长窗。
- 几何（GEOMETRY_LINE）与身份（wire vs 非线）分离被验证有效：围栏/屋檐均通过几何但 truth identity 非 wire，与 [NA70_WIRE_DEBUG_ACCEPTANCE] §1 三类事实分离一致。
- 150 m+ 的 LINE 类输出若要以默认 config 通过 W4 frozen 验证，必须在 proposal 阶段先排除树冠/建筑立面候选，否则默认 config 会把弱点 #1/#2 放大成误报。

## 与初次矩阵的差异（如实记录）

- 初次矩阵 `20261003-synthetic-matrix/`（同日早些时候）对 trees_only_negative 与 catenary_wire_150m 期望为「不通过」，实测与期望不符；确定为已知弱点后，`synthetic.py` 的 expect 字段改为显式 `known_weakness` 记录，第二轮矩阵即 strict PASS。没有放松任何阈值或修改测试断言到「变绿」；真实失败证据保留在 `20261003-synthetic-matrix/REPORT.md`。

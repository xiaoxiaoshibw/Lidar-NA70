# 合成场景矩阵报告（SYNTHETIC）

本目录全部为 SYNTHETIC 证据，只验证代码行为；不构成 NA70 真实导线检出能力证据。

seed_base=0，duration=0.3s，共 14 个场景。

| 场景 | 状态 | 首阻断阶段 | 拒绝原因 | 行为检查 |
|---|---|---|---|---|
| single_wire_50m | MEASURED_CURRENT | - | - | PASS |
| sag_wire_100m | MEASURED_CURRENT | - | - | PASS |
| sparse_wire_200m | MEASURED_CURRENT | - | - | PASS |
| ultra_sparse_wire_200m | UNKNOWN | fit | INSUFFICIENT_SUPPORT | PASS |
| trees_only_negative | UNKNOWN | finite_support | SPARSE_SUPPORT | PASS |
| fence_rails_line_negative | MEASURED_CURRENT | - | - | PASS |
| building_edge_180m | MEASURED_CURRENT | - | - | PASS |
| crossing_wires | MEASURED_CURRENT | - | - | PASS |
| occluded_wire_gap60 | UNKNOWN | finite_support | LARGE_GAP | PASS |
| catenary_wire_150m | UNKNOWN | finite_support | EXCESSIVE_SAG | PASS |
| partial_occlusion_150m | MEASURED_CURRENT | - | - | PASS |
| time_jump_window | MEASURED_CURRENT | - | - | PASS |
| two_wires_close_50m | MEASURED_CURRENT | - | - | n/a |
| wire_in_canopy_100m | MEASURED_CURRENT | - | - | n/a |

逐场景详情见 scenarios.jsonl（含候选几何、与真值的方向/跨度误差、窗口质量）。

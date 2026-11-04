# W4-A 提案对照实验：slab vs Hough vs PCA（2026-10-03）

范围：angular-slab connected-component、正向投影 HoughLinesP、邻域 PCA 线性度三种 full-FOV line proposal 方法。数据集：5 个 SYNTHETIC 场景（two_wires_close_50m, wire_in_canopy_100m, sparse_wire_200m, trees_only_negative, building_edge_180m）+ 1 个真实静态 PCAP 窗（zhong2 f600 duration=1.0s = 1000 packets/78 225 valid）。真实行数据标签 `REAL_PCAP_UNKNOWN_LINE`，SYNTHETIC 行单独 SYNTHETIC。**没有 HV-line identity 判定。**

## 实测对照表（source：compare.jsonl）

### SYNTHETIC（seed_base=0，duration=0.3 s≈全窗）

| 场景 | slab | hough | pca |
|---|---|---|---|
| two_wires_close_50m | **2 提案 / 2 评估 / MEASURED span 40.04 m / dir_err 0.14°** | 10 提案 / **0 评估**（默认 hough_threshold=10 过严，行像素成员半径不足） | 0 提案 |
| wire_in_canopy_100m | 1 提案 / MEASURED span 50.02 / dir_err 0.055° | 13 / 0 | 4 / MEASURED span 6.64（**span 43.4 m 少测**—pca 区域只盯主导附近） |
| sparse_wire_200m | 0 提案 | 0 | **1 / MEASURED span 7.98 m**（slab/hough 在 200 m 支撑上一无所获，pca 靠 KNN 主导 linearity 出线） |
| trees_only_negative | 1 / UNKNOWN（finite_support SPARSE_SUPPORT，正确） | 4 / 0 | 0 |
| building_edge_180m | 1 / MEASURED span 60.01 | 4 / 0 | 1 / MEASURED span 10.01 |

### 真实 zhong2 1s 窗（end_frame=600）

| 方法 | 提案数 | 评估数 | 状态 |
|---|---:|---:|---|
| slab | 0 | 0 | NO_PROPOSAL（如 20261003-w4-zhong2 预测） |
| **hough** | **25** | **18**（7 提案 no-fit-return） | **MEASURED_CURRENT，span_m=20.36，nearest_m=76.53**（identity UNKNOWN_LINE） |
| pca | 2 | 2 | UNKNOWN，reject=INSUFFICIENT_SUPPORTx1;LOW_COVERAGEx1;SPARSE_SUPPORTx1 |

## 断言（不做过度推断）

- **Hough 是真实 1s 窗上唯一能给 MEASURED_CURRENT 的 full-FOV 方法**。与 NA70_WIRE_ALGORITHMS §5 的期望一致；它说明 2D 投影下的 Hough 在 NA70 稀疏但局部连通的扫描模式里比 3D 邻域更敏感。
- **Slab 在真实 data 上如 W4_FINDINGS 预测失效**（0 提案）；该基线已可告一段落，P4 主对照不应再以 slab 为底。
- **PCA 在 200 m 支撑的 sparse_wire_200m 上胜出**（slab/hough 0），但在真实 data 上因太严格线性度 + 区域内覆盖条件被 finite_support 拒；pca 当前对真实稀疏回波不友好。
- **Hough 在 synthetic 上 0 evaluated** 是默认 hough_threshold=10 与会丢弃少于 10 px 行的行为，**不是 bug**；记录为「参数与 SYNTHETIC 物理尺度不配合」，**不调默认值到能过 syn**——真实 data 才是主证据。若后续需要 Hough 在 SYNTHETIC 做 ground-truth 预验证，新增专用 scenario + 独立 HoughConfig 实例，不污染默认。
- wire_in_canopy_100m 下 pca span 只 6.6 m（truth 50 m），提示 pca 区域在大片 canopy 里主导 linearity 只能捕获局部；真实 HV 任务不能依赖 PCA 把 100 m 长线一次抓回。
- **真实 zhong2 的 Hough 候选身份仍是 UNKNOWN_LINE**：span 20.4 m/nearest 76.5 m 对应哪一棵树/杆/断面需要 reviewed truth 才能确定；**不写成「重识别了某条某场景物体」**。

## 下一入口

W4-B：P3 真实 HV 数据到位后，在冻结 `HoughConfig + ValidationConfig` 下按 NA70_WIRE_DEBUG_ACCEPTANCE §5 一次 frozen 评分（TP/FN/FP、每分钟误报）；本轮 slab/hough/pca 的工程学习作为 P4 骨架（`na70_core.wire.proposal_*`）。

## 数据产物

`compare.jsonl`（18 行 SYNTHETIC + 3 行 REAL_PCAP_UNKNOWN_LINE）、`manifest.json`（种子、命令、源码与环境哈希）、`REPORT.md`（同表）、本文件。

# 2026-09-14 Sweep G1 与 SE(2) G2 开发记录

## 1. 本轮结论

- G1 `PASS`：已建立包级时间与原始字段索引、覆盖驱动 `SweepBuilder`、固定 100/200 包对照、四份真实 PCAP 报告和真实 PCAP 批量/分块流式一致性哈希。
- G2 `IN_PROGRESS（真实回归 FAIL）`：已实现六自由度可观测评分、几何候选分层和受约束 `SE(2)` 的 point-to-plane、robust GICP、multi-resolution NDT；合成回归通过，但操场真实抽样绝大多数仍不满足冻结质量门限，左右航向符号也没有形成可信对照。
- R7 保持 `IN_PROGRESS（基线 FAIL）`；未进入生产里程计、地图、回环或完整 SLAM。

## 2. 核心设计

`web/backend/decoder.py` 保留原 100 包 WebUI 帧接口，同时在内部索引每包 capture time、device time、原始 payload offset、factory code 和工厂角偏移。公开帧 API 不返回这些大数组，因此保持兼容。

`web/backend/sweeps.py` 以 `角码分箱覆盖 + 最小包/点数 + 最大时间窗` 结束 sweep。默认阈值全部位于 `web/backend/mapping.yaml`。一个包内 200 点只共享包时间，元数据明确写 `packet_time_only`，不生成点级时间。capture time 负责 sweep 边界；device time 回退仅标记，不用于速度。完全重复记录标记后去重。NA70 当前 payload 没有已确认的设备序列号，因此 PCAP 捕获序号不冒充丢包序列；只有输入适配器提供可信 `sequence_id` 时才检测 gap。

`web/backend/registration_se2.py` 使用配准信息矩阵特征分解报告 `x/y/z/roll/pitch/yaw` 得分；`SE(2)` 解算只包含 yaw、x、y。弱特征方向通过 TSVD 投影为零更新，不用软约束制造运动。候选先按局部法向显式选择能约束 x/y/yaw 的非地面结构，避免大量水平地面主导对应与质量统计。GICP 使用源/目标局部协方差、0.8/0.4/0.25 m 多尺度金字塔与 Mahalanobis 残差；NDT 使用 2.0/1.0 m 多分辨率体素高斯；三者均使用稳健核并执行同一对应、残差和运动门禁。

## 3. 论文与 GitHub 来源

本轮先查论文、后查 GitHub；源码均为项目内独立实现，没有复制参考仓库代码。完整许可说明见 `web/THIRD_PARTY_NOTICES.md`。

- Segal, Haehnel, Thrun, Generalized-ICP, RSS 2009, DOI `10.15607/RSS.2009.V.021`：采用两侧局部协方差的概率残差思想。
- Tuna et al., X-ICP, arXiv `2211.16335`：采用信息矩阵主方向的局部可定位性判断与弱方向冻结思想。
- Magnusson et al., 3D-NDT, DOI `10.1002/rob.20204`：采用体素高斯与多分辨率候选。
- `koide3/small_gicp`（MIT）：核对 GICP/VGICP 的协方差预计算与工程接口；当前未引入该依赖。
- `xuqingyuan2000/LP-ICP`：仓库目前没有公开实现，并明确警示部分可观测方向软约束可能波动；因此本轮不采用软约束。

## 4. G1 真实 PCAP 结果

覆盖驱动配置：最少 200 包/10000 有效点、最多 1000 包/0.75 s、俯仰码每 4 码和航向码每 8 码分箱、目标 20000 个角域分箱、完成率 0.90。

| 输入 | 包数 | sweep | 完整 | 时长中位数 s | 0.3/0.6/1.0 m 相邻覆盖中位数 | 时间/包异常 |
|---|---:|---:|---:|---:|---|---|
| 中 | 129738 | 281 | 279 | 0.452 | 0.591 / 0.740 / 0.838 | 无 |
| 左 | 122095 | 244 | 243 | 0.503 | 0.570 / 0.751 / 0.847 | 无 |
| 右 | 121036 | 231 | 230 | 0.540 | 0.585 / 0.751 / 0.829 | device time `26.766→26.512 s`，位于 sweep 19；capture time 单调 |
| 移动 | 94390 | 217 | 210 | 0.434 | 0.465 / 0.680 / 0.797 | source index 76101 为完全重复记录，标记并去重 |

固定 100 包在四份数据上的 0.3 m 覆盖中位数只有 `0.213–0.255`；覆盖驱动 sweep 提升到 `0.465–0.591`，没有扩大对应距离。四份 PCAP 的批量与变化分块（1/7/64/3/128/11 包）流式输入边界哈希完全一致，见 `streaming-consistency.json`。

## 5. G2 真实抽样结果

每份 PCAP 均匀抽取 12 对相邻 sweep，使用当前 `mapping.yaml` 冻结质量门限。

| 输入 | point-to-plane | robust GICP | multi-resolution NDT |
|---|---|---|---|
| 中 | 0/12 接受 | 0/12 接受 | 0/12 接受 |
| 左 | 0/12 接受 | 0/12 接受 | 0/12 接受 |
| 右 | 1/12 接受 | 1/12 接受 | 1/12 接受 |
| 移动 | 0/12 接受 | 0/12 接受 | 0/12 接受 |

非地面选择后的 12 对抽样中，左转三种候选 yaw 中位数为正、右转为负，说明非地面结构携带一定方向信息；但正式接受率仍近乎为零。随后对左 243 对、右 230 对全部相邻 sweep 复跑：左侧三种方法接受数均为 0，右侧均为 3；左侧候选正向比例仅 49%–52%，右侧为 37%–49%。再将比较跨度扩大到 10 个 sweep（约 5 s）仍没有提升接受数或符号一致性。当前数据不能证明航向连续正确。详细逐对 Hessian、结构分类、候选位姿和拒绝原因见 `../20260914-se2/*.json`、`../20260914-se2-full/*.json` 与 `../20260914-se2-stride10/*.json`。

## 6. 运行命令

```powershell
cd D:\Code\radar
.\web\.venv\Scripts\python.exe tools\analyze_sweep_coverage.py `
  2\9.14_caochangzhong2.pcap 2\9.14_caochangzuo.pcap `
  2\9.14_caochangyou.pcap 2\9.14_caochangyidong.pcap `
  --output-dir Plan\evidence\mapping-repair\20260914-sweeps
.\web\.venv\Scripts\python.exe tools\verify_sweep_streaming.py `
  2\9.14_caochangzhong2.pcap 2\9.14_caochangzuo.pcap `
  2\9.14_caochangyou.pcap 2\9.14_caochangyidong.pcap `
  --output Plan\evidence\mapping-repair\20260914-sweeps\streaming-consistency.json
.\web\.venv\Scripts\python.exe tools\compare_se2_registration.py `
  2\9.14_caochangzhong2.pcap 2\9.14_caochangzuo.pcap `
  2\9.14_caochangyou.pcap 2\9.14_caochangyidong.pcap `
  --output-dir Plan\evidence\mapping-repair\20260914-se2 --pairs 12
```

## 7. 下一项可验证实验

G2 不能通过，因此不进入 R7 生产融合。下一份采集需要在同一连续文件中包含：中心静止、可记录角度的左转并回中心、同幅度右转并回中心、沿已量取路线平移并返回；每次开始/停止和已知角度标记必须记录时间。雷达保持水平和固定高度，并让立柱、扶手、顶棚边缘持续进入视场。冻结配置后，先验证左右累计 yaw 符号相反且中心段不漂移，再评估平移。若仍只有远场地面，应引入 IMU/轮速/视觉之一或调整安装视向，不能继续放宽 ICP 门限。

## 8. 最终工程回归

```text
Python 3.12.10
pytest tests -q: 120 passed, 2 dependency deprecation warnings, 73.83 s
npm.cmd run build: TypeScript + Vite PASS, 756.24 kB main chunk warning
git diff --check: PASS, only existing LF-to-CRLF notices
```

两条 Python 警告来自 FastAPI/Starlette 测试客户端依赖弃用，不是本轮算法失败。前端主包大小提示为既有性能提示，不影响构建通过。源码哈希见 `source-hashes.json`。

# 2026-09-12 采集准备与预检交付

此文件为新增证据，不改写历史 final-status.md 或失败任务。

## 实现与边界

新增 CollectionWorkbench、preflight.py/preflight.yaml、capture_validation.py、test_preflight.py 与 tools/preflight_capture.py；修改 app.py、decoder.py、mapping_jobs.py、main.tsx、api.ts、style.css、mapping.spec.ts 和 playwright.config.ts。沿用上传、状态轮询、POST /api/mapping/jobs 与唯一生产配准实现。未修改任何配准门限。

表单支持固定/手动模式、五项检查、起动/返回秒数、停顿与旋转区间、路线、场景干扰、设备及初始化帧数。上传自动解码诊断，表单修改自动通过 PUT /api/captures/{id}/preflight 保存到 meta.json；任务创建时固化快照到 job.json 与 validation.json。旧任务明确显示“未预检”，不会伪造历史采集声明。任务页面支持复查自身快照。

诊断包含 SHA-256、大小、帧数、时长、帧率、包数、捕获与设备时间倒退位置、帧内点间距、近距点计数、重复射线距离标准差与采样帧间最近邻距离。抽样预算写入 YAML 和结果；近场 10m 是距离提示，不是已标定定位阈值。重复射线波动仅在声明固定视点时作为静止场景诊断，不能解释为传感器噪声标定。运动事实仍来自用户记录，点云变化不自动证明自身移动。

## P0–P3

- P0：全量回归通过；取消、持久化失败、恢复与原子导出由现有测试覆盖。逐个核对本轮 SYNTHETIC 证据包所有成员哈希（每包 1066 个成员），job/validation 快照一致。
- P1：增加包内时间异常位置和采集抽样诊断；原有逐帧覆盖率、预处理数量和失败原因保持。
- P2：保持 KISS-ICP/GLIM 独立实验状态与历史失败证据，未接入生产，未重新安装或替换候选。
- P3：validation 自动附加正式静止漂移、正式移动配准比例及连续正式跟踪时长。初始化、失败和不同坐标段不作为静止漂移成功证据。墙厚、双层墙、固定物体重影、返回起点容差验收明确为未评价：需要真实采集、固定结构 ROI 和事先冻结标准。P3 的完整几何验收尚未完成，不能宣称全部开发完成。

## 真实输入

006 SHA-256：5ce9d5999051ae9ee6f42d067c70e50aaa3fa8272634cdf8ad64f82657673409。
本轮只做真实预检：1551 帧，155.007858 秒；状态“时间戳异常”，并保留 006 全程静止、不能评价真实移动的判定原因。没有新真实 mapping 任务。
历史真实任务 f48f7a3c-2682-4899-a78d-8f3e79adeb48 不变：初始化 300、正式配准 0、失锁 1249；未经本轮重新计算，仍以历史证据为准。

## 命令和结果

环境 Windows / Python 3.12.10（web/.venv）/ Node v24.14.0 / npm 11.9.0。Git 基线 e131fdf832b79ac51fe408974b422e806eecb048，已有大量未提交改动，本轮源码哈希见 preflight-source-hashes-20260912.json。

从 web：
```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pytest tests -q
```
104 passed / 2 个依赖弃用警告，日志 preflight-tests-geometry-20260912.txt。

从 web/frontend：
```powershell
npm run build
$env:NA70_E2E_BASE_URL='http://127.0.0.1:8767'
npx playwright test --project=mapping
```
构建通过，有已有的 chunk 大小警告；浏览器 2 passed / 1 skipped。真实用例没有设置 NA70_MAPPING_E2E=1，跳过不计入通过。日志 preflight-build-release-20260912.txt、preflight-browser-release-20260912.txt。浏览器使用同一后端、隔离测试目录，服务仅绑定 localhost。

独立服务启动（web 目录）：
```powershell
$env:NA70_DATA_DIR='D:\Code\radar\web\test-results\preflight-isolated-20260912'
.\.venv\Scripts\python.exe -m backend.run --port 8767 --no-browser
```

真实预检复现（仓库目录，工具拒绝覆盖已有输出）：
```powershell
.\web\.venv\Scripts\python.exe tools/preflight_capture.py 2/006.pcap Plan/evidence/mapping-repair/20260911-repair/preflight-006-release-20260912.json
```

## 失败与操作限制

中间一次测试收集失败：缺少 yaml 模块，日志 preflight-tests-final-20260912.txt 保留。已改成与 mapping.yaml 相同的 JSON-compatible YAML 读取方式，不增加依赖，最终全量回归通过。
自动审批拒绝重启原 8765 服务，原因仅返回 blocked by policy；保留原服务，改用 8767 独立实例完成测试。8765 后端仍需受控重启才能加载本轮 Python 修改。

## 门禁与下一步

R0–R6 PASS；R7 BLOCKED（等待真实移动采集）；R8 NOT_RUN；完整 SLAM NOT_STARTED。下一步采集真实平移路线并记录静止/运动/停顿/返回时间和近场固定结构，冻结标定参数、几何 ROI 与容差后完成 R7 和 P3 几何验收。无新 PCAP，不启动完整 SLAM，不执行 Jetson。


## P3 离线几何工具补充

新增 web/backend/geometry_validation.py、tools/measure_mapping_geometry.py 和 web/tests/test_geometry_validation.py。读取现有地图分块，不运行配准，不合并坐标段。合同需显式提供 regions，每个区域包含 name、segment_id、minimum、maximum、origin、normal、histogram_bin_m、max_thickness_m、min_layer_separation_m、min_peak_fraction、min_points。

命令：
```powershell
.\web\.venv\Scripts\python.exe tools/measure_mapping_geometry.py <job目录> <冻结合同.json> <新证据.json>
```

工具输出厚度、是否达到合同厚度上限、层峰位置、双层候选、合同内容与 SHA-256、地图分块哈希。真实固定物体语义及重影仍需指定正确 ROI 和复核；没有合同不猜测墙面或生成 PASS。SYNTHETIC 单墙/双墙/稀疏 ROI 测试单独标记；不替代真实验收。


最终全量回归：104 passed, 2 warnings in 55.87s。后续只更新文档，未再修改代码。

本轮浏览器 SYNTHETIC 任务：7dfdbee5-c810-43b9-8721-abcbf6a34d08；证据包 1066 成员哈希全部通过，见 preflight-bundle-release-20260912.json。前一轮任务 84e99eab-42dc-4a3c-946b-8f2b2ddab2cb 同样保留。

几何端到端命令：
```powershell
.\web\.venv\Scripts\python.exe tools/measure_mapping_geometry.py web/test-results/SYNTHETIC-geometry-job Plan/evidence/mapping-repair/20260911-repair/SYNTHETIC-geometry-contract-20260912.json Plan/evidence/mapping-repair/20260911-repair/SYNTHETIC-geometry-result-20260912.json
```
SYNTHETIC 单墙 1000 点，P95–P05 厚度 0.0031988234m，无双层候选；仅验证工具，不构成真实验收。

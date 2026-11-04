# W1 工具开发与验证记录

日期：2026-09-21。环境：Windows / Python 3.12.10 / NumPy 2.5.3 / Matplotlib 3.11.2。

**原始回波诊断工具已实现并跑通；W1 真实细线回波归属仍 NOT_EVALUATED。** 尚未取得指定细线 PCAP、独立目标位置、静止依据及匹配背景录制。`1.pcap` 用作真实协议/工具验证，不能自动认定其中有细线或由这些数字推断导线探测能力。

## 1. 已实现范围

- [命令行入口](../../../../tools/audit_thin_wire.py)、[独立配置](../../../../tools/thin_wire_config.yaml)、[核心](../../../../web/backend/thin_wire.py)和[绘图](../../../../web/backend/thin_wire_plot.py)。
- 单帧与 0.5/1/2/5 秒包级设备时间窗；时间缺失、倒退、大间隔分段；原始帧/点/包索引、时间和码值可追溯。
- 原始 XYZ/intensity 不过球门距离/高度规则、不体素化；无效零距离射线保留并标 mask，不凭无效点输出目标。
- 人工目标/背景框、有限独立参考段的位置一致统计、不同来源帧/包数、首末时间及来源年龄；背景 PCAP 独立扫描和分段。
- 同源 NPZ、窗口/逐包 JSONL、三视图、目标及每个背景区分别放大、时间/计数图、报告与输入/源码/config/产物哈希。
- 不覆盖已有目录，超预算拒绝并记录错误；不将 ROI/参考走廊计数自动升级成细线回波。

契约与使用说明：[契约](../../../../docs/THIN_WIRE_W1_CONTRACT.md)、[运行说明](../../../../docs/THIN_WIRE_W1_USAGE.md)。绘图依赖及其传递依赖已加入现有 requirements 锁文件，按当前安装版本核对通过；未做新环境联网安装验证。

## 2. 工程验证

| 检查 | 实际结果 |
|---|---|
| 最终 W1 + decoder 直接回归 | 37 passed（24 个 W1 case、13 个 decoder case），3.47 s |
| 最后图形刻度调整后的图形用例 | 1 passed，1.83 s；不改点处理逻辑 |
| 全量后端的一次运行 | 292 passed、1 failed，91.72 s；保留失败，不写成全量通过 |
| 全量失败单项与当时 W1 的独立重跑 | 24 passed（转台 1 项 + 当时 W1 23 项），6.57 s |
| 真实 PCAP 工具实跑 | 五种窗口均完成，细线归属均 NOT_EVALUATED |
| 原始证据复核 | 18 个产物哈希；五窗各 96 个导出点与正式解码对照；索引唯一、有效 mask 和时间边界全量核对 |

全量失败为 `test_turntable_api_job_survives_service_restart`：转台导出枚举到 `job.json.tmp`，原子写入替换后 ZIP 再读文件出现 FileNotFoundError。W1 不调用该链；本轮未改转台实现。单项重跑通过不能抹去原失败，后续维护仍需解决该竞态。记录见 [运行摘录](full-suite-observation.txt)。

最早定向测试还遇到现有 pytest 用户临时目录访问限制，获准在沙箱外重跑后通过；后续使用新的工作区 test-results 临时目录。该错误属于测试环境，未改测试断言规避。两条 FastAPI/Starlette 弃用警告保留。

定向命令（工作目录 `web`；临时目录每次用新名称）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_thin_wire.py tests/test_decoder.py -q --tb=short --basetemp=test-results/w1-release-20260921-v4 -o cache_dir=test-results/w1-cache-20260921-v4
```

日志：[定向测试](targeted-pytest-final.log)、[图形测试](visual-pytest.log)、[真实工具运行](real-tool-release-v2.log)。前端和 ROS2 未改，本轮未运行前端构建、E2E 或 Jetson 测试；耗时是测试/开发环境用时，不作为性能验收。

## 3. 真实输入对照

输入：`radar_capture_data/1.pcap`。

SHA-256：`b17d7c17926e670b3d89ba0774c4140feb810e4935971ab57760f4bdd00636cd`。

窗口终点均为 frame 50，默认无目标 ROI/参考段，原始 sensor 坐标。所有窗口设备时间质量为 ok；下表是整个窗口的有效回波，**不是细线支撑点数**，且窗口彼此重叠。

| 请求窗口 | 所选首末包实际跨度/s | 原始射线数 | 有效回波 | 零距离射线 |
|---|---:|---:|---:|---:|
| 单帧 | 0.099 | 20000 | 6214 | 13786 |
| 0.5 秒 | 0.499 | 100000 | 35329 | 64671 |
| 1 秒 | 0.999 | 200000 | 69441 | 130559 |
| 2 秒 | 1.999 | 400000 | 137536 | 262464 |
| 5 秒 | 4.999 | 1000000 | 347541 | 652459 |

最终产物：[原始诊断报告](../20260921-w1-release-v2/REPORT.md)、[manifest](../20260921-w1-release-v2/manifest.json)、[五秒三视图](../20260921-w1-release-v2/figures/primary-f000050-5s-overview.png)。之前 smoke/final/release 目录保留为开发版本；当前代码对应 `20260921-w1-release-v2`。

仓库根目录实跑命令（不要原样复用已存在的输出目录）：

```powershell
& .\web\.venv\Scripts\python.exe tools\audit_thin_wire.py --pcap radar_capture_data\1.pcap --end-frame 50 --output Plan\evidence\thin-wire\20260921-w1-release-v2
& .\web\.venv\Scripts\python.exe Plan\evidence\thin-wire\20260921-w1-development\verify_evidence.py Plan\evidence\thin-wire\20260921-w1-release-v2
```

核验工具只检查原始对应与产物完整性，不能给出目标归属。详细核验文件由上述命令按运行目录名生成，重复执行不会覆盖已保存的核验结果。

## 4. 下一步

用户指定细线 PCAP 及目标位置/线径/姿态，补充固定机位依据和背景对照后，用独立实验配置执行 W1，并在重复录制上复核。证据不足时保留 NOT_EVALUATED；W2 距离阶梯、W3 线拟合、W4 全视场和 W5 运动实时本轮均未开始。

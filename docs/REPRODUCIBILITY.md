# 安装、数据与复现

## WebUI

环境：Windows、Python 3.12、Node.js 22.12+、PowerShell。根目录运行 `启动WebUI.bat` 或进入 `web` 执行 `./start.ps1`。默认地址 `http://127.0.0.1:8765/`。锁文件为 `web/requirements.lock` 与 `web/frontend/package-lock.json`。

已有环境下运行：

```powershell
cd web
.\.venv\Scripts\python.exe -m pytest tests -q
cd frontend
npm ci
npm run build
```

浏览器测试需要后端和指定录制，执行 `npx playwright install chromium` 和 `npm run test:e2e`。具体录制、标注与测试用例对应。

## 算法核心

从仓库根目录安装：

```powershell
python -m pip install -e ./packages/na70_core
python -m pip install -e "./packages/na70_core[wire]"
```

第二条安装细线拟合的可选依赖。工具参数见 `--help`，实验完整命令见对应报告。标定操作见 [工作流](NA70_CALIBRATION_WORKFLOW.md)。

## 数据与外部依赖

| 材料 | 用途与恢复方式 |
|---|---|
| PCAP | 按报告文件名、SHA-256 和帧范围恢复，导入 Web 或传入 CLI |
| IMU 和会话清单 | 保留 JSONL、主机时间、manifest、来源身份与时间等级 |
| 人工真值 | 按契约准备独立目标位置、ROI、动作时间和审核标签 |
| 点云、视频和结果包 | 已跟踪历史证据随 Git 保存；其余从原设备／备份恢复或按冻结配置重跑 |
| 外部源码与模型 | FAST-LIO、GLIM 等版本见 `tools/lio/upstreams.repos`，其他历史依赖见 [路线索引](ALGORITHM_HISTORY.md) |

本仓库保存源码、配置、锁文件、测试、报告及既有历史证据。新增 PCAP、视频、点云、压缩包、缓存和环境目录由 `.gitignore` 管理。历史结果引用的本机或设备路径按输入清单恢复。

## 验证口径

- `SYNTHETIC`：合成输入的软件行为与数学验证。
- 真实录制诊断：原始观测、内部统计与指定输入实验结果。
- 独立真值验收：冻结输入、配置、标注和协议后给出阶段结论。
- `WSL2_HUMBLE_X86_64`：集成预验证环境。
- Jetson Orin Nano／Humble：实时性能和部署验收环境。

历史状态、失败原因与指标保留于原报告。新增实验记录日期、版本、命令、依赖、输入哈希和输出位置。

# 2026-11-13 GitHub 归档记录

目标仓库：https://github.com/xiaoxiaoshibw/Lidar-NA70

## 发布范围

保留本地已有 13 次 Git 提交及其源码、配置、锁文件、测试和历史证据，新增分类导航、进度、历史路线与复现说明。同步此前工作区保留的 Jetson 实时点云观察工具、现场报告和计划更新。

| 分类 | 目录 |
|---|---|
| 算法核心 | `packages/na70_core/`、`web/backend/` |
| WebUI | `web/frontend/`、`web/start.ps1`、根目录启动入口 |
| ROS2 | `ROS2/src/` 及其配置、驱动与迁移材料 |
| 离线与设备工具 | `tools/` |
| 历史与证据 | `Plan/`、`docs/`、已有跟踪的实验输出 |

本次保持现有源码组织，增加文档分类入口。历史大文件中已经受 Git 跟踪的证据随历史保存；新增原始 PCAP、缓存、依赖安装目录和构建输出依忽略规则管理。

## 历史恢复

算法路线与 Git 分支分别记录：本地原分支为 `master`，GitHub 发布目标为 `main`；13 次原提交保持父子关系与 SHA。历史方案按 [历史索引](ALGORITHM_HISTORY.md) 定位。MapMOS 的完整环境与权重列为待恢复材料，源码、配置与数据恢复项已写入 Markdown。

## 发布检查

环境：Windows 本机，现有 `web/.venv` 与 Node.js 环境。

- `npm run build`：通过，TypeScript 检查与 Vite 生产构建完成；构建提示存在超过 500 kB 的分块。
- `git diff --check`：通过。
- Jetson 实时观察 `server.py`：Python AST 语法解析通过。
- 当前跟踪文件的常见凭据文件名与令牌签名扫描：命中数 0。
- Git 历史对象检查：超过 100 MiB 的单个文件数为 0。
- 新增导航中的本地链接逐项检查；发布记录本身随本次提交补齐。
- `web/.venv/Scripts/python.exe -m pytest tests -q`（工作目录 `web/`）：611 passed、2 failed、3 skipped，239.13 s。两项失败均为 `test_real_captures_random_access`，所需 `radar_capture_data/1.pcap`、`2.pcap` 待恢复，异常为 `FileNotFoundError`。测试结果按原样保存。
- `git fsck --full --no-reflogs --unreachable --no-progress`：发现游离 blob 465 个、tree 562 个，额外游离 commit 数为 0。原始 13 次提交完整保留，游离文件对象的路线归属与时间上下文需进一步考证。
- 首次推送已完成：GitHub `main` 接收归档提交 `4298980` 及全部父提交。

检查用于确认本次归档的软件与文档状态。各算法现场效果与性能沿用原实验报告的输入、环境及阶段口径。

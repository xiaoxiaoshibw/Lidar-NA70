# Jetson 正常退出收尾工程测试

独立 V4 服务运行于 Jetson 本地 8793，使用 SSH 临时转发发起 30 秒双流工程录制；录制约 9 秒时向该服务进程发送 SIGTERM。服务正常退出并保存会话 `20261030-162935`；原始 IMU 1824 帧、PCAP 13148900 字节。质量报告按提前停止标记 `degraded`，未显示正常完成。检查后该服务进程已退出，未看到遗留 tcpdump 进程；文件清单审计 `ok`，并明确记录采集状态 `degraded`。

本测试仅验证可处理的 SIGTERM，不代表 SIGKILL、断电、磁盘满、实时断流或任何同步/标定通过。V4 是单独测试实例，不替换原 8792 服务。Windows 采集测试 48 passed、1 skipped（POSIX 信号测试）；WSL2 Ubuntu 22.04 的 SYNTHETIC SIGTERM 测试 1 passed。

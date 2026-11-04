# A2 Jetson 浏览器工程录制

- Windows Vite `http://127.0.0.1:5173/capture.html`，SSH 将本地 8791 转发至 Jetson 测试版服务 8792；Playwright Chromium 实际操作页面并刷新。
- 命令：`NA70_E2E_BASE_URL=http://127.0.0.1:5173 NA70_CAPTURE_HARDWARE=1 npx playwright test --project capture-hardware`（Windows 使用 PowerShell 环境变量）。1 passed；页面启动、录制中刷新恢复、结束显示结果均已实测。
- 会话 `20261030-155552`：60.241 s，IMU 12018 帧、199.9 Hz，校验错、断号、溢出为 0；PCAP 88,072,684 字节。质量报告为 `ok`，主机窗口重叠 60.103 s。
- tcpdump 59995 captured、60089 received by filter、0 kernel dropped；差 94 包尚未解释。主机接收时间不构成硬件同步。此为工程诊断，未要求设备处于 B1 指定场景，不用于标定或代替用户采集。
- 独立审计 `audit.json`：**失败**，原因是旧版 manifest 在写入最后一条事件前计算 events.jsonl 哈希，结束后日志大小及哈希不匹配；其余列出的文件哈希、IMU 块总字节与 12018 行帧/CSV 对账通过。此审计不检查 PCAP 解码和有效时间重叠。保留原始会话，不修写历史 manifest。
- Windows 回传 PCAP 的 SHA-256 `f847d6aa23e706788cdeee6bb610010c19073b36c4fadcbfb8ec1eb173b0b62e` 与 manifest 一致。`web/backend/decoder.py` 扫描：59995 有效包、0 无效包、0 忽略包、600 帧、59.996 s、无警告。解码正常不解释 captured/received 差异。
- 600 帧中最后一帧含 95 包（不完整），其余 599 帧各 100 包；结束边界附近不可直接当完整帧。复核 `audit-v2.json` 显示 1180 个主机接收块中最大间隔 0.053003 s；该值是 USB 主机接收块间隔，并非设备采样间隔。
- 实际数据端点检查 `host-overlap.json`：PCAP 中 59995 个包与 1180 个 IMU 接收块的主机时间端点重叠 59.976969 s，最大相邻包接收间隔 0.001411 s、最大相邻 IMU 接收块间隔 0.053003 s。PCAP 和接收块文件的回传哈希均与清单匹配；这不是设备采样时间、硬件同步或全程有效性证明。
- [libpcap 官方统计说明](https://github.com/the-tcpdump-group/libpcap/blob/master/pcap_stats.3pcap)指出统计可能包括尚未交给应用读取的包；其 [Linux 说明](https://github.com/the-tcpdump-group/libpcap/blob/master/doc/README.linux)区分过滤器接收计数和缓冲丢弃计数。停止时尚未读取的数据是本次 captured/received 差异的一种可能解释，尚未通过该设备的专门实验确认；内核 dropped=0 不证明设备到应用端零丢包。
- 发现本次浏览器填写的工程备注未被旧版服务写入 operator.json；测试脚本及本报告提供用途证据，但原会话 task 标记 B1 不能当作用户 B1。新版代码已修复备注落盘，原文件不改写。
- 使用该 PCAP 和原始 `imu_host_receive.csv` 运行 `tools/prepare_lio_session.py --end-frame 500 --duration 0.1 --evidence-kind REAL_PCAP_DIAGNOSTIC`，结果正确拒绝：缺少设备时间 CSV 字段。完整输出在 [输入预检](../20261030-lio-host-time-rejection/REPORT.md)；不表示 IMU 数据无用，只表示不能冒充已同步的设备时间。
- 使用 `tools/check_na70_imu_static.py` 分 10 秒窗口做静置一致性诊断，源 JSONL 的 SHA-256 与 manifest 匹配。6 个窗口均为 `REJECTED`：加速度和角速度波动超过从历史静置监视器沿用的开发阈值；例如第一个窗口角速度轴标准差约 0.285、0.754、0.796（单位 deg/s 仍为推断）。结果见 `imu-static-diagnostic-v2.json`，并不区分振动、移动或其他噪声来源。未要求操作者保持静止的工程会话不能作为静置标定基线。首次通过 PowerShell 管道写出的 `imu-static-diagnostic.json` 中文乱码，保留并以 UTF-8 直写的 v2 为准。
- 长录诊断会话 `20261030-155659` 已启动，目标 600 s；启动后观察到两路写盘，完成状态须后续查询 manifest 和 quality 后确认。

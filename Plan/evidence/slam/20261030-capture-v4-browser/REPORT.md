# V4 真实浏览器工程试录

- 停止空闲 V3 服务后在 Jetson 8792 启动 V4，Windows localhost SSH 转发；Vite 和真实 Chromium 执行 `capture-hardware`：60 秒录制、刷新恢复、显示结果，1 passed。另启动 Windows 生产构建 WebUI 于 127.0.0.1:8765，`/capture/` 返回 200。
- 工程会话 `20261030-163303`：60.251 s，12020 IMU 帧、PCAP 88072684 字节，质量报告 `ok`。文件哈希与帧/块独立审计 `ok`；使用采集解析器重放原始 IMU 字节的结果 `ok`，共丢弃 36 个未解析字节，0 校验错误。此审计不独立验证协议、硬件同步或真实有效时间重叠。
- 雷达 59995 captured、60103 received by filter、0 kernel dropped；差 108 包未定因。PCAP 尚未回传解码，此记录不用于标定。
- IMU JSONL 回传 SHA-256 与 manifest 一致。静置一致性诊断 `imu-static-diagnostic.json` 六个窗口均被开发阈值拒绝，加速度及角速度波动偏高；场景中设备是否受振动或移动未知，不能据此推断原因。本次工程测试未要求操作者保持静止，不作 B1。
- 后续添加 B1 俯视示意和暂禁后续批次选项，页面已更新到 Jetson，SHA-256 与工作区一致；SYNTHETIC Playwright 1 passed，图仅为摆放示意，不是外参。

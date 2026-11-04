# 修复版 Jetson 双流短录验证

- 版本：独立目录 `/home/jetson/na70_capture_stage_v2`，端口 8792；不覆盖先前部署与历史会话。经 SSH 本地转发调用采集 API，任务 `A2-FIXED-VERIFY`、20 秒，备注 `verify note and manifest`。
- 会话 `20261030-160803` 持续 20.272 s，采集质量报告 `ok`。随后使用 `audit.py` 对所有清单文件复算大小和 SHA-256、检查块覆盖及 IMU 帧记录，审计 `ok`：4023 帧、395 个接收块、269541 字节原始 IMU 数据，备注确实写入 operator.json。
- tcpdump 19942 captured、20104 received、0 kernel dropped；差异未作无依据归因。审计不解码 PCAP、不验证有效数据时间重叠、时钟同步或标定；仅 20 秒，不能替代修复版长录与现场 B1。
- `imu-raw-audit.json` 按原始接收块重新解析，4023 帧与帧 JSONL 逐项一致；使用同一采集解析器，不是独立协议验证。

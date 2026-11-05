# IMU 串口批次与后续采集修复

## 实测事实

- 会话 `20261104-170341` 的 `raw/imu_chunks.jsonl` 有 164 个块，每块恰为 8192 字节；相邻块墙钟间隔中位数为 0.61008 s。该文件与原始字节均未改写。
- 当前源码默认请求 `device.read(8192)`、`read_timeout=0.05 s`。本机 pyserial 3.5 Windows 实现对非零超时将整个请求长度传给 `ReadFile`，并设置 Windows `ReadTotalTimeoutConstant`；其是否按该超时结束取决于运行时和串口驱动。原会话没有记录生效串口超时，不能核实当时实际设置。
- 当前系统串口列表中没有采集时的 COM12，无法对实际 USB 设备及驱动做现场复现或复测。没有证据足以证明该设备或驱动是根因。
- 本机 Python 3.12 `time.get_clock_info('monotonic')` 报告 Windows `GetTickCount64()`、分辨率 15.625 ms；`perf_counter` 报告 `QueryPerformanceCounter()`、分辨率 100 ns。分辨率不等于实际同步或采样精度。旧会话仍使用原来的单调时钟；墙钟时间也受系统时钟和调度影响。

## 对照实验（SYNTHETIC）

命令：`web/.venv/Scripts/python.exe Plan/evidence/slam/20261104-serial-capture-fix/loopback_experiment.py`。模拟每 5 ms 写入 67 字节，使用 pyserial `loop://`，结果见 `loopback_result.json`。旧 `read(8192)` 在超时 0.05 s 时返回约 670–871 字节；人为令超时为 `None` 时每次恰收 8192 字节，读取时长中位数约 0.6095 s；新策略取得更小的块。计时受 OS 调度影响。该实验表明等待填满足以产生相似现象，**不证明现场驱动超时失效或当时 timeout 为 None**；`loop://` 不使用 Windows USB 驱动。

## 修复与验证

后续读取改为只请求串口当前排队字节数，空队列只等待 1 字节；单次读取仍限制在 8192 字节。停止时调用驱动支持的 `cancel_read`，读取线程对取消前后已返回的字节照常写入，并保留解析器不完整尾部统计；驱动内部尚未返回的字节不能保证落盘。异常、重连、写入失败和超时仍通过原有质量机制暴露。

IMU 单调时钟改为 `perf_counter`，事件记录时钟函数名称；时间仍是**主机读取完成时间**，不是硬件接收时间或 IMU 采样时间。同一次读取的所有帧仍共享时间，不进行批内插值。不同单调时钟的数值不可直接相减；Linux 与 Windows 的单调时钟来源及绝对起点也未假设相同。墙钟用于现有 PCAP 关联，非硬同步。

从 `web/` 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_na70_capture.py tests/test_local_capture_dual_stream.py -q`，结果见 `pytest.log`。覆盖 SYNTHETIC 忽略超时的大请求模型、短读、读取完成时间共享、阻塞取消及尾字节、断连、写盘失败和双流联调。

下一次真实连接需在安全短录后检查批大小、间隔、原始帧序号、校验和停止尾部；减少主机批次本身不能证明设备采样时间、标定或动态 LIO 就绪。

# A2 更新版短时双流诊断

- 环境：Jetson `yahboom`，Wi-Fi SSH，采集接口 `enP8p1s0`；测试版独立目录 `/home/jetson/na70_capture_stage_20261030`、端口 8792。原 8791 服务与历史会话未覆盖。测试完成后关闭测试版服务。
- 命令：`python3 -m na70_core.capture.service --root /home/jetson/na70-sessions --port 8792 --run-as jetson --min-free-gb 1`；通过 SSH 本地转发向 `/api/session/start` POST `{"task":"A2-ENGINEERING","duration_s":15}`。
- 会话 `20261030-155342`：质量报告 `ok`，15.221 s，IMU 3014 帧、199.8 Hz，校验错/断号/队列溢出均为 0；PCAP 22,080,212 字节，15,041 captured、15,067 received by filter、0 kernel dropped。重叠 15.084 s 是采集进程窗口重叠，不是硬件同步。
- captured 与 received 差 26，原因未查明；不能声称无丢包。时间等级 `host_receive_only`，不能据此完成动态标定。此工程诊断不是用户 B1，未验证长录、浏览器实录或标定。
- 本地测试：`web/.venv/Scripts/python.exe -m pytest tests/test_na70_capture_service.py tests/test_na70_capture.py -q`，35 passed。新增线程异常失败传播检查；首次运行因测试替身缺少 `put` 接口失败，补齐后重跑通过。
- 测试版 SHA-256：service `82ffa79074a34141ae37540d50d4986539ab2b71d06b159a568c9cfb60649319`，imu `086ca2b72aa56889351120b431d2ded80a55f451d227a93929f2d34bab799263`，runner `755d3ec7948695b8f8f3a6671bd1ff1878d90496cbfbe9b3fbc823d4c4472c0d`，lidar `9d0d8b50326ff5c60b34eba622f1eed9d01f66fbf2020f45acd1e2080d4b06bb`。

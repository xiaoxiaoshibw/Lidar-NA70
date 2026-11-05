# 2026-11-04 会话目录与既有会话复核

## 实测事实

只读枚举 `radar_capture_data/na70_sessions/`，没有名称或目录项显示晚于 `20261104-170341` 的新现场会话；完整目录名和当前枚举到的串口见 [session-inventory.json](session-inventory.json)。当前枚举到 COM3、COM4、COM5、COM6，不含旧会话使用的 COM12；不据此判断设备是否连接或串口是否重编号。修复后的串口采集尚无真实复测数据。

对 `20261104-170341` 重新执行当前仓库的四个只读工具，均退出码 0。完整性审计 `ok`，采集质量仍为 `degraded`；原始 IMU 重放匹配 20,052 帧，校验和和载荷错误均为 0，末尾待解析 4 字节。该重放复用采集解析器，不是独立协议认证。164 个接收块均为 8192 字节，Unix 主机接收块时间间隔中位数约 0.610084 秒、最大 0.615829 秒；批次包含 122–123 帧。0x51/0x52 设备时间字段均未出现，序号未见断裂。连续序号不能证明没有丢帧。雷达有效包 83,351、无效包 0，雷达与 IMU 主机接收端点交叠 99.639859 秒，不证明区间连续或同步。

完整输入文件大小和 SHA-256、工具及解析器哈希、解释器与四条完整实际命令和返回码见 [provenance.json](provenance.json)；逐项结果见 [integrity.json](integrity.json)、[imu-replay.json](imu-replay.json)、[overlap.json](overlap.json)、[timebase.json](timebase.json)。每条命令 stdout、stderr 分别保留。会话原始文件未改动。

## 诊断假设与未验证条件

观察到的大块接收和批次间隔不能单独证明成因，更不能作为采样年龄、时间同步误差的上界。IMU 保持 `C_HOST_RECEIVE_ONLY`，尚无设备采样时刻、两设备时钟映射或修复后的实机批次与停止尾部证据。扫描数据和序号检查不证明设备发送端或 UDP 接收路径零丢包。可信动态 LIO、外参、独立真值、ATE、RPE 和绝对地图精度仍未验证。

## 环境和复现

Windows 11，Python 3.12.10，pyserial 3.5，Git 2.47.1.windows.2，仓库 HEAD `751faab3efcc7fadba9f264cf68cd364d616416f`；工作树含未提交修改，不能只以 HEAD 表示实际代码。执行前已核对四个审计工具 `--help`。使用下列命令复核；脚本输出会写入脚本所在目录，若重做请先将两个脚本复制到**新的**同层证据目录：

```powershell
.\web\.venv\Scripts\python.exe Plan\evidence\slam\20261104-session-independent-recheck\recheck.py
.\web\.venv\Scripts\python.exe Plan\evidence\slam\20261104-session-independent-recheck\session_inventory.py
```

## 下一步

无需新数据即可继续只读诊断和合成测试，但不能凭现有会话宣布时间或动态 LIO 门禁通过。最小现场操作：重新连接并刚性固定雷达和 IMU，将整机稳固静置、雷达正常工作；打开本机采集页，确认两路计数增长，录制 60 秒后停止，等待保存。成功信号是正常停止且新会话目录中两路数据存在；随后再检查批次、序号、校验和停止尾部。这一步本身不证明同步。

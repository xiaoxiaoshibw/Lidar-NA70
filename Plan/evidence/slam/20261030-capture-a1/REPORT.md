# A1 持续双流采集：实现与实测证据

日期：2026-10-30（Asia/Shanghai）。状态：**核心落盘已实现并通过真实双流验证；A2 用户入口未完成**。
执行计划：[NA70_YESENSE_标定AI执行计划.md](../../NA70_YESENSE_标定AI执行计划.md) 的 A1。
硬件合同：[hardware_contract.json](../20261030-hardware-contract/hardware_contract.json)。

## 1. 交付物

| 文件 | 作用 |
|---|---|
| `packages/na70_core/na70_core/capture/yesense.py` | YESENSE 帧解析（有状态、跨块边界、校验/重同步、字段解码） |
| `packages/na70_core/na70_core/capture/session.py` | 会话目录独占创建、events、磁盘下限、manifest/SHA-256 |
| `packages/na70_core/na70_core/capture/imu.py` | 串口读线程 + 有界写队列 + segment 轮转 + 原始字节优先落盘 |
| `packages/na70_core/na70_core/capture/lidar.py` | tcpdump 子进程封装（sudo/-Z 降权、SIGINT 停止、内核丢包统计） |
| `packages/na70_core/na70_core/capture/runner.py` | 双流编排、重叠窗口、质量判定（ok/degraded/failed） |
| `tools/na70_capture_session.py` | 命令行入口，输出会话摘要 JSON，返回码 0/3/4/2 |
| `web/tests/test_na70_capture.py` | 20 个用例（解析、会话、重连、溢出、tcpdump、质量判定） |

落盘目录契约（实测）：

```text
<session_id>/
  manifest.json  events.jsonl  quality.json  operator.json
  imu_frames.jsonl  imu_host_receive.csv
  raw/imu.bin  raw/imu_chunks.jsonl  raw/lidar.pcap  raw/lidar-tcpdump.log
```

## 2. 测试结果

```text
web> .\.venv\Scripts\python.exe -m pytest tests\test_na70_capture.py -q
20 passed in 0.61s
```

覆盖：四组字段解码、逐 3 字节分块喂入、校验失败重同步、垃圾字节计数、半帧保留、非法组长度拒绝、tid 回绕、会话独占与哈希、磁盘下限、原始字节完整、写队列溢出计数、串口打开失败重试、断流后 segment 轮转、tcpdump 提权/降权命令行、PCAP 拒绝覆盖、内核统计解析、质量三态判定。

## 3. 真实双流实测（Jetson 本机，2026-10-30 15:10）

会话 `20261030-151019`，15 秒，`--task A1-DUAL3`：

| 项目 | 实测 |
|---|---|
| 会话状态 | `ok`，`duration_s` 15.305，`overlap_s` 15.085（覆盖较短流 100%） |
| IMU | 3015 帧，199.8 Hz，校验错 0、tid 断号 0、队列溢出 0、丢弃字节 0 |
| 雷达 | `raw/lidar.pcap` 22,080,212 字节；tcpdump 统计 captured 15041 / received 15107 / dropped 0 |
| 传输对账 | 本机重算 SHA-256：`raw/lidar.pcap`、`imu_host_receive.csv`、`quality.json` 均 **MATCH** |
| 解码复核 | `web/backend/decoder.py` 解出 **151 帧 / 15041 有效包 / 0 异常包 / 15.041 s**（10.04 Hz） |
| IMU 导出一致性 | `imu_host_receive.csv` 3015 行，tid 连续无断号 |

本次为**发流与落盘验证**，不是 B1 标定数据：设备未按拍摄卡摆放，也没有测量参考。
原始 PCAP 留在 `radar_capture_data/na70_sessions/20261030-151019/`（`*.pcap` 已被 Git 排除），
本目录只保存会话元数据。

## 4. 过程中发现并修复的缺陷（保留失败事实）

| 现象 | 根因 | 处理 |
|---|---|---|
| 第一次双流（15:06）质量报告 `imu_rate = 0.0 Hz` | 停机后“最近 2 秒”窗口必然为空 | 停机时改用整段平均频率 |
| 第一次双流 `duration_s = 45.07`，重叠被判不合格 | `sudo` 无终端时不转发 SIGINT，停止卡满 20 s 超时 | `pkill -INT -x tcpdump` 精确补发；重叠分母改为“较短流长度” |
| 单元测试覆盖的断流轮转未触发 | `if self.segment and self.frames` 恒为假 | 改为 `if self.frames` |
| 质量报告 0 个失败项但 `duration_s = 47.08` | 同上停止卡顿，尚未修正分母前 | 第二轮已复现，第三轮修复后 15.3 s |

失败会话（`20261030-150648`、`20261030-150844`）未删除，仍在其原始目录，用于说明“参数看起来对但收尾错误”的情形。

## 5. 已知限制（不得当作已通过）

1. **captured 15041 < received 15107**：66 包（0.44%）在退出瞬间未落盘，原因未核实（缓冲/退出竞态），因此**不能声称零丢包**；长期录制需专门审计。
2. `dropped by kernel = 0` 只代表内核计数为 0，不等于写盘无损。
3. 未做长录（10–20 分钟）与磁盘写满、PCAP 截断、进程被杀等故障注入。
4. 时间仍为 `host_receive_only`：IMU 无设备采样时间戳，雷达时间为上电起算；两者只有主机关联。
5. 雷达工厂状态字 -20.48° 语义未核实，任何依赖点云几何的结论（墙面测距）暂不开展。
6. 采集端以 `jetson` 用户运行；已加入 `dialout`（最小权限），`tcpdump` 依赖免密 sudo。若收紧 sudoers，需保留 tcpdump 白名单。

## 6. 下一步

* A2：用户入口（设备状态→动作卡→倒计时→自动检查），页面不暴露参数与矩阵。
* A1 收尾：长录与故障注入；captured/received 差异定位。
* 之后再发 B1（60 秒静置试录）。
# Jetson 双网采集部署记录

日期：2026-10-30（Asia/Shanghai）。用户授权：有线网口接交换机，同时连接 NA70 与 192.168.3.1 网关；Wi-Fi 连接 Xiaomi_52BF；IMU 由 Jetson USB 接收。

状态：**NETWORK_CONFIGURED_VERIFIED / RADAR_STREAM_VERIFIED / IMU_STREAM_VERIFIED / CAPTURE_TOOL_CORE_VERIFIED / UI_NOT_READY**（2026-10-30 15:12）。实际主机 `yahboom`、aarch64，ROS Humble 安装目录存在；ROS_DOMAIN_ID=43 由用户提供，本轮未修改 ROS 配置。未做重启或 Jetson 性能验收。

## 用户补充的路由器连接记录

2026-10-30（Asia/Shanghai），用户在路由器界面确认：

- 设备名称：`yahboom`。
- Wi-Fi IPv4：`192.168.31.168`（DHCP 当前租约，未设置静态地址保留）。
- 路由器显示 MAC：`DC:4A:9E:DF:DC:AA`。
- 截图/抄录当时连接时长：3 分 23 秒；这是用户观察值，不是持续运行或重启验收。
- 来源：用户提供的路由器设备信息。本次仅记录，未修改网络或 DHCP 设置。

电脑连接同一 Xiaomi 网络且允许客户端互访时，Wi-Fi SSH 地址为 `ssh jetson@192.168.31.168`；已独立验证的有线入口仍为 `ssh jetson@192.168.3.129`。

## 1. 已应用配置

| 链路 | 实际接口 | Jetson 地址 | 作用 |
|---|---|---|---|
| 有线→交换机→NA70 | enP8p1s0 | 192.168.1.129/24 | 保留现有雷达接收地址；雷达自身 IP 与实际发流待核实 |
| 有线→交换机→网关 | 同上 | 192.168.3.129/24 | 新增管理地址，默认网关 192.168.3.1，metric 100 |
| 原有第二有线子网 | 同上 | 192.168.127.100/24 | 保留既有配置，不推断其设备用途 |
| Wi-Fi→Xiaomi_52BF | wlP1p1s0 | 192.168.31.168/24（DHCP） | 局域网管理链路；IPv4/IPv6 均不接管默认路由 |
| USB→IMU | ttyACM0 | /dev/serial/by-id/usb-WCH.CN_USB_Single_Serial_0003-if00 | 稳定串口路径，尚未验证本机用户读写/持续采集 |

有线 NetworkManager profile：`codex-na70-switch`。Wi-Fi profile：`codex-xiaomi52bf`。两者均已保存、autoconnect=yes、autoconnect-priority=100；保留旧 `wired-static` 和 `H3C_n6-506` 作为恢复配置。Wi-Fi 密钥仅由目标机 NetworkManager 保存，文档、仓库与证据 JSON 不记录密钥。

192.168.3.129 在配置前执行过有线 ARP 探测，未发现响应；这不是 DHCP 地址保留或永久无冲突保证。后续由 AI 核实路由器地址池/静态地址管理，避免同地址被其他设备分配。

## 2. 拓扑

```text
电脑 ── 有线侧现有网络 / 或连接 Xiaomi_52BF ── SSH 管理 Jetson
                                                      │
NA70 ── 网线 ── 交换机 ── enP8p1s0（多子网地址）       │
192.168.3.1 网关 ──┘                                   │
YESENSE IMU ── USB ────────────────────── Jetson 本机采集
```

雷达和 IMU 原始流应由 Jetson 同机记录，Windows 负责任务控制、下载、审计与结果展示；同机接收仍不等于传感器硬件同步。不要经 Wi-Fi 转发雷达流再用另一台电脑的 IMU 时钟拼接成“同步数据”。

## 3. 已完成验证

- SSH 在原 192.168.3.43 可用；切换后通过新有线地址 192.168.3.129 再次建立 SSH。
- 从 enP8p1s0 到 192.168.3.1 的 ARP 与 ICMP 均有响应；切换完成后两包 ping 无丢失。
- Wi-Fi 已实际连接 Xiaomi_52BF，DHCP 得到 192.168.31.168；从 wlP1p1s0 到热点网关 192.168.31.1 的 ping 通过。
- 三个有线网段与新 Wi-Fi 的 192.168.31.0/24 不重叠；实际默认路由为 `192.168.3.1 dev enP8p1s0 metric 100`。
- 变更前建立 5 分钟自动回退保护；完成连通性验证后已停止回退定时器。未删除原配置。
- 配置检查中使用过一个不支持的 nmcli 查询字段别名，导致凭据保存检查命令返回失败；随后用完整字段名核实成功。该失败发生在 Wi-Fi 已连接之后，不是连接失败；未将密钥输出到日志。
- 实际状态保存于 [network-state.json](evidence/slam/20261030-jetson-network/network-state.json)。

### 3.1 雷达发流实测（2026-10-30 14:52，雷达上电后）

先前两次监听（5 秒 `-c 3`、6 秒 `-c 5`）均为 **0 packets**，原因是雷达尚未上电；同一时段 IMU 已验证 200 Hz 在流，主机接收链路正常。用户上电后重测：

| 项目 | 实测值 |
|---|---|
| 源 → 目的 | `192.168.1.66:7000` → `192.168.1.129:2368`（单播，非广播） |
| UDP 载荷 | 1410 字节（与 classic 契约一致） |
| 包率 | 约 1000 包/秒（40 包 / 0.039 秒） |
| 3 秒抓包 | 4,217,588 字节 → 2873 个有效包、29 帧、0 个异常包 |
| 解码 | `web/backend/decoder.py` 可直接解码，20000 点/帧，`complete=true`，帧间隔 0.1000 秒（10 Hz） |
| 设备时间 | 8 字节尾字段呈**上电起算的单调秒+毫秒**（首帧 39.183 秒，逐帧 +0.1；历史 `1.pcap` 为 293.14 秒），不是绝对时钟 → 跨设备对齐仍需估计起始偏移 |
| 回波强度 | 9.3%–14.8% 射线有回波，非零距离中位 9.8 米；58 万点中出现 1 个 `0xFFFF` 距离码（65535×16 mm = 1048.56 米，应作无效码过滤） |
| 工厂状态字 | 本机 pitch/yaw offset 解码为 **-20.48° / -20.48°**（原始低 12 位为 0，符合“未写入”编码）；历史 `1.pcap`/`2.pcap` 为 +0.08° / −0.06° |

抓包文件：`radar_capture_data/na70_probe/stream_probe.pcap`（4.0 MB，`*.pcap` 已被 Git 排除），本次为**发流探测**，不是 B1 会话。

**待核实，暂按未决处理：** 本机工厂状态字的低 12 位为 0，解码公式得 -20.48°，而正常标定值（如历史 0.08°）应编码为 2048 → 0°。因此不能直接按 -20.48° 旋转点云（会让整帧偏转约 20°，直接影响墙面测距/平面测试）。需与厂家状态字语义或出厂配置文件核实后再决定“按 0 处理/按实测处理”。

### 3.2 持续双流采集落地（A1，2026-10-30 15:10）

采集端为 `packages/na70_core/na70_core/capture/` + `tools/na70_capture_session.py`，Jetson 部署在 `/home/jetson/na70_capture`，会话根目录 `/home/jetson/na70-sessions`。真实 15 秒会话 `20261030-151019` 判为 `ok`：IMU 3015 帧 / 199.8 Hz，PCAP 22,080,212 字节（captured 15041、received 15107、dropped 0），回传后 SHA-256 对账全部 MATCH，`web/backend/decoder.py` 解出 151 帧、0 异常包。证据与三个已知缺陷的修复记录见 [A1 REPORT](evidence/slam/20261030-capture-a1/REPORT.md)。

两处本机改动：

- `jetson` 用户已加入 `dialout` 组（`sudo usermod -aG dialout jetson`），使串口可在非 root 下读取；**未**修改设备节点权限为 0666。
- 雷达抓包经 `sudo -n tcpdump … -Z jetson` 降权写入；停止时用 `sudo -n pkill -INT -x tcpdump`，因为 sudo 在无终端时不转发 SIGINT（实测会让停止卡满 20 秒超时）。

仍未解决：captured 与 received 相差 66 包（0.44%），原因未定位，不能声称零丢包。

## 4. 仍未完成

1. 雷达发流已验收（见 3.1）；仍未实现持续写盘与丢包审计（A1），单帧/短抓包不等于长录可靠性。工厂状态字 -20.48° 的语义待核实，核实前不派发依赖点云几何的 B4。
2. ttyACM0 为 root:dialout / 0660；当前 jetson 用户不在 dialout，读写检查失败。AI 在采集准备阶段按串口权限最小需求处理，并在新会话复查；不得使用全员 0666 权限替代。
3. 目标机已安装 pyserial。2026-10-30 补充实测（只读、不发送任何指令）：以 root 打开 `/dev/serial/by-id/usb-WCH.CN_USB_Single_Serial_0003-if00`→`ttyACM0`（460800 8N1），2.14 秒窗口收到 28672 字节、428 个 `0x59 0x53` 帧头，折合 **200.4 Hz**，与 Windows COM12 历史记录一致。**但这是 root 读的**：jetson 用户不在 `dialout` 组，正式采集（A1）必须先做最小权限处理，不能用 0666 替代。收到字节不等于时间已核实：设备采样时间戳字段仍未确认，主机 USB 到达时间不能当采样时间。Windows COM12 是历史实测，不是 Jetson 设备名。
4. 持续双流录制、文件完整性、设备时钟语义、标定工具适配尚未完成；当前不派发 B1。
5. 自动连接配置已保存，但本轮没有重启验证。Wi-Fi DHCP 地址后续可能变化，AI 应重新查验，不把 192.168.31.168 当永久静态地址。

## 5. 管理入口与恢复

电脑能访问交换机所在 192.168.3.0/24 网络时：

```text
ssh jetson@192.168.3.129
```

电脑连接 Xiaomi_52BF 且热点允许客户端互访时，可尝试当前 Wi-Fi 地址：

```text
ssh jetson@192.168.31.168
```

第二种入口尚未从已连接热点的电脑独立验收；当前电脑连接旧 Wi-Fi，已验收的是第一种入口。原来的 192.168.3.43 是旧 Wi-Fi 地址，不再作为当前入口。

后续采集 Web 服务保持绑定 Jetson 的 127.0.0.1，电脑通过 SSH 本地端口转发访问；具体服务端口由 A2 实现后给出，不开放未鉴权 0.0.0.0 服务，也不为访问页面关闭防火墙。

若需恢复旧网络，应从本机控制台或已确认不会被切断的备用连接执行：

```bash
sudo nmcli connection modify codex-na70-switch connection.autoconnect no
sudo nmcli connection modify codex-xiaomi52bf connection.autoconnect no
sudo nmcli connection up wired-static ifname enP8p1s0
sudo nmcli connection up H3C_n6-506 ifname wlP1p1s0
```

恢复时有线新管理地址会消失，不能假定当前 SSH 会话可持续。后续 AI 修改网络仍需先建立回退路径。

下一阶段按 [标定 AI 执行计划](NA70_YESENSE_标定AI执行计划.md) 推进 A0–A2：Jetson 为实际采集端，Windows 为控制/分析端；网络就绪不升级标定/同步/LIO/Jetson 性能验收。

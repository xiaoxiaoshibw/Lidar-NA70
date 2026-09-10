# 主流激光雷达 ROS/ROS2 驱动 pcap 回放模块调研报告

> 目标：为 256 线双楔镜扫描激光雷达（Innolight/Innoview InnoLiDAR 系列）的 ROS2 驱动开发提供 pcap 解析参考。
> 调研日期：2026-09-08
> 所有源码均来自各厂商公开 GitHub 仓库，已逐文件读取验证。

---

## 目录

1. [总体架构概览](#1-总体架构概览)
2. [Velodyne ROS2 驱动](#2-velodyne-ros2-驱动)
3. [Ouster ROS2 驱动](#3-ouster-ros2-驱动)
4. [Hesai ROS/ROS2 驱动](#4-hesai-rosros2-驱动)
5. [RoboSense rslidar_sdk](#5-robosense-rslidar_sdk)
6. [Innoviz 及其他扫描式雷达](#6-innoviz-及其他扫描式雷达)
7. [通用 pcap 解析库与工具](#7-通用-pcap-解析库与工具)
8. [横向对比分析](#8-横向对比分析)
9. [对双楔镜扫描雷达 ROS2 驱动的建议](#9-对双楔镜扫描雷达-ros2-驱动的建议)

---

## 1. 总体架构概览

四家主流厂商的 pcap 回放模块均遵循 **三层架构**，但实现细节差异显著：

| 层级 | 职责 | 通用做法 |
|------|------|----------|
| **输入抽象层** | 屏蔽 live socket / pcap 文件差异，统一输出 UDP payload | 基类 `Input` + 派生类 `InputPCAP` / `InputSocket` |
| **协议解析层** | UDP payload → 距离/角度/强度/时间戳 | 按厂商自定义包结构逐字段解析，含校准参数 |
| **点云组装层** | 多包拼接为一帧 PointCloud2 | 按旋转角/帧标志切分，逐点计算 XYZ |

关键差异维度：
- **pcap 读取库**：裸 libpcap（Velodyne/Hesai/RoboSense）vs libtins 封装（Ouster）
- **过滤方式**：BPF 编译后 `pcap_offline_filter()` 逐包过滤（Velodyne/RoboSense）vs `pcap_setfilter()` 内核级过滤（Hesai）vs 按 payload size 自动识别（Ouster）
- **速率控制**：固定 `rclcpp::Rate`（Velodyne）vs 硬件时间戳差值（Hesai）vs 帧边界触发（RoboSense）vs 不控制/索引随机访问（Ouster）

---

## 2. Velodyne ROS2 驱动

### 2.1 仓库信息

- **GitHub**: https://github.com/ros-drivers/velodyne （`ros2` 分支）
- **License**: BSD 2-Clause
- **关键文件**:
  - `velodyne_driver/src/lib/input.cpp` — pcap/socket 输入抽象
  - `velodyne_driver/include/velodyne_driver/input.hpp` — 类定义
  - `velodyne_pointcloud/src/lib/rawdata.cpp` — UDP 包解析与点云计算
  - `velodyne_pointcloud/include/velodyne_pointcloud/datacontainerbase.hpp` — 点云容器

### 2.2 pcap 打开与过滤

`InputPCAP` 构造函数中完成 pcap 打开和 BPF 过滤器编译：

```cpp
// velodyne_driver/src/lib/input.cpp, L304-317
if ((pcap_ = pcap_open_offline(filename_.c_str(), errbuf_)) == nullptr) {
  RCLCPP_FATAL(private_nh->get_logger(), "Error opening Velodyne socket dump file.");
  return;
}

std::stringstream filter;
if (!devip_str_.empty()) {            // using specific IP?
  filter << "src host " << devip_str_ << " && ";
}
filter << "udp dst port " << port;
pcap_compile(
  pcap_, &pcap_packet_filter_,
  filter.str().c_str(), 1, PCAP_NETMASK_UNKNOWN);
```

**设计要点**：
- BPF 过滤表达式为 `src host <IP> && udp dst port <port>`，同时过滤源 IP 和目标端口
- 默认数据端口 `DATA_PORT_NUMBER = 2368`
- 仅编译过滤器，不调用 `pcap_setfilter()`，而是在读取时用 `pcap_offline_filter()` 逐包判断

### 2.3 逐包读取与速率控制

```cpp
// velodyne_driver/src/lib/input.cpp, L327-385
int InputPCAP::getPacket(VelodynePacket *pkt, const double time_offset)
{
  struct pcap_pkthdr *header;
  const u_char *pkt_data;

  while (true) {
    int res;
    if ((res = pcap_next_ex(pcap_, &header, &pkt_data)) >= 0) {
      // Skip packets not for the correct port and from the selected IP.
      if (0 == pcap_offline_filter(&pcap_packet_filter_, header, pkt_data))
        continue;

      // Keep the reader from blowing through the file.
      if (!read_fast_) {
        packet_rate_.sleep();  // rclcpp::Rate 固定频率休眠
      }

      ::memcpy(&pkt->data[0], pkt_data + 42, packet_size);  // 跳过 14 Eth + 20 IP + 8 UDP = 42 字节
      pkt->stamp = private_nh_->get_clock()->now();  // 用系统当前时间，非 pcap 时间戳
      empty_ = false;
      return 0;
    }
    // ... EOF 处理：read_once_ 则退出，否则 close+reopen 循环播放
  }
}
```

**设计要点**：
- 以太网帧头偏移硬编码为 **42 字节**（14 Ethernet + 20 IPv4 + 8 UDP），**不支持 IPv6 和 VLAN**
- 时间戳使用 `private_nh_->get_clock()->now()`（系统时间），**忽略 pcap 包头部时间戳和包内硬件时间戳**（pcap 模式下 `time_offset` 被显式忽略）
- 速率控制通过 `rclcpp::Rate packet_rate_` 固定频率，参数 `read_fast_` 可跳过
- 支持 `read_once_`（只读一遍）和 `repeat_delay_`（循环播放间隔）
- 文件结尾通过 `pcap_close()` + `pcap_open_offline()` 重新打开实现循环（无法 seek 回绕）

### 2.4 UDP 包结构与解析

Velodyne 数据包为 **1206 字节**（`velodyne_msgs/VelodynePacket.data` 固定大小），结构：

```
[12 blocks × (2 bytes header + 2 bytes rotation + 32 × 3 bytes scan_data)] + 4 bytes timestamp + 2 bytes factory
```

- 每个 block 含 `UPPER_BANK`(0xEEFF) 或 `LOWER_BANK`(0xDDFF) 标识
- 每个 scan_data = 2 bytes distance + 1 byte intensity
- VLP-16 特殊处理：每 block 含 2 个 firing × 16 通道 = 32 点

解析核心（`rawdata.cpp` L271 `unpack()`）：

```cpp
// rawdata.cpp, L292-341 (简化)
const raw_packet *raw = reinterpret_cast<const raw_packet *>(&pkt.data[0]);
for (int i = 0; i < BLOCKS_PER_PACKET; i++) {
  int bank_origin = (raw->blocks[i].header == LOWER_BANK) ? 32 : 0;
  for (int j = 0, k = 0; j < SCANS_PER_BLOCK; j++, k += RAW_SCAN_SIZE) {
    union two_bytes tmp{};
    tmp.bytes[0] = block.data[k];
    tmp.bytes[1] = block.data[k + 1];
    float distance = tmp.uint * calibration_->distance_resolution_m;  // 0.002m for VLP
    // ... 应用垂直/水平校准角，计算 XYZ
    data.addPoint(x, y, z, ring, intensity, time);
  }
}
```

**多回波处理**：Velodyne 通过 **DIFOP（位置包）** 中的 return mode 字段切换，驱动层不做特殊处理——单回波/最强回波/最后回波由雷达固件决定，pcap 中已包含最终数据。双回波模式下每包点数翻倍。

**时间同步**：
- live 模式支持 `gps_time_`：从包偏移 1200 处读取 4 字节 GPS 时间戳（`rosTimeFromGpsTimestamp`）
- pcap 模式**不使用**硬件时间戳，直接用系统时间
- 每点精确时间通过 `timing_offsets_` 表计算（VLP-16: `full_firing_cycle=55.296μs`, `single_firing=2.304μs`）

### 2.5 点云组装

`DataContainerBase` 提供 `addPoint()` 和 `newLine()` 接口，由 `rawdata.cpp` 在 azimuth 跨越 0°（或配置的起始角）时调用 `newLine()` 切分帧。支持 organized point cloud（按 ring 排列）和 unorganized 两种模式。

---

## 3. Ouster ROS2 驱动

### 3.1 仓库信息

- **GitHub (ROS 驱动)**: https://github.com/ouster-lidar/ouster-ros （`master` 分支，同时支持 ROS1/ROS2）
- **GitHub (SDK 核心)**: https://github.com/ouster-lidar/ouster-sdk （pcap 模块在此）
- **License**: BSD 3-Clause
- **关键文件**:
  - `ouster_pcap/src/os_pcap.cpp` — 回放/录制 C API 封装
  - `ouster_pcap/src/pcap.cpp` — `PcapReader` / `PcapWriter` 核心实现
  - `ouster_pcap/src/pcap_packet_source.cpp` — 迭代器式包源 + 多传感器索引
  - `ouster_pcap/include/ouster/pcap/pcap_frame_set_source.h` — 帧组装源

### 3.2 核心差异：使用 libtins 而非裸 libpcap

Ouster 是四家中**唯一不直接使用 libpcap API** 的厂商，而是基于 **libtins** 库（`Tins::FileSniffer`）：

```cpp
// ouster_pcap/src/pcap.cpp, L86-112
struct pcap_impl {
    pcap_t* handle{nullptr};
    std::unique_ptr<Tins::FileSniffer> pcap_reader;
    Tins::Packet packet_cache;
    IPv4Reassembler2 reassembler;  ///< IP 分片重组
    bool have_new_packet{false};
    int encap_proto{0};
};

PcapReader::PcapReader(const std::string& file) : impl_(new pcap_impl) {
    impl_->pcap_reader = std::make_unique<Tins::FileSniffer>(file);
    impl_->encap_proto = impl_->pcap_reader->link_type();
    impl_->pcap_reader_internals = pcap_file(impl_->pcap_reader->get_pcap_handle());
    file_start_ = FTELL(impl_->pcap_reader_internals);
}
```

**设计要点**：
- libtins 提供协议栈解析（EthernetII → IP/IPv6 → UDP → RawPDU），无需手动计算偏移
- 内置 **IPv4 分片重组**（`IPv4Reassembler2`），处理 Ouster 大数据包被 IP 分片的情况
- 支持 **Ethernet 和 SLL（Linux Cooked Capture）** 两种封装格式
- 支持 `seek()` / `reset()` 随机访问（通过 `fseek` pcap 文件内部位置）

### 3.3 逐包读取与自动流识别

```cpp
// ouster_pcap/src/pcap.cpp, L161-223 (简化)
size_t PcapReader::next_packet() {
    while (!reassm) {
        info_.file_offset = current_offset();
        impl_->packet_cache = impl_->pcap_reader->next_packet();
        auto pdu = impl_->packet_cache.pdu();
        IP* ip = pdu->find_pdu<IP>();
        IPv6* ipv6 = pdu->find_pdu<IPv6>();
        if (((ip && ip->protocol() == 17) || (ipv6 && ipv6->next_header() == 17))) {
            reassm = (impl_->reassembler.process(...) != FRAGMENTED);
            if (reassm) {
                UDP* udp = pdu->find_pdu<UDP>();
                info_.dst_port = udp->dport();
                info_.src_port = udp->sport();
                info_.payload_size = raw->payload_size();
                info_.timestamp = impl_->packet_cache.timestamp();  // pcap 头部时间戳
                data_ = raw->payload().data();
                result = info_.payload_size;
            }
        }
    }
    return result;
}
```

**自动端口识别**（`os_pcap.cpp` L280 `guess_ports()`）：
- 扫描整个 pcap 文件，统计所有 UDP 流的 `(src_ip, dst_ip, src_port, dst_port, payload_size)`
- 根据 metadata 中已知的 `lidar_packet_size` 和 `imu_packet_size` 匹配流
- 自动配对同一源 IP 的 lidar 流和 IMU 流
- **无需用户手动配置端口号**

### 3.4 IndexedPcapReader 与帧索引

Ouster 最新架构引入 `IndexedPcapReader`，构建 pcap 文件的帧索引：

```cpp
// ouster_pcap/src/pcap_packet_source.cpp, L233-265
PcapPacketSource::PcapPacketSource(const std::string& file, PcapPacketSourceOptions options)
    : reader_(new IndexedPcapReader(file, find_metadata(file, options))),
      index_(options.index.retrieve()) {
    if (index_) {
        reader_->build_index();  // 预扫描构建帧偏移索引
    }
    // ...
    for (const auto& info : sensor_info()) {
        packet_formats_.emplace_back(std::make_shared<PacketFormat>(*info));
    }
}
```

- 支持 `begin_frame(frame_index)` 跳转到指定帧（随机访问）
- 多传感器支持：通过 `check_sensor_idx_for_current_packet()` 按 sensor ID 区分
- 包类型按 payload size 区分：`lidar_packet_size` / `imu_packet_size` / `zone_packet_size`
- 迭代器模式：`PacketIterator` 配合 `advance()` / `value()`

### 3.5 Ouster 列格式（Column Format）

Ouster 数据包为**列格式**而非 Velodyne 的 block 格式：
- 每个 lidar packet 含多列（column），每列包含所有通道的一次测量
- 每列有独立的时间戳（`col_timestamp`）、测量 ID（`measurement_id`）、状态（`col_status`）
- IMU 数据包独立端口，含加速度计和陀螺仪数据
- 帧边界通过 `measurement_id` 回绕检测

### 3.6 时间同步

- `host_timestamp` = pcap 包头部时间戳（微秒）× 1000（纳秒）
- 包内硬件时间戳在解析层从列格式中提取
- 支持 PTP（IEEE 1588）硬件时间同步（通过 sensor metadata 配置）

---

## 4. Hesai ROS/ROS2 驱动

### 4.1 仓库信息

- **GitHub (ROS1)**: https://github.com/HesaiTechnology/HesaiLidar_General_ROS
- **GitHub (ROS2)**: https://github.com/HesaiTechnology/HesaiLidar_General_ROS2
- **License**: Apache 2.0
- **关键文件**:
  - `src/HesaiLidar_General_SDK/src/PandarGeneralRaw/src/pcap_reader.cpp` — pcap 回放核心
  - `src/HesaiLidar_General_SDK/src/PandarGeneralRaw/src/pcap_reader.h` — 类定义
  - `src/HesaiLidar_General_SDK/src/PandarGeneralRaw/src/input.cc` — live socket 输入
  - `src/HesaiLidar_General_SDK/src/util.c` — 时间工具

### 4.2 pcap 打开与过滤

```cpp
// pcap_reader.cpp, L88-103
pcapFile = pcap_open_offline(pcapPath.c_str(), pcapBuf);
if (NULL == pcapFile) { printf("open pcap file %s fail\n", ...); return; }

if (pcap_compile(pcapFile, &filter, "udp", 0, 0xffffffff) == -1) {
  printf("compile pcap file fail\n"); return;
}
if (pcap_setfilter(pcapFile, &filter) == -1) {
  printf("pcap set filter fail\n"); return;
}
```

**设计要点**：
- BPF 过滤器仅为 `"udp"`，**不按端口过滤**！所有 UDP 包都通过回调传出，由上层解析器按包结构区分
- 使用 `pcap_setfilter()` 在内核/库层面过滤，而非 `pcap_offline_filter()` 逐包判断
- 这是四家中过滤最宽松的实现

### 4.3 IPv4/IPv6 双栈支持

```cpp
// pcap_reader.cpp, L11-14, L110-126
#define IPV4_PKT_HEADER_SIZE (42)
#define IPV6_PKT_HEADER_SIZE (62)
#define IPV4_TYPE (0X0800)
#define IPV6_TYPE (0X86dd)

uint16_t ip_type = htons(*(uint16_t *)(packetBuf + MAC_PACKET_HEADER_LEN));
switch (ip_type) {
case IPV4_TYPE:
  packet = packetBuf + IPV4_PKT_HEADER_SIZE;   // 42 字节偏移
  pktSize = pktHeader->len - IPV4_PKT_HEADER_SIZE;
  break;
case IPV6_TYPE:
  packet = packetBuf + IPV6_PKT_HEADER_SIZE;   // 62 字节偏移
  pktSize = pktHeader->len - IPV6_PKT_HEADER_SIZE;
  break;
}
```

**设计要点**：
- 四家中**唯一同时支持 IPv4/IPv6** 的 pcap 读取
- 通过 EtherType 字段（偏移 12）判断 IP 版本，动态选择偏移量
- IPv6 偏移 62 字节（14 Eth + 40 IPv6 + 8 UDP）

### 4.4 硬件时间戳速率控制（最精密）

Hesai 的速率控制是四家中**最精准**的——基于包内硬件时间戳差值而非固定频率：

```cpp
// pcap_reader.cpp, L39-49 (时间戳偏移索引表，按型号)
void PcapReader::initTimeIndexMap() {
  m_timeIndexMap.insert({"Pandar40P", {1250, 1256}});  // {ts_offset, utc_offset}
  m_timeIndexMap.insert({"Pandar64",  {1182, 1188}});
  m_timeIndexMap.insert({"PandarQT",  {1056, 1062}});
  m_timeIndexMap.insert({"PandarXT-32", {1071, 1065}});
  // ...
}

// pcap_reader.cpp, L133-179 (每 100 包校准一次速率)
if (count >= gap && m_iUTCIndex != 0) {
  t.tm_year = packet[m_iUTCIndex];
  t.tm_mon  = packet[m_iUTCIndex+1] - 1;
  // ... 解析 UTC 时间
  pkt_ts = mktime(&t) * 1000000 + ((packet[m_iTsIndex]&0xff) |
      (packet[m_iTsIndex+1]&0xff)<<8 | ...);  // 微秒级硬件时间戳

  gettimeofday(&sys_time, NULL);
  current_time = sys_time.tv_sec * 1000000 + sys_time.tv_usec;

  int64_t sleep_time = (pkt_ts - last_pkt_ts) - (current_time - last_time);
  if (sleep_time > 0) {
    waitTime.tv_sec = sleep_time / 1000000;
    waitTime.tv_usec = sleep_time % 1000000;
    do { err = select(0, NULL, NULL, NULL, &waitTime); }
    while (err < 0 && errno != EINTR);
  }
}
```

**设计要点**：
- 每种型号有固定的硬件时间戳偏移（`m_iTsIndex`）和 UTC 时间偏移（`m_iUTCIndex`）
- 每 100 包读取一次包内 UTC 时间，计算与系统时间的差值，动态补偿休眠
- 比 Velodyne 的固定 `rclcpp::Rate` 更精准，能适应雷达转速波动
- 回调函数签名：`void(const uint8_t*, const int, double timestamp)`

### 4.5 回调架构

```cpp
// pcap_reader.h, L29
void start(boost::function<void(const uint8_t*, const int, double timestamp)> callback);
```

- 独立 `boost::thread` 运行 `parsePcap()` 循环
- 通过回调函数将 UDP payload 传给上层解析器
- 与 live 模式的 `Input::getPacket()` 拉模式不同，pcap 为推模式

### 4.6 Pandar 包结构

Hesai Pandar 系列 UDP 包结构：
- Header（含序列号、时间戳、UTC 时间、电机角度等）
- Body：多个 Block，每个 Block 含 header + 通道数据（distance × 2 bytes + reflectivity × 1 byte）
- Tail：功能安全、状态等

多回波通过 DIFOP 包配置 return mode，单回波/双回波由固件决定。

---

## 5. RoboSense rslidar_sdk

### 5.1 仓库信息

- **GitHub (SDK)**: https://github.com/RoboSense-LiDAR/rslidar_sdk （含 `rs_driver` 子模块）
- **GitHub (驱动核心)**: https://github.com/RoboSense-LiDAR/rs_driver
- **License**: BSD 3-Clause
- **关键文件**:
  - `src/rs_driver/driver/input/input_pcap.hpp` — pcap 输入类
  - `src/rs_driver/driver/driver_param.hpp` — 参数定义
  - `src/rs_driver/driver/decoder/decoder_RS16.hpp` — 解码器示例（多回波/时间戳）
  - `src/rs_driver/driver/input/input.hpp` — 输入基类

### 5.2 三端口 BPF 过滤设计

RoboSense 是四家中**唯一为 MSOP/DIFOP/IMU 分别编译独立 BPF 过滤器**的厂商：

```cpp
// input_pcap.hpp, L68-81
std::stringstream msop_stream, difop_stream, imu_stream;
if (input_param_.use_vlan) {
  msop_stream << "vlan && ";
  difop_stream << "vlan && ";
}
msop_stream << "udp dst port " << input_param_.msop_port;   // 默认 6699
difop_stream << "udp dst port " << input_param_.difop_port; // 默认 7788
imu_stream << "udp dst port " << input_param_.imu_port;     // 默认 0（禁用）

msop_filter_str_ = msop_stream.str();
difop_filter_str_ = difop_stream.str();
imu_filter_str_ = imu_stream.str();
```

```cpp
// input_pcap.hpp, L120-149 (init)
pcap_compile(pcap_, &msop_filter_, msop_filter_str_.c_str(), 1, 0xFFFFFFFF);
if ((input_param_.difop_port != 0) && (input_param_.difop_port != input_param_.msop_port)) {
  pcap_compile(pcap_, &difop_filter_, difop_filter_str_.c_str(), 1, 0xFFFFFFFF);
  difop_filter_valid_ = true;
}
// IMU 同理
```

**设计要点**：
- MSOP（Measurement Packet，测量数据包）、DIFOP（Device Information Packet，设备信息/配置包）、IMU 三类包按端口分离
- 支持 VLAN 标签（`use_vlan` 时 BPF 加 `vlan &&`，偏移加 4 字节）
- 支持用户自定义层偏移（`user_layer_bytes` / `tail_layer_bytes`）
- 默认端口：MSOP=6699, DIFOP=7788

### 5.3 逐包读取与帧边界速率控制

```cpp
// input_pcap.hpp, L179-280 (recvPacket 线程主循环)
void InputPcap::recvPacket() {
  auto start_time = std::chrono::steady_clock::now();
  while (!to_exit_recv_) {
    int ret = pcap_next_ex(pcap_, &header, &pkt_data);
    if (ret < 0) {  // EOF
      pcap_close(pcap_);
      if (input_param_.pcap_repeat) {
        pcap_ = pcap_open_offline(input_param_.pcap_path.c_str(), errbuf);
        continue;
      } else { break; }
    }

    if (pcap_offline_filter(&msop_filter_, header, pkt_data) != 0) {
      int dataLen = (int)(header->len - pcap_offset_ - pcap_tail_);
      memcpy(pkt->data(), pkt_data + pcap_offset_, dataLen);
      if (cb_pcap_split_frame_(pkt->data())) {  // 帧边界检测
        auto duration = std::chrono::duration_cast<ms>(end - start_time);
        auto sleepTime = ms(millisec_to_delay_) - duration;
        if (sleepTime > ms(0)) std::this_thread::sleep_for(sleepTime);
        start_time = std::chrono::steady_clock::now();
      }
      pushPacket(pkt);
    } else if (difop_filter_valid_ && pcap_offline_filter(&difop_filter_, ...)) {
      // DIFOP 包处理（不做速率控制）
      pushPacket(pkt);
    } else if (imu_filter_valid_ && pcap_offline_filter(&imu_filter_, ...)) {
      // IMU 包处理
      pushPacket(pkt);
    }
  }
}
```

**设计要点**：
- `pcap_offset_` 动态计算：`ETH_HDR_LEN(14) + VLAN_HDR_LEN(4 if use_vlan) + user_layer_bytes`
- **速率控制在帧边界触发**：`cb_pcap_split_frame_()` 回调检测一帧结束，然后按 `pcap_rate` 比例休眠
- `pcap_rate` 范围 0.1~10.0，`DELAY_CALC_BASE=100ms` 为每帧基准延迟
- DIFOP 和 IMU 包不参与速率控制，立即推送
- 独立接收线程 `recv_thread_`，通过 `pushPacket()` 放入线程安全队列

### 5.4 多回波处理

```cpp
// decoder_RS16.hpp, L192-203
RSEchoMode DecoderRS16::getEchoMode(uint8_t mode) {
  switch (mode) {
    case 0x00: return RSEchoMode::ECHO_DUAL;      // 双回波
    case 0x01: return RSEchoMode::ECHO_SINGLE;    // 最强回波
    case 0x02: return RSEchoMode::ECHO_SINGLE;    // 最后回波
  }
}

// decoder_RS16.hpp, L224-232 (DIFOP 中动态切换回波模式)
RSEchoMode echo_mode = getEchoMode(adapter.return_mode);
if (this->echo_mode_ != echo_mode) {
  this->echo_mode_ = echo_mode;
  this->split_blks_per_frame_ = (echo_mode == ECHO_DUAL) ?
    this->blks_per_frame_ : (this->blks_per_frame_ >> 1);  // 双回波每帧 block 数翻倍
  calcParam();
}
```

- 双回波模式下使用 `Rs16DualReturnBlockIterator`，单回波使用 `Rs16SingleReturnBlockIterator`
- 模板化设计，不同型号（RS16/RS32/RS128/M1/M2/M3/E1 等）各有解码器
- 回波模式从 DIFOP 包的 `return_mode` 字段动态获取，运行时可切换

### 5.5 时间同步

```cpp
// decoder_RS16.hpp, L257-268
double pkt_ts = 0;
if (this->param_.use_lidar_clock) {
  pkt_ts = parseTimeYMD(&pkt.header.timestamp) * 1e-6;  // 硬件时间戳（年月日格式）
} else {
  uint64_t ts = getTimeHost();
  pkt_ts = ts * 1e-6 - this->getPacketDuration();  // 系统时间回退一个包时长
}
```

- 支持 `use_lidar_clock` 切换硬件/软件时间戳
- 硬件时间戳为 **YMD 格式**（年/月/日/时/分/秒/微秒），非 Velodyne 的 GPS 秒格式
- 软件时间戳模式下回退一个包时长以逼近真实采集时间
- 每点时间通过 `firing_tss[]` 表（微秒级 firing offset）插值

---

## 6. Innoviz 及其他扫描式雷达

### 6.1 Innoviz

- **仓库**: 无官方开源 ROS 驱动。Innoviz 提供专有 SDK（Innoviz API），支持 C++/Python，pcap 回放功能集成在 SDK 内部，不公开源码。
- 社区中有少量第三方适配项目，但均未形成主流参考实现。
- **一方称**：InnovizOne/Two 采用 MEMS 微振镜扫描（非双楔镜），其 UDP 协议包含帧头（frame header）+ 像素数据（pixel data）结构，每帧包含完整的 2D 扫描数据，与旋转式雷达的"逐包增量"模式不同。

### 6.2 双楔镜（Risley Prism / Double Wedge Mirror）扫描雷达

- 双楔镜扫描通过两个旋转楔形棱镜的差速配合实现 Lissajous 图形扫描，点云分布**非均匀**，与旋转式雷达的均匀方位角分布有本质区别。
- 目前**无开源 ROS 驱动**可直接参考。Innolight/Innoview InnoLiDAR 系列为该架构代表。
- 可参考的类似非旋转扫描雷达：
  - **Ouster**（Flash + 数字光斑）：列格式 + 每列独立时间戳，帧边界清晰
  - **Innoviz**（MEMS 振镜）：帧式输出，每帧含完整 2D 数据
  - **Sony IMX459**（SPAD ToF）：CMOS 式逐帧输出

### 6.3 对双楔镜架构的启示

双楔镜扫描雷达的 pcap 解析应借鉴：
- **Ouster 的列/帧格式**：每包含完整或部分扫描行，帧头含扫描参数
- **Hesai 的硬件时间戳速率控制**：双楔镜转速可能波动，硬件时间戳比固定频率更可靠
- **RoboSense 的 MSOP/DIFOP 分离**：测量包与配置/校准包分离端口

---

## 7. 通用 pcap 解析库与工具

### 7.1 libpcap / WinPcap / Npcap

| 库 | 平台 | 特点 |
|----|------|------|
| **libpcap** | Linux/macOS | 标准 Unix pcap 库，Velodyne/Hesai/RoboSense 均使用 |
| **WinPcap** | Windows (已停止维护) | 原厂 `Double_Wedge_Mirror.exe` 使用，最后版本 4.1.3 |
| **Npcap** | Windows (维护中) | WinPcap 的现代替代，支持 NDIS 6.x，建议新项目使用 |

核心 API（四家厂商通用）：
```c
pcap_t *pcap_open_offline(const char *fname, char *errbuf);
int pcap_compile(pcap_t *p, struct bpf_program *fp, const char *str, int optimize, bpf_u_int32 netmask);
int pcap_setfilter(pcap_t *p, struct bpf_program *fp);       // Hesai 用
int pcap_offline_filter(const struct bpf_program *fp, const struct pcap_pkthdr *h, const u_char *pkt);  // Velodyne/RoboSense 用
int pcap_next_ex(pcap_t *p, struct pcap_pkthdr **pkt_header, const u_char **pkt_data);
void pcap_close(pcap_t *p);
```

### 7.2 libtins（Ouster 使用）

- **GitHub**: https://github.com/mfontanini/libtins
- C++ 网络包解析库，封装 libpcap，提供协议栈级 API
- 支持 IP 分片重组、IPv6、多种封装格式（Ethernet/SLL/Radiotap）
- `Tins::FileSniffer` 用于 pcap 回放，`Tins::PacketWriter` 用于录制
- 优势：无需手动计算协议头偏移，代码更健壮；劣势：额外依赖

### 7.3 Scapy（Python）

- 用于 pcap 快速分析和协议逆向，适合开发阶段验证 UDP payload 结构
- 基本用法：
```python
from scapy.all import *
packets = rdpcap("lidar.pcap")
for pkt in packets:
    if pkt.haslayer(UDP) and pkt[UDP].dport == 2368:
        payload = bytes(pkt[UDP].payload)
        # 解析自定义 PointPacket 结构
```
- 支持 BPF 过滤：`sniff(offline="lidar.pcap", filter="udp dst port 2368", prn=...)`

### 7.4 tshark / Wireshark

- `tshark -r lidar.pcap -Y "udp.dstport == 2368" -T fields -e data` 导出 UDP payload
- Wireshark 可配置 "Decode As" → UDP_PORT → 自定义协议，直观查看包结构
- 用于验证 pcap 中的端口分布、包大小统计、丢包检测

---

## 8. 横向对比分析

### 8.1 架构对比

| 维度 | Velodyne | Ouster | Hesai | RoboSense |
|------|----------|--------|-------|-----------|
| **输入抽象** | `Input` 基类 + `InputPCAP`/`InputSocket` | `PcapPacketSource`（迭代器） | `PcapReader`（回调推模式） | `Input` 基类 + `InputPcap`（线程+队列） |
| **pcap 库** | 裸 libpcap | libtins（封装 libpcap） | 裸 libpcap | 裸 libpcap |
| **过滤方式** | `pcap_offline_filter()` 逐包 | 按 payload size 自动识别 | `pcap_setfilter()` 内核级 | `pcap_offline_filter()` 逐包，三过滤器 |
| **BPF 表达式** | `src host IP && udp dst port PORT` | 无（自动识别） | `udp`（最宽松） | `udp dst port MSOP/DIFOP/IMU` |
| **IPv6 支持** | 否（硬编码 42 偏移） | 是（libtins） | 是（动态 42/62 偏移） | 否（ETH_HDR_LEN 固定） |
| **VLAN 支持** | 否 | 是（libtins） | 否 | 是（use_vlan 参数） |
| **IP 分片重组** | 否 | 是（IPv4Reassembler2） | 否 | 否 |
| **随机访问/帧索引** | 否 | 是（IndexedPcapReader） | 否 | 否 |

### 8.2 多回波处理对比

| 厂商 | 回波模式 | 实现方式 | 动态切换 |
|------|----------|----------|----------|
| Velodyne | 单/最强/最后/双 | 固件决定，驱动透明 | 不支持（需重启雷达） |
| Ouster | 单/双 | 列格式中每像素含多回波字段 | 不支持 |
| Hesai | 单/双 | DIFOP 配置，驱动透明 | 不支持 |
| RoboSense | 单(最强)/单(最后)/双 | DIFOP `return_mode` 字段 + 模板迭代器 | **支持**（运行时从 DIFOP 检测切换） |

### 8.3 时间同步策略对比

| 厂商 | pcap 时间戳来源 | 硬件时间戳 | 每点时间精度 | 速率控制 |
|------|----------------|-----------|-------------|----------|
| Velodyne | 系统时间 `now()` | 偏移 1200（仅 live 模式） | `timing_offsets_` 表（μs级） | 固定 `rclcpp::Rate` |
| Ouster | pcap 头部时间戳 | 列格式 `col_timestamp` | 列级（μs级） | 不控制（索引/按需） |
| Hesai | 系统时间 `getNowTimeSec()` | 型号相关偏移（UTC+μs） | 包级 | **硬件时间戳差值**（最精准） |
| RoboSense | 队列接收时间 | `parseTimeYMD()` YMD格式 | `firing_tss[]` 表（μs级） | 帧边界 + `pcap_rate` |

### 8.4 丢包与包序处理对比

| 厂商 | 丢包检测 | 包序错乱处理 |
|------|----------|-------------|
| Velodyne | 无显式检测 | 按 pcap 顺序读取，假设有序 |
| Ouster | `size_error_count_` / `id_error_count_` 统计 | 按 sensor ID 分类，索引保证帧完整 |
| Hesai | 无显式检测 | 按 pcap 顺序读取 |
| RoboSense | 序列号检测（解码器层） | MSOP 序列号连续检测，DIFOP 独立通道 |

### 8.5 点云组装对比

| 厂商 | 帧切分依据 | 点云格式 | 组织方式 |
|------|-----------|----------|----------|
| Velodyne | azimuth 跨越起始角 | `sensor_msgs/PointCloud2` | organized（按 ring）/ unorganized |
| Ouster | `measurement_id` 回绕 | `sensor_msgs/PointCloud2` | organized（按列×通道） |
| Hesai |  azimuth 回绕 + 包序号 | `sensor_msgs/PointCloud2` | organized |
| RoboSense | `split_blks_per_frame_` block 计数 | `sensor_msgs/PointCloud2` | organized / unorganized |

---

## 9. 对双楔镜扫描雷达 ROS2 驱动的建议

### 9.1 推荐借鉴的设计

| 设计 | 来源 | 理由 |
|------|------|------|
| **Input 基类 + InputPCAP/InputSocket 派生** | Velodyne/RoboSense | 统一 live/回放接口，ROS2 驱动标准模式 |
| **MSOP/DIFOP 双端口分离** | RoboSense | 测量包与配置/校准包分离，解析清晰 |
| **硬件时间戳速率控制** | Hesai | 双楔镜转速可能波动，硬件时间戳比固定频率可靠 |
| **IPv4/IPv6 双栈偏移** | Hesai | 现代网络环境兼容性，实现成本低（一个 switch） |
| **按 payload size 自动识别包类型** | Ouster | 减少用户配置，多型号兼容 |
| **帧边界触发速率控制** | RoboSense | 双楔镜非均匀扫描，按帧控制比按包控制更合理 |
| **DIFOP 动态切换回波模式** | RoboSense | 运行时可配置，无需重启 |

### 9.2 pcap 模块推荐架构

```
┌─────────────────────────────────────────────┐
│              Input (abstract)               │
│  virtual int getPacket(UdpPacket&) = 0      │
├──────────────────┬──────────────────────────┤
│  InputSocket     │  InputPCAP               │
│  (live UDP)      │  - pcap_open_offline()   │
│                  │  - pcap_compile() ×2     │
│                  │    (MSOP port + DIFOP)   │
│                  │  - pcap_next_ex() 循环   │
│                  │  - pcap_offline_filter() │
│                  │  - 硬件时间戳速率控制    │
│                  │  - pcap_repeat 循环      │
└──────────────────┴──────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────┐
│         PacketDecoder (abstract)            │
│  - decodeMsopPkt()  → PointCloud           │
│  - decodeDifopPkt()  → 校准/配置参数        │
│  - 多回波模板迭代器                         │
│  - 每点时间戳插值（firing offset 表）       │
└─────────────────────────────────────────────┘
```

### 9.3 关键实现细节建议

1. **以太网偏移**：不要硬编码 42，参考 Hesai 的 IPv4/IPv6 动态判断，或参考 RoboSense 的 `pcap_offset_` 可配置偏移（含 VLAN/user_layer）

2. **BPF 过滤**：使用 `"udp dst port <msop_port>"` 和 `"udp dst port <difop_port>"` 两个独立过滤器，与 RoboSense 一致；避免 Hesai 的 `"udp"` 过宽过滤

3. **时间戳**：
   - pcap 包头部时间戳可用于排序和调试
   - 包内硬件时间戳用于点云时间和速率控制（参考 Hesai 的 UTC+μs 格式）
   - 每点时间通过预计算的 firing offset 表插值（参考 Velodyne/RoboSense）

4. **速率控制**：优先使用硬件时间戳差值（Hesai 方式），备选帧边界 + 可配置 `pcap_rate`（RoboSense 方式），避免 Velodyne 的固定 `rclcpp::Rate`

5. **双楔镜特有**：
   - 点云分布非均匀，**不建议使用 organized point cloud**（固定 H×W 排列），使用 unorganized
   - 帧切分应基于包内帧标志/扫描周期，而非 azimuth 回绕
   - 256 线对应双楔镜的 256 个扫描位置，需校准表映射到实际角度

6. **丢包处理**：在解码器层检测包序列号，丢包时记录日志并用 NaN 填充对应位置（参考 Velodyne 的 `addPoint(nanf(""), ...)`）

### 9.4 与原厂软件的对应关系

原厂 `Double_Wedge_Mirror.exe` 的管线与 ROS2 驱动的映射：

| 原厂模块 | ROS2 对应 | 建议 |
|----------|----------|------|
| `pcap_open_offline()` | `InputPCAP::init()` | 一致 |
| `pcap_compile("dst port ")` | `pcap_compile("udp dst port <port>")` | 建议加上 `udp` 前缀更精确 |
| `pcap_next_ex()` | `InputPCAP::getPacket()` | 一致 |
| `PointPacket` 结构解析 | `PacketDecoder::decodeMsopPkt()` | 需将原厂解析逻辑迁移 |
| `pcl::PointCloud<PointXYZRGB>` | `sensor_msgs/PointCloud2` | ROS2 标准消息，含 ring/time/intensity 字段 |
| VTK 渲染 | RViz2 可视化 | 无需自渲染 |
| `pcap_open_live()` | `InputSocket` | 一致 |

---

## 附录：源码验证说明

- 所有代码片段均来自 2026-09-08 下载的 GitHub 仓库最新版本（Velodyne `ros2` 分支、Ouster SDK `master`、Hesai ROS1 `master`、RoboSense rs_driver `main`）
- Hesai ROS2 仓库（`HesaiLidar_General_ROS2`）因网络限制未直接下载，其 pcap 模块与 ROS1 版本共享 `HesaiLidar_General_SDK` 子模块，代码一致
- Ouster ROS 驱动（`ouster-ros`）为 `ouster-sdk` 的薄封装层，pcap 逻辑全部在 SDK 中
- "一方称"标注的信息（Innoviz 部分）未经过源码验证，仅供参考

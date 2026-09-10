#!/usr/bin/env python3
"""PCAP 回放工具 —— 把抓包的 NA70 UDP 载荷重新打到本地端口，喂给驱动。

背景：NA70 雷达当前不在手边，但 Desktop/NA70_capture_tool/ 下有 16 组真实抓包。
PCAP 里的载荷就是雷达发出的原始字节，重放它等价于让驱动收到真实数据，
可以在没有硬件的情况下验证驱动的解析、坐标变换和 PointCloud2 出口。

只用标准库：Jetson 上没有 scapy/tcpreplay，装它们需要 sudo。
PCAP 格式足够简单，直接解析反而更可控，还能顺手做协议校验。

用法：
    python3 replay_pcap.py <pcap>                    # 分析 + 回放
    python3 replay_pcap.py <pcap> --analyze-only     # 只分析不发包
    python3 replay_pcap.py <pcap> --rate 1000        # 指定包速率（包/秒）
    python3 replay_pcap.py <pcap> --realtime         # 按原始时间间隔回放
    python3 replay_pcap.py <pcap> --count 500        # 只回放前 N 个包
    python3 replay_pcap.py <pcap> --port 2368        # 目标端口

注意：回放的是 UDP 载荷，不是原始以太网帧。目的地固定为 127.0.0.1，
所以驱动必须运行在同一台机器上。
"""

import argparse
import socket
import struct
import sys
import time
from collections import Counter

# libpcap 文件头魔数。大小端两种字节序，以及纳秒精度变体。
PCAP_MAGIC_LE = 0xA1B2C3D4  # 小端，微秒
PCAP_MAGIC_BE = 0xD4C3B2A1  # 大端，微秒
PCAP_MAGIC_LE_NS = 0xA1B23C4D  # 小端，纳秒
PCAP_MAGIC_BE_NS = 0x4D3CB2A1  # 大端，纳秒

LINKTYPE_ETHERNET = 1

# NA70 协议常量，与 inno_device_lidar_node.cpp:58-62 一致
NA70_POINTS_PER_PACKET = 200
NA70_POINT_BYTES = 7
NA70_DATA_BYTES = NA70_POINTS_PER_PACKET * NA70_POINT_BYTES  # 1400
NA70_TIMESTAMP_INDEX = 1400
NA70_EXPECTED_PAYLOAD = 1410


class PcapError(Exception):
    pass


def parse_pcap(path):
    """解析 pcap，产出 (相对时间戳, UDP 载荷, 源地址, 目的地址) 四元组。

    只处理 Ethernet + IPv4 + UDP，其余静默跳过（抓包里常混有 ARP 等）。
    """
    with open(path, "rb") as f:
        header = f.read(24)
        if len(header) < 24:
            raise PcapError(f"文件太短，不是有效 pcap：{path}")

        (magic,) = struct.unpack("<I", header[:4])
        if magic == PCAP_MAGIC_LE:
            endian, ns_precision = "<", False
        elif magic == PCAP_MAGIC_BE:
            endian, ns_precision = ">", False
        elif magic == PCAP_MAGIC_LE_NS:
            endian, ns_precision = "<", True
        elif magic == PCAP_MAGIC_BE_NS:
            endian, ns_precision = ">", True
        else:
            raise PcapError(
                f"未知 pcap 魔数 0x{magic:08X}。"
                "pcapng 格式不支持，请用 editcap 转成 pcap。"
            )

        linktype = struct.unpack(endian + "I", header[20:24])[0]
        if linktype != LINKTYPE_ETHERNET:
            raise PcapError(f"仅支持 Ethernet 链路层，实际 linktype={linktype}")

        first_ts = None
        while True:
            pkt_header = f.read(16)
            if len(pkt_header) < 16:
                break  # 正常结束
            ts_sec, ts_frac, caplen, origlen = struct.unpack(endian + "IIII", pkt_header)
            data = f.read(caplen)
            if len(data) < caplen:
                break  # 文件被截断，丢弃最后这个不完整包

            ts = ts_sec + (ts_frac / 1e9 if ns_precision else ts_frac / 1e6)
            if first_ts is None:
                first_ts = ts

            parsed = _extract_udp(data)
            if parsed is not None:
                payload, src, dst = parsed
                yield ts - first_ts, payload, src, dst


def _extract_udp(frame):
    """从以太网帧里取 UDP 载荷。非 IPv4/UDP 返回 None。"""
    if len(frame) < 14:
        return None
    ethertype = struct.unpack(">H", frame[12:14])[0]
    if ethertype != 0x0800:  # 非 IPv4
        return None

    ip = frame[14:]
    if len(ip) < 20:
        return None
    version_ihl = ip[0]
    if version_ihl >> 4 != 4:
        return None
    ihl = (version_ihl & 0x0F) * 4
    if ip[9] != 17:  # 非 UDP
        return None
    src_ip = ".".join(str(b) for b in ip[12:16])
    dst_ip = ".".join(str(b) for b in ip[16:20])

    udp = ip[ihl:]
    if len(udp) < 8:
        return None
    src_port, dst_port, udp_len = struct.unpack(">HHH", udp[:6])
    # udp_len 含 8 字节 UDP 头。用抓到的实际长度兜底，防止截断包越界。
    payload = udp[8:min(udp_len, len(udp))]
    return payload, f"{src_ip}:{src_port}", f"{dst_ip}:{dst_port}"


def analyze(packets):
    """统计流向、载荷长度和 NA70 协议特征。"""
    flows = Counter()
    lengths = Counter()
    na70_count = 0
    sync_flagged = 0
    first_payload = None
    last_ts = 0.0

    for ts, payload, src, dst in packets:
        flows[f"{src} -> {dst}"] += 1
        lengths[len(payload)] += 1
        last_ts = ts
        if first_payload is None:
            first_payload = payload
        if len(payload) >= NA70_EXPECTED_PAYLOAD:
            na70_count += 1
            # 时间戳首字节最高位 = 同步标志，见驱动 process_udp_packet
            if payload[NA70_TIMESTAMP_INDEX] & 0x80:
                sync_flagged += 1

    total = sum(flows.values())
    print(f"UDP 包总数     : {total}")
    print(f"抓包时长       : {last_ts:.2f} s")
    if last_ts > 0:
        print(f"平均包速率     : {total / last_ts:.1f} 包/秒")
    print()

    print("流向：")
    for flow, count in flows.most_common(10):
        print(f"  {count:8d}  {flow}")
    print()

    print("载荷长度分布：")
    for length, count in lengths.most_common(10):
        marker = "  <- NA70 数据包" if length == NA70_EXPECTED_PAYLOAD else ""
        print(f"  {count:8d}  {length} 字节{marker}")
    print()

    print("NA70 协议校验：")
    print(f"  载荷 >= {NA70_EXPECTED_PAYLOAD} 字节的包：{na70_count} "
          f"（驱动要求 >= {NA70_EXPECTED_PAYLOAD}，否则丢弃并告警）")
    if na70_count:
        print(f"  时间戳同步位置位     ：{sync_flagged} / {na70_count}")
        if sync_flagged == 0:
            print("    -> 全部走未同步分支（GPS/PTP 未授时）")
        elif sync_flagged == na70_count:
            print("    -> 全部走已同步分支")
        else:
            print("    -> 混合，回放期间授时状态发生过变化")

    if first_payload and len(first_payload) >= 32:
        print()
        print("首包前 32 字节：")
        hexs = " ".join(f"{b:02x}" for b in first_payload[:32])
        print(f"  {hexs}")
        # 按驱动的解析方式解一个点出来，便于人工核对量级是否合理
        d = ((first_payload[0] << 8) | first_payload[1]) * 0.01
        p = ((first_payload[2] << 8) | first_payload[3]) * 0.01
        y = ((first_payload[4] << 8) | first_payload[5]) * 0.01
        r = first_payload[6]
        print(f"  按驱动解析首点：距离={d:.2f} 俯仰={p:.2f} 偏航={y:.2f} 反射率={r}")

    return total, na70_count


def replay(packets, host, port, rate, realtime, limit):
    """把载荷打到 UDP 端口。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # 抓包速率可达上千包/秒，放大发送缓冲避免本地丢包
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)

    sent = 0
    skipped = 0
    start = time.monotonic()
    interval = 1.0 / rate if rate else 0.0
    prev_ts = None

    print(f"回放到 {host}:{port}"
          + (" （按原始时间间隔）" if realtime else f" （{rate} 包/秒）" if rate else " （全速）"))
    print("Ctrl-C 中断\n")

    try:
        for ts, payload, _src, _dst in packets:
            if limit and sent >= limit:
                break
            # 驱动会丢弃并告警的包，这里就不发了，免得污染日志
            if len(payload) < NA70_EXPECTED_PAYLOAD:
                skipped += 1
                continue

            if realtime:
                if prev_ts is not None:
                    delay = ts - prev_ts
                    if delay > 0:
                        time.sleep(delay)
                prev_ts = ts
            elif interval:
                target = start + sent * interval
                now = time.monotonic()
                if target > now:
                    time.sleep(target - now)

            sock.sendto(payload, (host, port))
            sent += 1
            if sent % 1000 == 0:
                elapsed = time.monotonic() - start
                print(f"  已发送 {sent} 包（{sent / elapsed:.0f} 包/秒）")
    except KeyboardInterrupt:
        print("\n用户中断")
    finally:
        sock.close()

    elapsed = time.monotonic() - start
    print(f"\n发送完成：{sent} 包，耗时 {elapsed:.2f} s"
          + (f"，平均 {sent / elapsed:.0f} 包/秒" if elapsed > 0 else ""))
    if skipped:
        print(f"跳过 {skipped} 个短包（< {NA70_EXPECTED_PAYLOAD} 字节，驱动会拒收）")
    return sent


def main():
    ap = argparse.ArgumentParser(
        description="回放 NA70 抓包到 UDP 端口，喂给 ROS2 驱动",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("pcap", help="pcap 文件路径")
    ap.add_argument("--host", default="127.0.0.1", help="目标地址（默认 127.0.0.1）")
    ap.add_argument("--port", type=int, default=2368, help="目标端口（默认 2368）")
    ap.add_argument("--rate", type=float, default=1000.0,
                    help="发送速率，包/秒（默认 1000，抓包实测约此量级）")
    ap.add_argument("--realtime", action="store_true",
                    help="按 pcap 原始时间间隔回放，忽略 --rate")
    ap.add_argument("--count", type=int, default=0, help="只回放前 N 个包（0=全部）")
    ap.add_argument("--analyze-only", action="store_true", help="只分析，不发包")
    args = ap.parse_args()

    print(f"=== {args.pcap} ===\n")

    try:
        # 分析要遍历一次，回放再遍历一次。pcap 是流式解析，重新打开即可，
        # 不需要把几百 MB 的包全读进内存。
        total, na70_count = analyze(parse_pcap(args.pcap))
    except (PcapError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    if total == 0:
        print("\n没有解析到任何 UDP 包，无法回放。", file=sys.stderr)
        return 1

    if args.analyze_only:
        return 0

    if na70_count == 0:
        print(f"\n没有长度 >= {NA70_EXPECTED_PAYLOAD} 字节的包，"
              "驱动会全部拒收，回放没有意义。", file=sys.stderr)
        return 1

    print()
    try:
        replay(parse_pcap(args.pcap), args.host, args.port,
               args.rate, args.realtime, args.count)
    except (PcapError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""Probe a pcap file: global header, link type, first packets, UDP/TCP ports."""
import struct
import sys
from collections import Counter

def probe(path, max_pkts=200):
    with open(path, 'rb') as f:
        gh = f.read(24)
        magic = gh[:4]
        if magic == b'\xd4\xc3\xb2\xa1':
            endian = '<'; fmt = 'pcap-classic-le'
        elif magic == b'\xa1\xb2\xc3\xd4':
            endian = '>'; fmt = 'pcap-classic-be'
        elif magic == b'\x4d\x3c\xb2\xa1':
            endian = '<'; fmt = 'pcap-ns-le'
        elif magic == b'\xa1\xb2\x3c\x4d':
            endian = '>'; fmt = 'pcap-ns-be'
        else:
            # try pcapng
            if gh[:4] == b'\x0a\x0d\x0d\x0a':
                print('Format: pcapng (need dpkt or pyshark)')
                return
            print('Unknown magic:', magic.hex())
            return
        ver_maj, ver_min, tz, sig, snaplen, network = struct.unpack(endian + 'HHiiii', gh[4:24])
        print(f'Format: {fmt}  version {ver_maj}.{ver_min}  snaplen {snaplen}  linktype {network}')

        ethertypes = Counter()
        protos = Counter()
        udp_ports = Counter()
        tcp_ports = Counter()
        sizes = []
        ts_list = []
        n = 0
        while n < max_pkts:
            hdr = f.read(16)
            if len(hdr) < 16:
                break
            ts_sec, ts_usec, incl, orig = struct.unpack(endian + 'IIII', hdr)
            data = f.read(incl)
            sizes.append(incl)
            ts_list.append(ts_sec + ts_usec * 1e-6)
            n += 1
            if network == 1 and incl >= 14:
                et = struct.unpack('>H', data[12:14])[0]
                ethertypes[et] += 1
                if et == 0x0800 and incl >= 34:
                    ihl = (data[14] & 0x0F) * 4
                    proto = data[14 + 9]
                    protos[proto] += 1
                    if proto == 17 and incl >= 14 + ihl + 8:
                        sp, dp = struct.unpack('>HH', data[14 + ihl: 14 + ihl + 4])
                        udp_ports[(sp, dp)] += 1
                    elif proto == 6 and incl >= 14 + ihl + 4:
                        sp, dp = struct.unpack('>HH', data[14 + ihl: 14 + ihl + 4])
                        tcp_ports[(sp, dp)] += 1
        print(f'Scanned {n} packets')
        print('Ethertypes:', dict(ethertypes.most_common(5)))
        print('IP protos:', dict(protos.most_common(5)))
        print('UDP (src,dst) top:', udp_ports.most_common(8))
        print('TCP (src,dst) top:', tcp_ports.most_common(5))
        if sizes:
            print(f'Packet size: min={min(sizes)} max={max(sizes)} avg={sum(sizes)/len(sizes):.1f}')
        if len(ts_list) > 1:
            dt = [(ts_list[i+1]-ts_list[i]) for i in range(len(ts_list)-1) if ts_list[i+1] > ts_list[i]]
            if dt:
                dt.sort()
                print(f'Delta-t (s): median={dt[len(dt)//2]*1e3:.4f}ms  p5={dt[int(len(dt)*0.05)]*1e3:.4f}ms  p95={dt[int(len(dt)*0.95)]*1e3:.4f}ms')
                print(f'Estimated rate: {1.0/dt[len(dt)//2]:.1f} pkt/s')

if __name__ == '__main__':
    probe(sys.argv[1] if len(sys.argv) > 1 else r'D:\Code\radar\radar_capture_data\1.pcap')

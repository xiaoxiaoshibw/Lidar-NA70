# -*- coding: utf-8 -*-
"""Dump UDP payloads of a pcap to inspect radar frame format."""
import struct
import sys

def dump(path, max_pkts=8, payload_only=True):
    with open(path, 'rb') as f:
        gh = f.read(24)
        endian = '<'
        network = struct.unpack(endian + 'i', gh[20:24])[0]
        for i in range(max_pkts):
            hdr = f.read(16)
            if len(hdr) < 16:
                break
            ts_sec, ts_usec, incl, orig = struct.unpack(endian + 'IIII', hdr)
            data = f.read(incl)
            # ethernet + ipv4 + udp
            ihl = (data[14] & 0x0F) * 4
            udp = 14 + ihl
            payload = data[udp + 8:]
            print(f'=== pkt {i} ts={ts_sec}.{ts_usec:06d} payload_len={len(payload)}')
            # print hex in 16-byte rows, first 8 rows
            for r in range(min(8, (len(payload) + 15) // 16)):
                chunk = payload[r*16:(r+1)*16]
                hexs = ' '.join(f'{b:02x}' for b in chunk)
                asc = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
                print(f'  {r*16:04x}  {hexs:<48}  {asc}')
            # entropy check of whole payload
            from collections import Counter
            c = Counter(payload)
            ent = -sum((v/len(payload)) * (v/len(payload) and __import__('math').log2(v/len(payload))) for v in c.values())
            print(f'  entropy={ent:.2f} bits/byte  nonzero_ratio={sum(1 for b in payload if b)/len(payload):.3f}')

if __name__ == '__main__':
    dump(sys.argv[1] if len(sys.argv) > 1 else r'D:\Code\radar\radar_capture_data\1.pcap')

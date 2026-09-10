# -*- coding: utf-8 -*-
"""Dump full payload hex for a few representative packets, try multiple point-size hypotheses."""
import struct
import sys
import numpy as np

def read_payloads(path, max_pkts=200000):
    out = []
    with open(path, 'rb') as f:
        f.read(24)
        while len(out) < max_pkts:
            hdr = f.read(16)
            if len(hdr) < 16:
                break
            ts_sec, ts_usec, incl, orig = struct.unpack('<IIII', hdr)
            data = f.read(incl)
            ihl = (data[14] & 0x0F) * 4
            out.append((ts_sec + ts_usec * 1e-6, data[14 + ihl + 8:]))
    return out

def show(payload, title):
    print(f'\n### {title}  len={len(payload)}')
    for r in range(0, min(len(payload), 256), 16):
        chunk = payload[r:r+16]
        hexs = ' '.join(f'{b:02x}' for b in chunk)
        asc = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        print(f'  {r:04x}  {hexs:<48}  {asc}')
    # hypotheses for point size
    for psize in (2, 3, 4, 5, 6):
        if len(payload) % psize == 0:
            n = len(payload) // psize
            print(f'  [hypothesis] {psize} bytes/point -> {n} points')
            if psize == 3:
                vals = [struct.unpack('<HB', payload[i:i+3]) for i in range(0, len(payload), 3)]
                print('    first 10 (u16,u8):', vals[:10])
            elif psize == 4:
                vals = [struct.unpack('<HH', payload[i:i+4]) for i in range(0, len(payload), 4)]
                print('    first 10 (u16,u16):', vals[:10])
            elif psize == 5:
                vals = [struct.unpack('<HHB', payload[i:i+5]) for i in range(0, len(payload), 5)]
                print('    first 10 (u16,u16,u8):', vals[:10])
            elif psize == 6:
                vals = [struct.unpack('<HHH', payload[i:i+6]) for i in range(0, len(payload), 6)]
                print('    first 10 (u16,u16,u16):', vals[:10])

def main(path):
    pkts = read_payloads(path)
    # group by consecutive run of sparse (few points) then dense
    sparse_seen = 0
    dense_seen = 0
    for ts, pl in pkts:
        nz = sum(1 for b in pl if b)
        if nz < 30 and sparse_seen < 2:
            show(pl, f'sparse pkt ts={ts:.3f} nonzero={nz}')
            sparse_seen += 1
        elif nz >= 30 and dense_seen < 2:
            show(pl, f'dense pkt ts={ts:.3f} nonzero={nz}')
            dense_seen += 1
        if sparse_seen >= 2 and dense_seen >= 2:
            break

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else r'D:\Code\radar\radar_capture_data\1.pcap')

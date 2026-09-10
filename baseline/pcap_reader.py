# -*- coding: utf-8 -*-
"""Read pcap, extract radar packets, group points into time-frame slices."""
import struct
import math
from radar_protocol import points_from_payload, PAYLOAD_LEN

LINKTYPE_ETHERNET = 1


def iter_packets(path, max_pkts=None):
    """Yield (ts, payload) for each UDP radar packet."""
    with open(path, 'rb') as f:
        gh = f.read(24)
        if len(gh) < 24:
            return
        if gh[:4] == b'\xd4\xc3\xb2\xa1':
            endian = '<'
        elif gh[:4] == b'\xa1\xb2\xc3\xd4':
            endian = '>'
        else:
            raise ValueError(f'Unsupported pcap magic: {gh[:4].hex()}')
        network = struct.unpack(endian + 'i', gh[20:24])[0]
        n = 0
        while max_pkts is None or n < max_pkts:
            hdr = f.read(16)
            if len(hdr) < 16:
                break
            ts_sec, ts_usec, incl, _ = struct.unpack(endian + 'IIII', hdr)
            data = f.read(incl)
            if len(data) < incl:
                break
            n += 1
            ts = ts_sec + ts_usec * 1e-6
            if network == LINKTYPE_ETHERNET and incl >= 14:
                et = struct.unpack('>H', data[12:14])[0]
                if et != 0x0800 or incl < 34:
                    continue
                ihl = (data[14] & 0x0F) * 4
                if data[14 + 9] != 17:  # UDP
                    continue
                udp_len = struct.unpack('>H', data[14 + ihl + 4:14 + ihl + 6])[0]
                if udp_len < 8:
                    continue
                payload = data[14 + ihl + 8:14 + ihl + udp_len]
                if len(payload) == PAYLOAD_LEN:
                    yield ts, payload


def slice_frames(path, frame_s, max_duration_s=None, max_pkts=None):
    """Group packets into fixed time windows. Each frame: list of points.
    Returns list of (t_start, points) where points are (x, y, amp, az, rng, chan)."""
    frames = []
    cur_start = None
    cur = []
    for ts, pl in iter_packets(path, max_pkts):
        if max_duration_s is not None and cur_start is not None and ts - cur_start > max_duration_s:
            break
        if cur_start is None:
            cur_start = ts
        pts = points_from_payload(pl)
        if pts:
            cur.append(pts)
        if ts - cur_start >= frame_s:
            flat = [p for pts in cur for p in pts]
            frames.append((cur_start, flat))
            cur_start = ts
            cur = []
    if cur:
        flat = [p for pts in cur for p in pts]
        frames.append((cur_start, flat))
    return frames

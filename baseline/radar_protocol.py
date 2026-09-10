# -*- coding: utf-8 -*-
"""Radar protocol constants & frame parser for the Double_Wedge_Mirror radar.

Protocol (reverse-engineered from captured pcap):
  Ethernet -> IPv4 -> UDP, src 7000 -> dst 2368, payload 1410 bytes
  Payload: 10-byte header + 200 points x 7 bytes
  Point layout (little-endian):
    +0 u16 azimuth code   (0..65535 -> 0..360 deg, ANGLE_PER_UNIT=360/65536)
    +2 u16 range code     (range_bin = code >> 8, sub_bin = code & 0xFF)
    +4 u8  amplitude      (echo intensity)
    +5 u16 channel code   (line/channel index mostly in high byte)
  Range: bin * RANGE_PER_BIN meters (default 0.1 m/bin; max ~13.8-17.8 m)
  NOTE: units are assumptions calibrated against config.ini (distance_flag=1380)
        and verified by field smoothness; they are parameterized for re-tuning.
"""
import struct

UDP_SRC_PORT = 7000
UDP_DST_PORT = 2368
PAYLOAD_LEN = 1410
HEADER_LEN = 10
POINTS_PER_PACKET = 200
POINT_LEN = 7

ANGLE_PER_UNIT = 360.0 / 65536.0   # degrees per azimuth code unit
RANGE_PER_BIN = 0.1                # meters per range bin


def parse_point(pl, idx):
    o = HEADER_LEN + idx * POINT_LEN
    az, rng = struct.unpack_from('<HH', pl, o)
    amp = pl[o + 4]
    chan = struct.unpack_from('<H', pl, o + 5)[0]
    return az, rng, amp, chan


def points_from_payload(pl, azimuth_offset=0.0, rng_bias=0.0):
    """Decode payload to list of (x, y, amp, az_deg, range_m, chan).
    Invalid points (az==0 and rng==0) are skipped.
    Returns None if payload length is not as expected."""
    if len(pl) != PAYLOAD_LEN:
        return None
    pts = []
    for i in range(POINTS_PER_PACKET):
        az, rng, amp, chan = parse_point(pl, i)
        if az == 0 and rng == 0:
            continue
        rng_bin = rng >> 8
        if rng_bin == 0:
            continue  # range 0 = invalid echo
        az_deg = (az * ANGLE_PER_UNIT + azimuth_offset) % 360.0
        r_m = rng_bin * RANGE_PER_BIN + rng_bias
        th = az_deg * 3.141592653589793 / 180.0
        x = r_m * __import__('math').cos(th)
        y = r_m * __import__('math').sin(th)
        pts.append((x, y, amp, az_deg, r_m, chan))
    return pts

#!/usr/bin/env python3
"""Raw UDP loopback test: multiple sizes, with error checking."""
import socket
import sys
import time
import threading

PORT = 15999


def receiver(size, duration=6):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    except OSError as e:
        print(f"  RCVBUF set failed: {e}", flush=True)
    actual_rcvbuf = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
    sock.bind(("127.0.0.1", PORT))
    print(f"  receiver bound, rcvbuf={actual_rcvbuf}, waiting {duration}s", flush=True)
    sock.settimeout(duration)
    count = 0
    try:
        while True:
            data, addr = sock.recvfrom(65536)
            count += 1
            print(f"  RECV #{count} len={len(data)} from {addr}", flush=True)
    except socket.timeout:
        pass
    print(f"  receiver done: {count} packets received", flush=True)


def sender(size):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
    except OSError as e:
        print(f"  SNDBUF set failed: {e}", flush=True)
    actual_sndbuf = sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
    print(f"  sender sndbuf={actual_sndbuf}", flush=True)
    payload = b"x" * size
    time.sleep(1.5)
    for i in range(5):
        try:
            n = sock.sendto(payload, ("127.0.0.1", PORT))
            print(f"  sent #{i+1} {n}B", flush=True)
        except OSError as e:
            print(f"  send #{i+1} FAILED: {e}", flush=True)
        time.sleep(0.3)


if __name__ == "__main__":
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 12000
    print(f"=== raw UDP loopback test, size={size} ===", flush=True)
    t = threading.Thread(target=receiver, args=(size,), daemon=True)
    t.start()
    sender(size)
    t.join(timeout=8)

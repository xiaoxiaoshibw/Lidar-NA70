"""SYNTHETIC pyserial loop:// control; not a Windows USB driver experiment."""
import json
import statistics
import threading
import time
from pathlib import Path

import serial


def run(mode, timeout):
    port = serial.serial_for_url("loop://", baudrate=460800, timeout=timeout)
    stop = threading.Event()

    def produce():
        deadline = time.monotonic()
        while not stop.is_set():
            deadline += .005
            port.write(b"x" * 67)
            stop.wait(max(0, deadline - time.monotonic()))

    producer = threading.Thread(target=produce)
    producer.start()
    sizes, elapsed = [], []
    start = time.monotonic()
    while time.monotonic() - start < .7:
        before = time.monotonic()
        size = 8192 if mode == "old" else min(8192, max(1, port.in_waiting))
        chunk = port.read(size)
        elapsed.append(time.monotonic() - before)
        sizes.append(len(chunk))
    stop.set()
    producer.join()
    port.close()
    return {"mode": mode, "timeout_s": timeout, "read_calls": len(sizes),
            "bytes_per_read_min": min(sizes), "bytes_per_read_median": statistics.median(sizes),
            "bytes_per_read_max": max(sizes),
            "read_duration_s_median": statistics.median(elapsed),
            "read_duration_s_max": max(elapsed)}


result = {"evidence": "SYNTHETIC", "transport": "pyserial loop:// (not Windows serial driver)",
          "pyserial_version": serial.VERSION,
          "producer": "67 bytes / 5 ms; scheduling is approximate",
          "results": [run("old", .05), run("old", None), run("available_or_one", .05)]}
target = Path(__file__).with_name("loopback_result.json")
target.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))

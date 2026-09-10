"""Full real-file decode throughput and bounded-memory measurements on Windows."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import platform
from pathlib import Path
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.decoder import scan_pcap, decode_frame, DECODER_VERSION


class Memory(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in (
        'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage',
        'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]


def memory():
    info = Memory(); info.cb = ctypes.sizeof(info)
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
    ctypes.windll.psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(info), info.cb)
    return info.WorkingSetSize


def main():
    project = Path(__file__).resolve().parents[2]
    results = []
    for name in ['1.pcap', '2.pcap']:
        path = project / 'radar_capture_data' / name
        before = memory(); start = time.perf_counter()
        scan = scan_pcap(path)
        indexing = time.perf_counter() - start
        first = decode_frame(path, scan.frames[0])
        first_display_ready = time.perf_counter() - start
        timings = []; peak = memory(); digest = hashlib.sha256(); points = 0
        for f in scan.frames:
            start = time.perf_counter()
            cloud = decode_frame(path, f)
            timings.append(time.perf_counter() - start)
            digest.update(cloud.tobytes())
            points += len(cloud)
            peak = max(peak, memory())
        results.append(dict(file=name, size=path.stat().st_size, sha256=hashlib.file_digest(path.open('rb'), 'sha256').hexdigest(),
                            valid_packets=scan.valid_packets, invalid_packets=scan.invalid_packets, frames=len(scan.frames),
                            duration_seconds=scan.duration, index_seconds=indexing, first_frame_ready_seconds=first_display_ready,
                            points_decoded=points, full_decode_seconds=sum(timings), p95_decode_ms=float(np.percentile(timings, 95)*1000),
                            mean_decode_fps=len(timings)/sum(timings), memory_before_bytes=before, peak_working_set_bytes=peak,
                            all_frames_sha256=digest.hexdigest(), partial_tail_points=scan.frames[-1]['point_count']))
    report = dict(environment=platform.platform(), python=sys.version, numpy=np.__version__, decoder=DECODER_VERSION, results=results)
    target = project / 'web/test-results/benchmark.json'; target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report, indent=2), 'utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()

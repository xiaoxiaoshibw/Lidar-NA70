"""Read-only comparison of selected original PCAP rays and viewer decimation."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'web'))
from backend import decoder

PCAP = ROOT / 'radar_capture_data/na70_sessions/20261104-170341/raw/lidar.pcap'
scan = decoder.scan_pcap(PCAP)
origin = float(scan.frames[0]['packet_capture_times'][0])
allframes = []
with PCAP.open('rb') as source:
    for index in range(1, 101):
        f = scan.frames[index]
        codes = decoder.decode_frame_codes(PCAP, f, source=source)
        points = decoder.decode_frame(PCAP, f, 1, source=source)
        idx = np.flatnonzero((codes[:, 0] != 0) & (codes[:, 0] != 65535) &
                             (codes[:, 1] != 65535) & (codes[:, 2] != 65535))
        selected = idx if len(idx) <= 1200 else idx[np.linspace(0, len(idx) - 1, 1200, dtype=np.int64)]
        ranges = np.linalg.norm(points[:, :3], axis=1)
        allframes.append(dict(index=index, start=float(f['packet_capture_times'][0]-origin),
                              end=float(f['packet_capture_times'][-1]-origin),
                              rays=len(points), eligible=len(idx),
                              eligible_within_150=int(np.sum(ranges[idx] <= 150)),
                              selected=len(selected), selected_within_150=int(np.sum(ranges[selected] <= 150)),
                              p=points[idx[ranges[idx] <= 150], :3],
                              ps=points[selected[ranges[selected] <= 150], :3]))

def summarize(count):
    frames = allframes[-count:]
    pts = np.concatenate([f['p'] for f in frames])
    samples = np.concatenate([f['ps'] for f in frames])
    ranges = np.linalg.norm(pts, axis=1)
    sumkey = lambda key: sum(f[key] for f in frames)
    edges = [0, 10, 20, 50, 100, 150]
    return {'frame_range': [frames[0]['index'], frames[-1]['index']],
            'packet_host_range_s': [frames[0]['start'], frames[-1]['end']],
            'rays': sumkey('rays'), 'eligible': sumkey('eligible'),
            'eligible_within_150': len(pts), 'selected_before_150': sumkey('selected'),
            'selected_within_150': len(samples), 'range_bins_m': edges,
            'eligible_range_bin_counts': np.histogram(ranges, edges)[0].tolist(),
            'eligible_range_quantiles_m_p10_p50_p90': np.percentile(ranges, [10, 50, 90]).tolist(),
            'eligible_z_quantiles_m_p10_p50_p90': np.percentile(pts[:, 2], [10, 50, 90]).tolist()}

result = {'pcap_sha256': hashlib.file_digest(PCAP.open('rb'), 'sha256').hexdigest(),
          'scope': 'all windows end at zero-based frame 100; display mask only',
          'max_display_range_m': 150, 'display_max_points_per_frame': 1200,
          'frames': {str(n): summarize(n) for n in (10, 50, 100)}}
print(json.dumps(result, ensure_ascii=False, indent=2))

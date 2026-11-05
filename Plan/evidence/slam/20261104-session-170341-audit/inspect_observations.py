"""Read-only diagnostics using the project's canonical PCAP scanner."""
import json
import hashlib
import platform
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / 'web'), str(ROOT / 'packages/na70_core')]
from backend.decoder import DECODER_VERSION, scan_pcap

session = ROOT / 'radar_capture_data/na70_sessions/20261104-170341'
def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

manifest = json.loads((session / 'manifest.json').read_text(encoding='utf-8'))
declared = {x['path']: x for x in manifest['files']}
inventory = []
for path in sorted(p for p in session.rglob('*') if p.is_file()):
    name = path.relative_to(session).as_posix()
    h = sha(path); record = declared.get(name)
    inventory.append({'path': name, 'bytes': path.stat().st_size, 'sha256': h,
                      'listed_in_manifest': record is not None,
                      'manifest_match': None if record is None else record['bytes'] == path.stat().st_size and record['sha256'] == h})
result_path = Path(__file__).parent
(result_path / 'inventory.json').write_text(json.dumps({'files': inventory,
    'declared_but_missing': sorted(set(declared) - {x['path'] for x in inventory}),
    'manifest_self_hash_note': 'manifest does not list itself; computed hash is recorded above'},
    indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
code_files = ['web/backend/decoder.py', 'tools/audit_na70_session.py', 'tools/audit_na70_imu.py',
              'tools/audit_na70_overlap.py', 'tools/audit_na70_imu_timebase.py',
              'tools/audit_na70_imu_ranges.py', 'tools/report_na70_session.py',
              'tools/verify_na70_gyro_unit.py', 'tools/analyze_na70_imu_run.py', 'tools/lio/static_check.yaml']
metadata = {'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'python': sys.version, 'platform': platform.platform(), 'numpy': np.__version__,
            'code_sha256': {p: sha(ROOT / p) for p in code_files},
            'script_sha256': sha(Path(__file__)),
            'comparison_pcap_sha256': sha(ROOT / 'radar_capture_data/na70_sessions/20261104-161220/raw/lidar.pcap')}
(result_path / 'provenance.json').write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
scan = scan_pcap(session / 'raw/lidar.pcap')
ts = np.array([t for f in scan.frames for t in f['packet_capture_times']])
dev = [t for f in scan.frames for t in f['packet_device_times']]
d = np.diff(ts)
bins = np.floor(ts - ts[0]).astype(int)
counts = np.bincount(bins)
chunks = [json.loads(line) for line in (session / 'raw/imu_chunks.jsonl').read_text(encoding='utf-8').splitlines()]
frames = [json.loads(line) for line in (session / 'imu_frames.jsonl').read_text(encoding='utf-8').splitlines()]
gyro = np.array([f['gyro'] for f in frames]); accel = np.array([f['accel'] for f in frames])
times = np.array([f['t_unix'] for f in frames]); euler = np.array([f['euler'] for f in frames])
windows = []
for i in range(10):
    m = (times - times[0] >= i * 10) & (times - times[0] < (i + 1) * 10)
    if m.any():
        windows.append({'host_start_offset_s': i * 10, 'frames': int(m.sum()),
                        'gyro_rms_recorded': np.sqrt(np.mean(gyro[m] ** 2, axis=0)).tolist(),
                        'gyro_mean_recorded': np.mean(gyro[m], axis=0).tolist(),
                        'accel_mean_recorded': np.mean(accel[m], axis=0).tolist(),
                        'euler_min_recorded': np.min(euler[m], axis=0).tolist(),
                        'euler_max_recorded': np.max(euler[m], axis=0).tolist()})
valid_dev = np.array([x for x in dev if x is not None])
device_dt_ms = np.rint(np.diff(valid_dev) * 1000).astype(int)
comparison_path = ROOT / 'radar_capture_data/na70_sessions/20261104-161220/raw/lidar.pcap'
comparison = scan_pcap(comparison_path)
ct = np.array([t for f in comparison.frames for t in f['packet_capture_times']])
cd = np.array([t for f in comparison.frames for t in f['packet_device_times'] if t is not None])
result = {
    'decoder_version': DECODER_VERSION,
    'lidar': {'valid_packets': scan.valid_packets, 'invalid_packets': scan.invalid_packets,
              'ignored_packets': scan.ignored_packets, 'warnings': scan.warnings,
              'frame_count': len(scan.frames), 'complete_100_packet_bundles': sum(f['complete'] for f in scan.frames),
              'incomplete_bundles': [{'index': f['index'], 'packet_count': f['packet_count']} for f in scan.frames if not f['complete']],
              'host_span_s': float(ts[-1] - ts[0]), 'mean_packet_rate_endpoint_hz': float((len(ts)-1)/(ts[-1]-ts[0])),
              'packet_counts_per_full_host_second': counts[:-1].tolist(), 'final_partial_second_packets': int(counts[-1]),
              'gap_quantiles_s_p50_p90_p95_p99_max': np.quantile(d, [.5,.9,.95,.99,1]).tolist(),
              'gaps_over_5ms': int((d > .005).sum()), 'gaps_over_10ms': int((d > .01).sum()),
              'zero_host_gaps': int((d == 0).sum()), 'backwards_host_gaps': int((d < 0).sum()),
              'device_tail_decoded_count': len(valid_dev), 'device_tail_null_count': len(dev)-len(valid_dev),
              'device_tail_first_values': valid_dev[:10].tolist(), 'device_tail_last_values': valid_dev[-10:].tolist(),
              'device_tail_unique_values': len(set(valid_dev.tolist())),
              'device_tail_backwards': int((np.diff(valid_dev) < 0).sum()),
              'device_tail_delta_rounded_milliseconds': dict(sorted(Counter(str(v) for v in device_dt_ms).items(), key=lambda x: int(x[0]))),
              'device_tail_interpretation': 'decoder-parsed tail only; semantics, epoch, clock mapping and packet measurement time are unverified'},
    'comparison_20261104_161220': {'source': str(comparison_path.relative_to(ROOT)),
         'valid_packets': comparison.valid_packets, 'invalid_packets': comparison.invalid_packets,
         'host_span_s': float(ct[-1]-ct[0]), 'mean_packet_rate_endpoint_hz': float((len(ct)-1)/(ct[-1]-ct[0])),
         'device_tail_first_s': float(cd[0]), 'device_tail_last_s': float(cd[-1]),
         'device_tail_delta_rounded_milliseconds': dict(sorted(Counter(str(v) for v in np.rint(np.diff(cd)*1000).astype(int)).items(), key=lambda x: int(x[0]))),
         'limits': 'short engineering session; unknown device state/traffic/load; host observations do not identify cause'},
    'imu': {'chunk_size_counts': dict(Counter(str(c['bytes']) for c in chunks)), 'window_statistics': windows,
            'limitations': 'RMS includes bias, vibration and motion; cannot infer translation or motion type or certified physical units from these statistics.'},
    'limits': ['100-packet bundles are decoder groupings, not proof of complete device scans or no missing UDP datagrams',
               'packet counts/host gaps alone cannot establish device transmit rate or packet loss',
               'IMU host receive timestamps are batched and not device sampling times']}
(Path(__file__).parent / 'observations.json').write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')

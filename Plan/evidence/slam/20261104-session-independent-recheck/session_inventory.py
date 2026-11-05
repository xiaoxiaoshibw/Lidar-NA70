"""List session directories and summarize recorded IMU chunks without editing them."""
from collections import Counter
import json
from pathlib import Path
import platform
import statistics
import sys
import serial
from serial.tools import list_ports

root = Path(__file__).resolve().parents[4]
base = root / 'radar_capture_data/na70_sessions'
chunks_path = base / '20261104-170341/raw/imu_chunks.jsonl'
chunks = [json.loads(line) for line in chunks_path.read_text(encoding='utf-8').splitlines()]
gaps = [b['t_unix'] - a['t_unix'] for a, b in zip(chunks, chunks[1:])]
data = {
    'platform': platform.platform(), 'python': sys.version, 'pyserial': serial.__version__,
    'serial_ports': [port.device for port in list_ports.comports()],
    'session_directories': sorted(p.name for p in base.iterdir() if p.is_dir()),
    'chunk_count': len(chunks), 'chunk_byte_counts': dict(Counter(c['bytes'] for c in chunks)),
    'unix_gap_median_s': statistics.median(gaps), 'unix_gap_max_s': max(gaps),
    'note': 'Recorded host read-completion times and sizes; they do not bound sample age or prove synchronization.',
}
print(json.dumps(data, ensure_ascii=False, indent=2))

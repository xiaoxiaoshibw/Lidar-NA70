"""Read-only repeat audit of an existing NA70 capture; outputs only beside script."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

root = Path(__file__).resolve().parents[4]
out = Path(__file__).resolve().parent
session = Path('radar_capture_data/na70_sessions/20261104-170341')
commands = {
    'integrity': ['tools/audit_na70_session.py', str(session)],
    'imu-replay': ['tools/audit_na70_imu.py', str(session)],
    'overlap': ['tools/audit_na70_overlap.py', str(session / 'raw/lidar.pcap'), str(session / 'raw/imu_chunks.jsonl')],
    'timebase': ['tools/audit_na70_imu_timebase.py', '--frames', str(session / 'imu_frames.jsonl'), '--chunks', str(session / 'raw/imu_chunks.jsonl'), '--output', str(out / 'timebase.json')],
}
env = {**os.environ, 'PYTHONUTF8': '1'}
results = {}
for name, args in commands.items():
    result = subprocess.run([sys.executable, *args], cwd=root, env=env, capture_output=True, text=True, encoding='utf-8')
    (out / f'{name}.stdout.txt').write_text(result.stdout, encoding='utf-8')
    (out / f'{name}.stderr.txt').write_text(result.stderr, encoding='utf-8')
    if name != 'timebase' and result.stdout.strip():
        json.loads(result.stdout)
        (out / f'{name}.json').write_text(result.stdout, encoding='utf-8')
    results[name] = {'argv': [sys.executable, *args], 'exit_code': result.returncode}
paths = list((root / session).rglob('*')) + [root / a[0] for a in commands.values()]
paths += [root / 'packages/na70_core/na70_core/capture/yesense.py', root / 'packages/na70_core/na70_core/capture/imu.py', root / 'web/backend/decoder.py', Path(__file__).resolve()]
inventory = []
for path in paths:
    if path.is_file():
        digest = hashlib.sha256()
        with path.open('rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(block)
        inventory.append({'path': str(path.relative_to(root)), 'bytes': path.stat().st_size, 'sha256': digest.hexdigest()})
(out / 'provenance.json').write_text(json.dumps({'python': sys.version, 'executable': sys.executable, 'commands': results, 'inventory': inventory}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(results, ensure_ascii=False, indent=2))

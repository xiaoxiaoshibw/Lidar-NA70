"""Compare Windows NumPy decoder against saved actual WSL ROS2 PointCloud2."""
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.decoder import scan_pcap, decode_frame

root = Path(__file__).resolve().parents[2]
out = root / 'web/test-results'
results = []
for name in ['1.pcap', '2.pcap']:
    actual = np.load(out / (name + '.ros2-first12.npy'))
    path = root / 'radar_capture_data' / name
    scan = scan_pcap(path)
    worst = 0
    for i, cloud in enumerate(actual):
        expected = decode_frame(path, scan.frames[i])
        assert expected.shape == cloud.shape
        np.testing.assert_array_equal(expected[:, 3], cloud[:, 3])
        error = float(np.abs(expected[:, :3] - cloud[:, :3]).max())
        assert error <= .001, (name, i, error)
        worst = max(worst, error)
    results.append(dict(file=name, frames=len(actual), points=int(actual.shape[0] * actual.shape[1]), max_coordinate_error_m=worst))
report = dict(environment='Windows decoder versus actual WSL2_HUMBLE_X86_64 PointCloud2', numpy=np.__version__, results=results)
(out / 'windows_ros2_comparison.json').write_text(json.dumps(report, indent=2), 'utf-8')
print(json.dumps(report, indent=2))

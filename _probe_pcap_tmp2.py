# -*- coding: utf-8 -*-
"""探针2：前10帧叠加后远距离点的水平分布，寻找球门候选簇（临时脚本）。"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent / "web"))
from backend import decoder  # noqa: E402

pcap = Path(sys.argv[1])
scan = decoder.scan_pcap(pcap)
src = pcap.open("rb")
far = []
for i in range(10):
    pts = decoder.decode_frame(pcap, scan.frames[i], source=src)
    d = np.linalg.norm(pts[:, :3], axis=1)
    m = (d >= 80) & (d < 150)
    far.append(pts[m][:, :3])
src.close()
far = np.vstack(far)
print(f"far points total={len(far)}")
# 水平面网格直方图（2m 格）
x, y = far[:, 0], far[:, 1]
H, xe, ye = np.histogram2d(x, y, bins=[np.arange(60, 160, 2), np.arange(-40, 40, 2)])
top = np.argwhere(H >= 15)
print("dense cells (x_bin_center, y_bin_center, count), count>=15:")
rows = sorted(((xe[i] + 1, ye[j] + 1, int(H[i, j])) for i, j in top), key=lambda r: -r[2])
for r in rows[:40]:
    print(f"  x={r[0]:.0f} y={r[1]:.0f} n={r[2]}")
# z 分布
z = far[:, 2]
print(f"z percentiles: 5={np.percentile(z,5):.1f} 50={np.percentile(z,50):.1f} 95={np.percentile(z,95):.1f}")
# 按方位角看
az = np.degrees(np.arctan2(y, x))
H2, xe2 = np.histogram(az, bins=np.arange(-45, 46, 2))
strong = [(xe2[i] + 1, int(H2[i])) for i in range(len(H2)) if H2[i] >= 50]
print("azimuth bins (deg) with >=50 far pts:", strong)

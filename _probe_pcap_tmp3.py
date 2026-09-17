# -*- coding: utf-8 -*-
"""探针3：x≈115m 结构的几何形态——立柱/横梁（临时脚本）。"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent / "web"))
from backend import decoder  # noqa: E402

pcap = Path(sys.argv[1])
scan = decoder.scan_pcap(pcap)
src = pcap.open("rb")
sel = []
for i in range(10):
    pts = decoder.decode_frame(pcap, scan.frames[i], source=src)
    x, y = pts[:, 0], pts[:, 1]
    m = (x >= 100) & (x <= 130) & (np.abs(y) <= 15)
    sel.append(pts[m][:, :3])
src.close()
sel = np.vstack(sel)
print(f"points near 100-130m front: {len(sel)}")
# 沿 y 方向 1m 分 bin，看每个 bin 的 z 范围与点数
for ylo in range(-15, 15):
    m = (sel[:, 1] >= ylo) & (sel[:, 1] < ylo + 1)
    n = m.sum()
    if n >= 10:
        z = sel[m, 2]
        x = sel[m, 0]
        print(f"y=[{ylo:>3},{ylo+1:>3}) n={n:>4} x_p10={np.percentile(x,10):.0f} x_p90={np.percentile(x,90):.0f} "
              f"z_p05={np.percentile(z,5):.1f} z_p50={np.percentile(z,50):.1f} z_p95={np.percentile(z,95):.1f}")
# 顶部结构：z 高处的点沿 y 分布
m = sel[:, 2] > 3.0
print(f"\npts z>3m: {m.sum()}")
if m.sum():
    yy = sel[m][:, 1]
    print("  y histogram 1m bins:", {int(b): int(c) for b, c in
          zip(np.arange(-15, 15), np.histogram(yy, bins=np.arange(-15, 16))[0]) if c})
# 底部
m = (sel[:, 2] > -0.5) & (sel[:, 2] < 1.0)
print(f"pts z in [-0.5,1.0): {m.sum()}")
if m.sum():
    yy = sel[m][:, 1]
    print("  y histogram 1m bins:", {int(b): int(c) for b, c in
          zip(np.arange(-15, 15), np.histogram(yy, bins=np.arange(-15, 16))[0]) if c})

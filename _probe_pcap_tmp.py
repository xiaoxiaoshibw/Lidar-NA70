# -*- coding: utf-8 -*-
"""探针：检查操场 PCAP 的帧数、点数、距离分布与远距离结构（临时脚本，用后即删）。"""
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent / "web"))
from backend import decoder  # noqa: E402

pcap = Path(sys.argv[1])
scan = decoder.scan_pcap(pcap)
frames = scan.frames
print(f"frames={len(frames)} duration={getattr(scan, 'duration', '?')}")

src = pcap.open("rb")
for idx in [0, len(frames) // 4, len(frames) // 2, 3 * len(frames) // 4, len(frames) - 1]:
    f = frames[idx]
    pts = decoder.decode_frame(pcap, f, source=src)
    d = np.linalg.norm(pts[:, :3], axis=1)
    valid = d[d > 0.5]
    print(f"\nframe {idx}: n={len(pts)} valid={len(valid)} "
          f"time={f.get('time')} pitch_off={f.get('pitch_offset')} yaw_off={f.get('yaw_offset')}")
    if len(valid):
        print(f"  range: min={valid.min():.1f} p50={np.percentile(valid, 50):.1f} "
              f"p95={np.percentile(valid, 95):.1f} max={valid.max():.1f}")
        for lo, hi in [(0, 20), (20, 50), (50, 80), (80, 100), (100, 120), (120, 200), (200, 1000)]:
            m = ((d >= lo) & (d < hi)).sum()
            if m:
                print(f"  [{lo:>3},{hi:>4}): {m}")
        far = pts[(d >= 80) & (d < 150)][:, :3]
        if len(far):
            print(f"  far(80-150m) x:[{far[:,0].min():.0f},{far[:,0].max():.0f}] "
                  f"y:[{far[:,1].min():.0f},{far[:,1].max():.0f}] z:[{far[:,2].min():.1f},{far[:,2].max():.1f}]")
            # 10 帧叠加看看远距离点数
acc10 = []
for i in range(10):
    pts = decoder.decode_frame(pcap, frames[i], source=src)
    d = np.linalg.norm(pts[:, :3], axis=1)
    acc10.append(((d >= 80) & (d < 150)).sum())
print(f"\nfirst 10 frames far(80-150m) counts: {acc10} total={sum(acc10)}")
src.close()

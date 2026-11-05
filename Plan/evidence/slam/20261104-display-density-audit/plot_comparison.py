"""Same 10 frames: display candidates vs viewer subset, no pose compensation."""
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'web'))
from backend import decoder

PCAP = ROOT / 'radar_capture_data/na70_sessions/20261104-170341/raw/lidar.pcap'
scan = decoder.scan_pcap(PCAP)
full, subset = [], []
with PCAP.open('rb') as source:
    for index in range(91, 101):
        frame = scan.frames[index]
        codes = decoder.decode_frame_codes(PCAP, frame, source=source)
        pts = decoder.decode_frame(PCAP, frame, 1, source=source)
        valid = np.flatnonzero((codes[:, 0] != 0) & (codes[:, 0] != 65535) &
                               (codes[:, 1] != 65535) & (codes[:, 2] != 65535))
        chosen = valid if len(valid) <= 1200 else valid[np.linspace(0, len(valid)-1, 1200, dtype=np.int64)]
        full.append(pts[valid, :3])
        subset.append(pts[chosen, :3])
full, subset = np.concatenate(full), np.concatenate(subset)
font = FontProperties(fname='C:/Windows/Fonts/msyh.ttc')
fig, axes = plt.subplots(2, 2, figsize=(11, 10), constrained_layout=True)
for row, radius in enumerate((150, 50)):
    for col, (values, title) in enumerate(((full, '全部候选点'), (subset, '回放页面抽样'))):
        ax = axes[row, col]
        points = values[np.linalg.norm(values, axis=1) <= radius]
        ax.scatter(points[:, 0], points[:, 1], s=0.6, alpha=.28, color='#2370a8', linewidths=0, rasterized=True)
        ax.scatter([0], [0], c='#cf3c33', s=16, marker='+')
        ax.set(xlim=(-radius, radius), ylim=(-radius, radius), aspect='equal',
               xlabel='X (m)', ylabel='Y (m)')
        ax.set_title(f'{title}  |  显示半径 {radius} m  |  {len(points):,} 点', fontproperties=font, fontsize=11)
        ax.grid(alpha=.15)
fig.suptitle('雷达帧 91–100：俯视投影（未做运动补偿）\n用户说明前 80 秒手持举起有抖动，直接堆叠可能重影；红色十字为雷达坐标原点',
             fontproperties=font, fontsize=13)
fig.savefig(Path(__file__).with_name('stack-comparison.png'), dpi=150)

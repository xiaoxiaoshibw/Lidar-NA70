"""G0/G1 REAL_PCAP windows: exact time, raw references and height ablation.

No output of this tool is automatically promoted to reviewed goal truth.
Moving windows may be inspected without claiming a static ground reference.
"""
import argparse
import hashlib
import json
import platform
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))
from backend import decoder
from backend.goal_detection.config import load_configuration, configuration_version
from backend.goal_detection.pipeline import _estimate_ground_level, _apply_ground_level
from audit_goal_failure import probe
from run_goal_demo_offline import sha256_file, source_hashes


def summarize(points):
    return {'points': len(points), 'z_span': float(np.ptp(points[:, 2])) if len(points) else None,
            'z_min': float(points[:, 2].min()) if len(points) else None,
            'z_max': float(points[:, 2].max()) if len(points) else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pcap', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--confirmed-static-until', type=float,
                        help='Operator-confirmed seconds from first device timestamp')
    parser.add_argument('--motion-status', choices=('unknown', 'moving'), default='unknown',
                        help='Status outside the optional confirmed-static interval')
    parser.add_argument('--windows', help='Optional start:end pairs, comma separated')
    parser.add_argument('--roi', help='Optional diagnostic xmin,xmax,ymin,ymax,zmin,zmax')
    args = parser.parse_args()
    if args.confirmed_static_until is None and not args.windows:
        parser.error('--windows is required without --confirmed-static-until')
    if args.confirmed_static_until is not None and args.confirmed_static_until < 0:
        parser.error('--confirmed-static-until must be nonnegative')
    args.output.mkdir(parents=True, exist_ok=False)
    cfg = load_configuration()
    cfg['accumulation']['roi'] = cfg['webui']['demo_roi']
    if args.roi:
        cfg['accumulation']['roi'] = [float(v) for v in args.roi.split(',')]
    scan = decoder.scan_pcap(args.pcap)
    sha = sha256_file(args.pcap)
    start = scan.frames[0]['device_start_time']
    static = ([f for f in scan.frames if f['device_time'] is not None and
               f['device_start_time'] is not None and
               start <= f['device_start_time'] <= f['device_time'] <= start + args.confirmed_static_until]
              if args.confirmed_static_until is not None else [])
    if args.confirmed_static_until is not None and not static:
        raise ValueError('No complete device-time frames in confirmed interval')
    windows = ([(0, static[-1]['index']), (64, 83), (308, 327), (134, 153), (578, 597)]
               if static else [])
    if args.windows:
        windows = [tuple(int(v) for v in item.split(':')) for item in args.windows.split(',')]
    arrays, refs, counts = {}, {}, {}
    bounds = np.asarray(cfg['accumulation']['roi']).reshape(3, 2)
    with args.pcap.open('rb') as stream:
        for frame in scan.frames[:max(b for _, b in windows)+1]:
            p = decoder.decode_frame(args.pcap, frame, source=stream)
            finite = np.isfinite(p[:, :3]).all(axis=1)
            distance = np.linalg.norm(p[:, :3], axis=1)
            ranged = finite & (distance >= cfg['accumulation']['range']['min']) & (distance <= cfg['accumulation']['range']['max'])
            keep = ranged & np.all((p[:, :3] >= bounds[:, 0]) & (p[:, :3] <= bounds[:, 1]), axis=1)
            i = frame['index']
            arrays[i] = p[keep]
            refs[i] = np.column_stack((np.full(keep.sum(), i), np.flatnonzero(keep)))
            counts[i] = [len(p), int(ranged.sum())]
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    rows = []
    for lo, hi in windows:
        points = np.vstack([arrays[i] for i in range(lo, hi+1)])
        references = np.vstack([refs[i] for i in range(lo, hi+1)])
        t0, t1 = scan.frames[lo]['device_start_time'], scan.frames[hi]['device_time']
        motion = ('confirmed_static' if args.confirmed_static_until is not None
                  and t1 <= start + args.confirmed_static_until else args.motion_status)
        context = {'source_age_seconds': t1-t0, 'source_frame_range': [lo, hi], 'motion_status': motion}
        level = _estimate_ground_level(points[:0], points, cfg, cfg['goal_detection']['z_leveling'], source_context=context)
        normalized = _apply_ground_level(points, level)
        absolute_keep = points[:, 2] > cfg['goal_detection']['ground_z'][1]
        relative_keep = normalized[:, 2] > cfg['goal_detection']['ground_z'][1]
        cloud_id = 'raw-window:' + hashlib.sha256(sha.encode()+references.tobytes()).hexdigest()[:24]
        record = {'frame_range': [lo, hi], 'device_time_range': [t0, t1], 'duration_seconds': t1-t0,
                  'cloud_id': cloud_id, 'coordinate_frame': 'lidar_link', 'motion_status': motion,
                  'motion_source': ('CLI --confirmed-static-until' if motion == 'confirmed_static'
                                    else 'CLI --motion-status' if motion == 'moving' else 'unknown'),
                  'review_status': 'PENDING_REVIEW', 'observation_unit': 'accumulated_window',
                  'raw_points': sum(counts[i][0] for i in range(lo, hi+1)),
                  'range_points': sum(counts[i][1] for i in range(lo, hi+1)),
                  'roi_points': len(points), 'ground_model': level,
                  'absolute_filtered': summarize(points[absolute_keep]),
                  'relative_filtered': summarize(normalized[relative_keep]) if level['enabled'] else None,
                  'components': {},
                  'geometry_after': probe(normalized, cfg, hi-lo+1) if level['enabled'] else None}
        # Diagnostic neighborhoods around the ROI centre, NOT fitted goalposts.
        center_x = float(bounds[0].mean())
        center_y = float(bounds[1].mean())
        for name, y in [('edge_a', center_y - 2.5), ('edge_b', center_y + 2.5)]:
            mask = (points[:, 0] >= center_x - 1.5) & (points[:, 0] <= center_x + 1.5) \
                & (np.abs(points[:, 1] - y) <= .4)
            record['components'][name] = {'raw': summarize(points[mask]),
                'absolute': summarize(points[mask & absolute_keep]),
                'relative': summarize(normalized[mask & relative_keep]) if level['enabled'] else None,
                'raw_refs': references[mask].tolist(), 'role': 'diagnostic_neighborhood_not_truth'}
        np.savez_compressed(args.output / f'raw-{lo}-{hi}.npz', points=points, raw_refs=references,
                            normalized=normalized, absolute_keep=absolute_keep, relative_keep=relative_keep)
        fig, axes = plt.subplots(1, 3, figsize=(16, 5), constrained_layout=True)
        time_scatter = None
        for ax, (a, b, title) in zip(axes, [(1, 2, 'Front (Y/Z)'), (0, 2, 'Side (X/Z)'), (1, 0, 'Top (Y/X)')]):
            if motion == 'moving':
                time_scatter = ax.scatter(points[:, a], points[:, b], s=3,
                                          c=references[:, 0], cmap='viridis',
                                          vmin=lo, vmax=max(hi, lo + 1))
            else:
                ax.scatter(points[:, a], points[:, b], s=1, c='#999999', label='Raw ROI')
                ax.scatter(points[absolute_keep, a], points[absolute_keep, b], s=1, c='#e07040', label='Absolute z > 0.3')
                if level['enabled']:
                    ax.scatter(points[relative_keep, a], points[relative_keep, b], s=1, c='#006e90', label='Relative height > 0.3')
            ax.set_title(title)
            ax.set_xlabel(('X','Y','Z')[a]+' (m)'); ax.set_ylabel(('X','Y','Z')[b]+' (m)')
            ax.set_aspect('equal'); ax.grid(alpha=.2)
        if time_scatter is not None:
            fig.colorbar(time_scatter, ax=axes, label='source frame')
        else:
            axes[0].legend(markerscale=4, fontsize=8)
        fig.suptitle(f'REAL_PCAP frames {lo}-{hi} / {t1-t0:.3f}s / {motion}\nGround: {level.get("reason") or "geometric plane accepted"}; goal truth PENDING_REVIEW')
        fig.savefig(args.output/f'views-{lo}-{hi}.png', dpi=150); plt.close(fig)
        rows.append(record)
    manifest = {'input_sha256': sha, 'source_hashes': source_hashes(),
                'tool_sha256': sha256_file(Path(__file__)), 'command': sys.argv,
                'python': platform.python_version(), 'numpy': np.__version__,
                'config': cfg, 'config_version': configuration_version(cfg),
                'annotation_version': 'diagnostic-window-v2', 'windows': rows}
    (args.output/'audit.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps([{k:r[k] for k in ('frame_range','duration_seconds','roi_points','ground_model','absolute_filtered','relative_filtered')} for r in rows], ensure_ascii=False))


if __name__ == '__main__':
    main()

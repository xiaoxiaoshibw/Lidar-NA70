"""Experimental KISS-ICP adapter; not a production mapping implementation."""
import argparse
import importlib.metadata
import json
import sys
import time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
from backend.decoder import decode_frame
from kiss_icp.config import load_config
from kiss_icp.kiss_icp import KissICP


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--bootstrap', type=int, default=0)
    parser.add_argument('--voxel', type=float)
    parser.add_argument('--max-range', type=float)
    parser.add_argument('--extended-static-reference', action='store_true',
                        help='Diagnostic only: permit up to 300 stationary frames, leaving 30-60s held out')
    args = parser.parse_args()
    frames = json.loads((args.capture/'frames.json').read_text())
    if args.limit:
        frames = frames[:args.limit]
    cfg = load_config(None)
    cfg.data.deskew = False
    cfg.registration.max_num_threads = 4
    if args.voxel is not None:
        cfg.mapping.voxel_size = args.voxel
    if args.max_range is not None:
        cfg.data.max_range = args.max_range
    odometry = KissICP(cfg)
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    started = time.perf_counter()
    for i, frame in enumerate(frames):
        points = decode_frame(args.capture/'capture.pcap', frame)[:, :3].astype(np.float64)
        points = points[np.isfinite(points).all(axis=1) & (np.linalg.norm(points, axis=1) > 0)]
        if i < args.bootstrap:
            if frame['time'] >= 30 or args.bootstrap > (300 if args.extended_static_reference else 30):
                raise ValueError('Bootstrap exceeds confirmed interval or existing 30-frame budget')
            prepared = odometry.preprocessor.preprocess(points, np.empty(0), np.eye(4))
            _, downsampled = odometry.voxelize(prepared)
            odometry.local_map.update(downsampled, np.eye(4))
        else:
            odometry.register_frame(points, np.empty(0))
        pose = odometry.last_pose.copy()
        rotation = float(np.degrees(np.arccos(np.clip((np.trace(pose[:3,:3])-1)/2,-1,1))))
        rows.append(dict(frame=i, time=frame['time'], pose=pose.tolist(), displacement=float(np.linalg.norm(pose[:3,3])), rotation_deg=rotation))
        if i % 100 == 0:
            print(i, round(rows[-1]['displacement'],3), round(rotation,3), flush=True)
    static = [r for r in rows if r['time'] < 60]
    result = dict(algorithm='KISS-ICP',version=importlib.metadata.version('kiss-icp'),config=cfg.model_dump(),
                  data_source='REAL',imu_used=False,deskew=False,loop_closure=False,
                  stationary_declaration='User confirmed first 60 seconds stationary',
                  bootstrap_frames=args.bootstrap,
                  elapsed_seconds=time.perf_counter()-started,
                  static_max_displacement=max(r['displacement'] for r in static),
                  static_max_rotation=max(r['rotation_deg'] for r in static),
                  final_displacement=rows[-1]['displacement'],
                  geometric_acceptance='NOT_EVALUATED',
                  note='Outputs are ungated candidate poses; pose count is not tracking success.')
    (args.output/'metrics.json').write_text(json.dumps(result,indent=2))
    (args.output/'trajectory.json').write_text(json.dumps(rows))
    np.save(args.output/'local_map.npy',odometry.local_map.point_cloud())
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()

"""Read-only measured empty-scene reconstruction; no production pipeline changes."""
import hashlib
import json
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent
SOURCE = Path('D:/Code/ldiar/captures/remote/cap_20261010_175227')
CONTRACT = Path('D:/Code/ldiar/sidequests/human_fusion_lab/core/leveling.js')
R = np.array([[.893544, 0, .448975], [-.014337, .99949, .028533],
              [-.448746, -.031932, .893089]])
T = np.array([0, 0, 1.380286])
CONFIG = {'voxel_m': .05, 'min_range_m': .2, 'max_range_m': 15,
          'min_frame_fraction': .5, 'viewer_max_points': 30000}


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    meta = json.loads((SOURCE / 'meta.json').read_text(encoding='utf-8'))
    assert len(meta['frames']) == 304
    assert abs(meta['duration_sec'] - 31.417239) < .001
    assert sha(SOURCE / 'meta.json') == '2f3139a4cd9aa2ef665a26bfe157224e3fe93ffc3db60dcc6be8275d39661610'
    assert sha(SOURCE / 'points.bin') == '7ee30e01878205f37463d79d95983321780f6c66a14d2a1ac7c4a5d48e61a5d8'
    assert not meta.get('leveling'), 'Avoid applying leveling twice'
    assert meta['point_stride_bytes'] == 28
    assert (SOURCE / 'points.bin').stat().st_size == meta['total_points'] * 28
    raw = np.memmap(SOURCE / 'points.bin', dtype='u1', mode='r')
    xyz = np.ndarray((meta['total_points'], 3), dtype='<f4', buffer=raw, strides=(28, 4))
    # Display fusion only: source files retain all fields and point correspondence.
    cells = {}
    valid_count = 0
    for fi, frame in enumerate(meta['frames']):
        start, count = frame['offset_points'], frame['count_points']
        assert 0 <= start <= start + count <= len(xyz)
        p = xyz[start:start + count].astype('f8')
        ranges = np.linalg.norm(p, axis=1)
        keep = np.isfinite(p).all(axis=1) & (ranges > CONFIG['min_range_m']) & (ranges < CONFIG['max_range_m'])
        ids = np.flatnonzero(keep) + start
        ranges, p = ranges[keep], p[keep] @ R.T + T
        valid_count += len(p)
        keys, inv = np.unique(np.floor(p / CONFIG['voxel_m']).astype('i4'), axis=0, return_inverse=True)
        counts = np.bincount(inv)
        sums = np.column_stack([np.bincount(inv, weights=p[:, a]) for a in range(3)])
        range_sums = np.bincount(inv, weights=ranges)
        first = np.full(len(keys), len(ids), dtype='i8')
        np.minimum.at(first, inv, np.arange(len(ids)))
        for i, key in enumerate(keys):
            k = tuple(key)
            if k not in cells:
                cells[k] = [np.zeros(3), 0, 0, 0., fi, int(ids[first[i]])]
            row = cells[k]
            row[0] += sums[i]
            row[1] += int(counts[i])
            row[2] += 1
            row[3] += range_sums[i]
    rows = [cells[k] for k in sorted(cells)]
    points = np.array([r[0] / r[1] for r in rows])
    hits = np.array([r[2] for r in rows])
    stable = hits >= np.ceil(len(meta['frames']) * CONFIG['min_frame_fraction'])
    ranges = np.array([r[3] / r[1] for r in rows])
    np.savez_compressed(OUT / 'scene.npz', xyz_leveled=points, mean_raw_range_m=ranges,
                        frame_hits=hits, stable=stable, return_count=[r[1] for r in rows],
                        representative_frame=[r[4] for r in rows], representative_source_index=[r[5] for r in rows], R=R, T=T)
    colors = np.where(stable[:, None], [70, 195, 225], [125, 125, 135])
    with (OUT / 'scene.ply').open('w', encoding='ascii') as f:
        f.write('ply\nformat ascii 1.0\ncomment observed_only leveled metres\n')
        f.write(f'element vertex {len(points)}\nproperty float x\nproperty float y\nproperty float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n')
        for p, c in zip(points, colors):
            f.write(' '.join(map(str, [*np.round(p, 5), *c])) + '\n')
    chosen = np.linspace(0, len(points)-1, min(len(points), CONFIG['viewer_max_points']), dtype=int)
    display = [[*np.round(points[i], 3), int(stable[i]), round(float(ranges[i]), 3), int(hits[i])] for i in chosen]
    payload = {'points': display, 'sensor': T.tolist(), 'frames': len(meta['frames'])}
    template = (OUT / 'viewer_template.html').read_text(encoding='utf-8')
    (OUT / 'viewer.html').write_text(template.replace('__SCENE__', json.dumps(payload, separators=(',', ':'))), encoding='utf-8')
    near = points[(points[:, 0] > .5) & (points[:, 0] < 5) & (np.abs(points[:, 2]) < .15)]
    report = {'status': 'DEVELOPMENT / OBSERVED_ONLY', 'scene_identity_with_photo': 'UNVERIFIED',
              'source': str(SOURCE), 'activity': 'S1-empty-with-furniture', 'duration_sec': meta['duration_sec'], 'sensor': meta['sensor'], 'frames': len(meta['frames']),
              'raw_points': len(xyz), 'valid_returns_in_range': valid_count, 'voxel_points': len(points),
              'persistent_voxels': int(stable.sum()), 'config': CONFIG,
              'coordinate_contract': {'source': str(CONTRACT), 'R': R.tolist(), 'T': T.tolist(),
               'ground_physical_accuracy': 'NOT_EVALUATED', 'range': 'mean Euclidean raw XYZ range per voxel; metres',
               'note': 'No semantic labels; persistence is not proof of background identity; no unseen surfaces filled'},
              'ground_diagnostic': {'near_zero_voxels': len(near), 'z_median_m': float(np.median(near[:, 2])),
                                    'z_p05_p95_m': np.quantile(near[:, 2], [.05, .95]).tolist()},
              'source_sha256': {n: sha(SOURCE / n) for n in ['meta.json', 'points.bin']},
              'contract_sha256': sha(CONTRACT)}
    # The frozen audit identifies this exact empty recording. Fail on source drift.
    assert report['source_sha256']['points.bin'] == '7ee30e01878205f37463d79d95983321780f6c66a14d2a1ac7c4a5d48e61a5d8'
    assert np.isfinite(points).all() and len(points) > 0
    assert np.all(hits <= len(meta['frames']))
    assert np.max(np.abs(np.linalg.norm((points-T) @ np.linalg.inv(R.T), axis=1) - ranges)) < .1
    (OUT / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ['frames', 'raw_points', 'voxel_points', 'persistent_voxels', 'ground_diagnostic']}))


if __name__ == '__main__':
    main()

"""REAL PCAP diagnostic using a user-declared stationary interval; no fusion claims."""
import json
import sys
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
from backend.mapping import configuration, preprocess, downsample, register, translation_m, rotation_deg
from backend.decoder import decode_frame

root = Path(__file__).resolve().parents[1]
folder = root / 'web/data/defcea4d-51db-4f7f-b226-c5ffac4805df'
frames = json.loads((folder / 'frames.json').read_text())
cfg = configuration()
output = []
for start, end, probes in [(0, 29, [30, 34, 35, 36, 50, 100]), (570, 599, [600, 601, 605, 610, 620])]:
    clouds = [preprocess(decode_frame(folder / 'capture.pcap', frames[i]), cfg) for i in range(start, end+1)]
    target, _ = downsample(np.concatenate([p[0] for p in clouds]), np.concatenate([p[1] for p in clouds]),
                          cfg['submap_voxel_size'], cfg['submap_max_points'])
    tree = cKDTree(target)
    for i in probes:
        source, _ = preprocess(decode_frame(folder / 'capture.pcap', frames[i]), cfg)
        d = tree.query(source)[0]; valid = d < cfg['correspondence_distance']
        pose, q = register(source, target, np.eye(4), cfg, tree=tree)
        row = dict(window=[start,end], frame=i, time=frames[i]['time'], identity_overlap=float(valid.mean()),
                   identity_rmse=float(np.sqrt(np.mean(d[valid]**2))), quality=q,
                   stationary_truth_applicable=frames[i]['time'] < 60)
        output.append(row)
        print(json.dumps({k:v for k,v in row.items() if k!='quality'}), q['reason'], flush=True)
(root/'Plan/evidence/mapping-repair/20260911-repair/static-probe.json').write_text(json.dumps(output,indent=2))

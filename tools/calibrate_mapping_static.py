"""REAL static calibration; fit on 3-30s and hold out 30-60s, no moving-data fit."""
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
clouds = [preprocess(decode_frame(folder/'capture.pcap',frames[i]),cfg) for i in range(30)]
target,_ = downsample(np.concatenate([x[0] for x in clouds]),np.concatenate([x[1] for x in clouds]),cfg['submap_voxel_size'],cfg['submap_max_points'])
tree=cKDTree(target)
rows=[]
for i in range(30,600,10):
    source,_=preprocess(decode_frame(folder/'capture.pcap',frames[i]),cfg)
    d=tree.query(source)[0]; keep=d<cfg['correspondence_distance']
    _,q=register(source,target,np.eye(4),cfg,tree=tree)
    candidate=np.asarray(q['candidate_pose']) if q.get('candidate_pose') is not None else None
    rows.append(dict(frame=i,time=frames[i]['time'],split='fit' if i<300 else 'holdout',
                     identity_rmse=float(np.sqrt(np.mean(d[keep]**2))),identity_overlap=float(keep.mean()),
                     candidate_translation=translation_m(candidate) if candidate is not None else None,
                     candidate_rotation=rotation_deg(candidate) if candidate is not None else None,quality=q))
fit=[r['identity_rmse'] for r in rows if r['split']=='fit']
threshold=float(np.quantile(fit,.95))
result=dict(data_source='REAL',stationary_declaration='User confirmed first 60s stationary',
            policy='candidate max_rmse = p95 identity nearest-neighbor RMSE on sampled fit frames; not applied automatically',
            proposed_max_rmse=threshold,rows=rows)
out=root/'Plan/evidence/mapping-repair/20260911-repair/static-calibration.json'
out.write_text(json.dumps(result,indent=2))
for split in ('fit','holdout'):
    subset=[r for r in rows if r['split']==split]
    print(split,'identity rmse p50/p95',np.quantile([r['identity_rmse'] for r in subset],[.5,.95]),
          'candidate displacement p50/p95',np.quantile([r['candidate_translation'] for r in subset if r['candidate_translation'] is not None],[.5,.95]),flush=True)
print('proposed_max_rmse',threshold)

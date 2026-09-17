"""REAL fixed-pose held-out coverage diagnostic, not production bootstrap tuning."""
import json
import sys
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'web'))
from backend.mapping import configuration,preprocess,downsample,register
from backend.decoder import decode_frame

root=Path(__file__).resolve().parents[1]
folder=root/'web/data/defcea4d-51db-4f7f-b226-c5ffac4805df'
frames=json.loads((folder/'frames.json').read_text());cfg=configuration()
clouds=[preprocess(decode_frame(folder/'capture.pcap',frames[i]),cfg) for i in range(300)]
held={i:preprocess(decode_frame(folder/'capture.pcap',frames[i]),cfg)[0] for i in [305,405,505,595]}
rows=[]
for length in [30,100,300]:
    xyz=np.concatenate([x[0] for x in clouds[:length]]); intensity=np.concatenate([x[1] for x in clouds[:length]])
    for budget in [40000,200000]:
        target,_=downsample(xyz,intensity,cfg['submap_voxel_size'],budget); tree=cKDTree(target)
        for i,source in held.items():
            d=tree.query(source)[0];valid=d<cfg['correspondence_distance']
            row=dict(reference_frames=length,budget=budget,target_points=len(target),heldout_frame=i,
                     identity_overlap=float(valid.mean()),identity_rmse=float(np.sqrt(np.mean(d[valid]**2))))
            if length==300 and budget==200000:
                _,row['quality']=register(source,target,np.eye(4),cfg,tree=tree)
            rows.append(row)
            print({k:v for k,v in row.items() if k!='quality'},flush=True)
(root/'Plan/evidence/mapping-repair/20260911-repair/reference-coverage.json').write_text(json.dumps(rows,indent=2))

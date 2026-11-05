from pathlib import Path
import json,runpy,numpy as np
out=Path(__file__).parent
ns=runpy.run_path(str(out/'build.py'))
assert ns['yaw_bins']([-.49,.49,.5,1.49,1.5]).tolist()==[0,0,1,1,2]
# Unwrapping 179 -> -179 must continue forward rather than jump a full turn.
yaw=np.rad2deg(np.unwrap(np.deg2rad([179,-179,-178])));assert np.allclose(yaw-yaw[0],[0,2,3])
m=json.loads((out/'metrics.json').read_text(encoding='utf8'));d=np.load(out/'source_observations.npz',allow_pickle=False)
assert len(d['degree_bin'])==m['selected_returns']==sum(b['returns'] for b in m['batches'])
assert np.array_equal(ns['yaw_bins'](d['yaw_deg']),d['degree_bin'])
assert np.allclose(np.linalg.norm(d['xyz_lidar'],axis=1),np.linalg.norm(d['xyz_assumed'],axis=1),atol=1e-5)
assert np.allclose(np.linalg.norm(d['xyz_lidar'],axis=1),d['raw_codes'][:,0]*.016,atol=1e-5)
assert (d['frame_point_index']>=0).all() and (d['frame_point_index']<20000).all()
for b in m['batches']:assert np.count_nonzero(d['degree_bin']==b['degree'])==b['returns']
result={'status':'PASS','checks':['nearest-degree boundaries','yaw unwrap','all selected observations accounted once','actual-angle bins','rigid rotation preserves range','raw distance-code correspondence','source-index bounds','per-batch counts'],'geometry_accuracy_validated':False}
(out/'checks.json').write_text(json.dumps(result,indent=2),encoding='utf8');print(json.dumps(result))

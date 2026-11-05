from pathlib import Path
import json,numpy as np,open3d as o3d
from scipy.spatial import cKDTree
out=Path(__file__).parent; report=[]
for name in ['rotation_plus','static','rotation_minus']:
 mesh=o3d.io.read_triangle_mesh(str(out/(name+'.ply')));p=o3d.io.read_point_cloud(str(out/(name+'_points.ply')));v=np.asarray(mesh.vertices);f=np.asarray(mesh.triangles)
 distance=cKDTree(np.asarray(p.points)).query(v)[0]
 assert len(f)>0 and np.isfinite(v).all() and np.max(distance)<1e-5
 assert np.max(np.linalg.norm(v[f]-v[np.roll(f,1,axis=1)],axis=2))<.400001
 report.append({'scene':name,'vertices_supported_by_input':True,'max_input_distance_m':float(distance.max()),'triangles':len(f)})
(out/'geometry-check.json').write_text(json.dumps(report,indent=2),encoding='utf8');print('3 meshes passed input support and edge checks; geometric accuracy not evaluated')

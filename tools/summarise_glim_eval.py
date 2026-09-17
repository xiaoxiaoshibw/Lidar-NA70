"""Evaluate drift against the user-declared initial stationary interval."""
import json
from pathlib import Path
import numpy as np

root=Path.home()/'na70-glim-eval'
rows=[]
for name in ['dump-ct-map','dump-ct-bootstrap','dump-ct-snapshot']:
    a=np.loadtxt(root/name/'odom_lidar.txt')
    t=a[:,0]-560.069
    d=np.linalg.norm(a[:,1:4]-a[0,1:4],axis=1)
    angle=np.degrees(2*np.arccos(np.clip(abs(a[:,7]),0,1)))
    row=dict(name=name,trajectory_samples=len(a),static_max_translation=float(d[t<60].max()),
             static_max_rotation_deg=float(angle[t<60].max()),final_translation=float(d[-1]),
             environment='WSL2_HUMBLE_X86_64',imu_used=False,global_optimization=False,
             geometric_acceptance='NOT_EVALUATED')
    rows.append(row)
    print(row)
Path('/mnt/d/Code/radar/Plan/evidence/mapping-repair/20260911-repair/glim-comparison.json').write_text(json.dumps(rows,indent=2))

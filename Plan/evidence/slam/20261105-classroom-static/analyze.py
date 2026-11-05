from pathlib import Path
import sys, json, hashlib
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
root=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(root/'web'))
from backend import decoder
pcap=root/'radar_capture_data/na70_sessions/20261105-162858-351200/raw/lidar.pcap'
out=Path(__file__).resolve().parent
scan=decoder.scan_pcap(pcap)
blocks=[]; total=0
with pcap.open('rb') as src:
 for f in scan.frames:
  p=decoder.decode_frame(pcap,f,1,source=src)
  c=decoder.decode_frame_codes(pcap,f,source=src)
  valid=(c[:,0]!=0)&(c[:,0]!=65535)&(c[:,1]!=65535)&(c[:,2]!=65535)&np.isfinite(p[:,:3]).all(axis=1)
  blocks.append(p[valid,:3]); total+=len(c)
p=np.concatenate(blocks)
r=np.linalg.norm(p,axis=1)
# 5 cm voxels preserve mean of observed points and return count, no pose correction.
key=np.floor(p/0.05).astype(np.int32)
_, inverse, counts=np.unique(key,axis=0,return_inverse=True,return_counts=True)
xyz=np.column_stack([np.bincount(inverse,weights=p[:,i])/counts for i in range(3)])
with (out/'static_observations.ply').open('w',encoding='ascii') as f:
 f.write('ply\nformat ascii 1.0\ncomment DIAGNOSTIC unregistered sensor coordinates voxel 0.05m\nelement vertex %d\nproperty float x\nproperty float y\nproperty float z\nproperty uint observations\nend_header\n'%len(xyz))
 for v,n in zip(xyz,counts): f.write('%.4f %.4f %.4f %d\n'%(*v,n))
fig,axes=plt.subplots(1,3,figsize=(16,5),constrained_layout=True)
for ax,(a,b,title) in zip(axes,[(0,1,'Top: X-Y'),(0,2,'Side: X-Z'),(1,2,'Front: Y-Z')]):
 mask=(np.linalg.norm(xyz,axis=1)<=35)
 q=xyz[mask]; n=counts[mask]
 ax.scatter(q[:,a],q[:,b],c=np.log10(n+1),s=1,cmap='viridis',vmin=0,vmax=2,rasterized=True)
 ax.scatter([0],[0],marker='x',c='red',s=40,label='LiDAR origin')
 ax.set(xlabel='XYZ'[a]+' (m)',ylabel='XYZ'[b]+' (m)',title=title,aspect='equal'); ax.grid(alpha=.25); ax.legend(loc='upper right',fontsize=8)
fig.suptitle('Static scene: 21 s accumulated, sensor coordinates; color = log10(return count + 1); within 35 m',fontsize=11)
fig.savefig(out/'static_views.png',dpi=180); plt.close(fig)
summary={'source':str(pcap),'sha256':hashlib.sha256(pcap.read_bytes()).hexdigest(),'frames':len(scan.frames),'duration_s':scan.duration,'rays':total,'selected_returns':len(p),'min_range_m':float(r.min()),'range_quantiles_m':np.quantile(r,[0,.01,.1,.5,.9,.99,1]).tolist(),'under_8m':int((r<8).sum()),'under_10m':int((r<10).sum()),'under_20m':int((r<20).sum()),'under_35m':int((r<35).sum()),'voxel_m':0.05,'voxel_count':len(xyz),'limits':'sensor coordinates; static accumulation; no registration or extrinsic validation; 0xFFFF excluded for display with manufacturer semantics unverified'}
(out/'metrics.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf8')
print(json.dumps(summary,ensure_ascii=False,indent=2))

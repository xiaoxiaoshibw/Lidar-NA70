from pathlib import Path
import json,sys,hashlib
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
root=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(root/'web'),str(root/'packages/na70_core')]
from backend import decoder
folder=root/'radar_capture_data/na70_sessions/20261105-162734-639900'
pcap=folder/'raw/lidar.pcap'; imu=folder/'imu_frames.jsonl'; out=Path(__file__).parent
rows=[json.loads(x) for x in imu.open(encoding='utf8')]
t=np.array([x['t_unix'] for x in rows]); yaw=np.unwrap(np.deg2rad([x['euler'][2] for x in rows])); scan=decoder.scan_pcap(pcap)
points=[]; groups=[]
with pcap.open('rb') as f:
 for i,frame in enumerate(scan.frames):
  if i%5: continue
  ft=np.mean([frame['packet_capture_times'][0],frame['packet_capture_times'][-1]])
  if ft<t[0] or ft>t[-1]: continue
  p=decoder.decode_frame(pcap,frame,1,source=f); c=decoder.decode_frame_codes(pcap,frame,source=f)
  ok=(c[:,0]!=0)&(c[:,0]!=65535)&(c[:,1]!=65535)&(c[:,2]!=65535)&(c[:,0]*.016<20)
  p=p[ok,:3][::4]; angle=float(np.interp(ft,t,yaw)-yaw[0]); points.append((p,angle)); groups.append([i,ft,float(np.rad2deg(angle)),len(p)])
fig,axs=plt.subplots(1,2,figsize=(14,7),constrained_layout=True)
meta=[]
for ax,sign in zip(axs,[1,-1]):
 clouds=[]
 for p,a in points:
  ca,sa=np.cos(sign*a),np.sin(sign*a); q=p.copy(); q[:,0]=ca*p[:,0]-sa*p[:,1];q[:,1]=sa*p[:,0]+ca*p[:,1];clouds.append(q)
 cloud=np.concatenate(clouds); key=np.floor(cloud/.1).astype(np.int32); _,ind,n=np.unique(key,axis=0,return_inverse=True,return_counts=True); xyz=np.column_stack([np.bincount(ind,weights=cloud[:,j])/n for j in range(3)])
 name='plus' if sign==1 else 'minus'
 with (out/(name+'.ply')).open('w',encoding='ascii') as f:
  f.write('ply\nformat ascii 1.0\ncomment DIAGNOSTIC assumed yaw sign %d rotation only not calibration\nelement vertex %d\nproperty float x\nproperty float y\nproperty float z\nproperty uint observations\nend_header\n'%(sign,len(xyz)))
  for v,k in zip(xyz,n): f.write('%.4f %.4f %.4f %d\n'%(*v,k))
 ax.scatter(xyz[:,0],xyz[:,1],c=xyz[:,2],s=.7,cmap='viridis',vmin=-3,vmax=7,rasterized=True); ax.scatter(0,0,c='red',marker='x',s=45);ax.set(title='Assumed yaw sign %s'%('+' if sign==1 else '-'),xlabel='X (m)',ylabel='Y (m)',aspect='equal'); ax.grid(alpha=.2)
 meta.append({'sign':sign,'voxel_count':len(xyz),'selected_points':len(cloud)})
fig.suptitle('Experimental rotation stack: host-time IMU yaw, uncalibrated axis and sign; color = sensor-relative Z (m)',fontsize=10)
fig.savefig(out/'yaw_comparison.png',dpi=160);plt.close(fig)
result={'status':'DIAGNOSTIC_ASSUMPTIONS_UNVERIFIED','pcap_sha256':hashlib.sha256(pcap.read_bytes()).hexdigest(),'imu_sha256':hashlib.sha256(imu.read_bytes()).hexdigest(),'frames_selected':len(groups),'yaw_range_deg':[min(x[2] for x in groups),max(x[2] for x in groups)],'voxel_m':.1,'range_max_m':20,'frame_step':5,'ray_stride':4,'candidates':meta,'assumptions':['IMU yaw rotates about lidar Z','two possible signs','fixed position','host receive midpoint interpolation','frame-level motion approximation','no translation compensation','no scan registration'],'limitations':['not calibrated','no independent geometry validation','65535 excluded as diagnostic only']}
(out/'metrics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(result,ensure_ascii=False,indent=2))

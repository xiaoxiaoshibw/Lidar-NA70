"""Packet-time, 1-degree observation batches. Pose model is diagnostic only."""
from pathlib import Path
import json, sys, hashlib, time
import numpy as np
import open3d as o3d
from plotly.offline import get_plotlyjs
ROOT=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(ROOT/'web'),str(ROOT/'packages/na70_core')]
from backend import decoder
OUT=Path(__file__).parent
CFG=json.loads((OUT/'config.json').read_text(encoding='utf-8-sig'))

def yaw_bins(degrees):
    return np.floor(np.asarray(degrees)/CFG['degree_step']+0.5).astype(np.int32)

def voxel(points):
    if len(points)==0:return points
    _, ix=np.unique(np.floor(points/CFG['voxel_m']).astype(np.int32),axis=0,return_index=True)
    return points[np.sort(ix)]  # first measured point; no invented centroid

def mesh_points(xyz):
    pc=o3d.geometry.PointCloud(o3d.utility.Vector3dVector(xyz))
    if len(xyz)<10:return pc,o3d.geometry.TriangleMesh()
    pc.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=CFG['normal_radius_m'],max_nn=CFG['normal_nn']))
    pc.orient_normals_towards_camera_location(np.zeros(3))
    mesh=o3d.geometry.TriangleMesh.create_from_point_cloud_ball_pivoting(pc,o3d.utility.DoubleVector(CFG['ball_radii_m']))
    v=np.asarray(mesh.vertices); f=np.asarray(mesh.triangles)
    if len(f):
        edges=np.linalg.norm(v[f]-v[np.roll(f,1,axis=1)],axis=2)
        mesh.remove_triangles_by_mask(edges.max(axis=1)>CFG['max_triangle_edge_m'])
        mesh.remove_degenerate_triangles();mesh.remove_duplicated_triangles();mesh.remove_unreferenced_vertices();mesh.compute_vertex_normals()
    return pc,mesh

def build():
    start=time.monotonic();session=ROOT/'radar_capture_data/na70_sessions/20261105-162734-639900'
    imu=session/'imu_frames.jsonl';pcap=session/'raw/lidar.pcap';rows=[json.loads(s) for s in imu.open(encoding='utf8')]
    times=np.array([r['t_unix'] for r in rows]); angles=np.unwrap(np.deg2rad([r['euler'][2] for r in rows]));angles-=angles[0]
    assert np.isfinite(times).all() and np.all(np.diff(times)>=0)
    times,unique=np.unique(times,return_index=True);angles=angles[unique]
    scan=decoder.scan_pcap(pcap);chunks=[];raw_rays=0;outside=0;far=0;packets=[]
    with pcap.open('rb') as src:
        for fi,frame in enumerate(scan.frames):
            p=decoder.decode_frame(pcap,frame,1,source=src);codes=decoder.decode_frame_codes(pcap,frame,source=src)
            ts=np.repeat(np.asarray(frame['packet_capture_times']),200);assert len(ts)==len(p)
            valid=(codes[:,0]!=0)&(codes[:,0]!=65535)&(codes[:,1]!=65535)&(codes[:,2]!=65535)&np.isfinite(p).all(axis=1)
            cover=(ts>=times[0])&(ts<=times[-1]);outside+=int((valid&~cover).sum());far+=int((valid&cover&(codes[:,0]*.016>CFG['max_range_m'])).sum())
            valid &= cover&(codes[:,0]*.016<=CFG['max_range_m']);idx=np.flatnonzero(valid);raw_rays+=len(p)
            if len(idx)==0:continue
            yaw=np.interp(ts[idx],times,angles);xyz=p[idx,:3].copy();c=np.cos(yaw);s=np.sin(yaw);q=xyz.copy();q[:,0]=c*xyz[:,0]-s*xyz[:,1];q[:,1]=s*xyz[:,0]+c*xyz[:,1]
            chunks.append({'xyz_lidar':xyz,'xyz_assumed':q,'t_unix':ts[idx],'yaw_deg':np.rad2deg(yaw),'degree_bin':yaw_bins(np.rad2deg(yaw)),'frame_index':np.full(len(idx),fi,dtype=np.int32),'frame_point_index':idx.astype(np.int32),'raw_codes':codes[idx]})
    arrays={k:np.concatenate([d[k] for d in chunks]) for k in chunks[0]};np.savez_compressed(OUT/'source_observations.npz',**arrays)
    (OUT/'batches').mkdir(exist_ok=True);groups=[];payload=[];lo=int(arrays['degree_bin'].min());hi=int(arrays['degree_bin'].max())
    for degree in range(lo,hi+1):
        ids=np.flatnonzero(arrays['degree_bin']==degree);q=voxel(arrays['xyz_assumed'][ids]);local=voxel(arrays['xyz_lidar'][ids]);pc,mesh=mesh_points(q)
        name=f'degree_{degree:+04d}';o3d.io.write_point_cloud(str(OUT/'batches'/(name+'_sensor.ply')),o3d.geometry.PointCloud(o3d.utility.Vector3dVector(local)))
        o3d.io.write_point_cloud(str(OUT/'batches'/(name+'_assumed.ply')),pc)
        v=np.asarray(mesh.vertices);f=np.asarray(mesh.triangles)
        if len(f):o3d.io.write_triangle_mesh(str(OUT/'batches'/(name+'_surface.ply')),mesh)
        groups.append({'degree':degree,'returns':len(ids),'packet_count':len(np.unique(arrays['frame_index'][ids].astype(np.int64)*100+arrays['frame_point_index'][ids]//200)),'first_s':float(arrays['t_unix'][ids].min()-times[0]) if len(ids) else None,'last_s':float(arrays['t_unix'][ids].max()-times[0]) if len(ids) else None,'voxel_points':len(q),'triangles':len(f),'status':'OBSERVED' if len(ids) else 'EMPTY','files_prefix':name})
        payload.append({'degree':degree,'p':np.round(q,4).tolist(),'v':np.round(v,4).tolist(),'f':f.tolist(),'returns':len(ids)})
    assert sum(g['returns'] for g in groups)==len(arrays['degree_bin'])
    allq=voxel(arrays['xyz_assumed']);pc,mesh=mesh_points(allq);o3d.io.write_point_cloud(str(OUT/'cumulative_assumed.ply'),pc);o3d.io.write_triangle_mesh(str(OUT/'cumulative_surface.ply'),mesh)
    result={'status':'DEGREE_BATCH_DIAGNOSTIC','session':str(session),'pcap_sha256':hashlib.sha256(pcap.read_bytes()).hexdigest(),'imu_sha256':hashlib.sha256(imu.read_bytes()).hexdigest(),'generator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'config':CFG,'raw_rays':raw_rays,'selected_returns':len(arrays['degree_bin']),'outside_imu_returns':outside,'over_range_returns':far,'yaw_range_deg':[float(arrays['yaw_deg'].min()),float(arrays['yaw_deg'].max())],'degree_range':[lo,hi],'batches':groups,'cumulative_points':len(allq),'cumulative_triangles':len(mesh.triangles),'seconds':time.monotonic()-start,'claims':{'packet_host_time_interpolation':True,'one_degree_batches':True,'calibration_validated':False,'sampling_sync_validated':False,'three_walls_verified':False},'limits':['assumed IMU yaw axis = lidar Z, positive sign, fixed origin','packet host receive time copied to 200 rays, not device firing time','0xFFFF codes excluded for diagnostic display, manufacturer semantics unverified','no geometric registration or pose optimization','20m selection for classroom inspection; far returns counted','no extrapolation beyond IMU coverage; actual yaw used in transform; bin centers only group observations']}
    (OUT/'metrics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    template=(OUT/'viewer.html').read_text(encoding='utf8');(OUT/'index.html').write_text(template.replace('__PLOTLY__',get_plotlyjs()).replace('__BATCHES__',json.dumps(payload,separators=(',',':'))),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='batches'},ensure_ascii=False,indent=2))

if __name__=='__main__':build()

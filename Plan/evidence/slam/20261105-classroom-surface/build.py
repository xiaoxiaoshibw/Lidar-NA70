"""Observed-surface meshing only; does not estimate poses or calibrate sensors."""
from pathlib import Path
import json,hashlib,platform,time
import numpy as np
import open3d as o3d
import plotly.graph_objects as go
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
out=Path(__file__).parent
cfg=json.loads((out/'config.json').read_text(encoding='utf-8-sig'))
sources=[('rotation_plus','旋转拼接＋：轴向与位置假设待验证',out.parent/'20261105-classroom-rotation-prototype/plus.ply'),('static','固定朝向：静止实测表面',out.parent/'20261105-classroom-static/static_observations.ply'),('rotation_minus','旋转拼接－：对照假设待验证',out.parent/'20261105-classroom-rotation-prototype/minus.ply')]
fig=go.Figure(); metrics=[]; start=time.monotonic()
preview,axs=plt.subplots(1,2,figsize=(15,7),subplot_kw={'projection':'3d'},constrained_layout=True)
for group,(name,label,source) in enumerate(sources):
 pc=o3d.io.read_point_cloud(str(source)); a=np.asarray(pc.points); valid=np.isfinite(a).all(axis=1)&(np.linalg.norm(a,axis=1)<cfg['max_range_m']); pc=pc.select_by_index(np.flatnonzero(valid));pc=pc.voxel_down_sample(cfg['voxel_m']);pc,keep=pc.remove_statistical_outlier(nb_neighbors=cfg['outlier_neighbors'],std_ratio=cfg['outlier_std_ratio'])
 pc.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=cfg['normal_radius_m'],max_nn=cfg['normal_max_nn']));pc.orient_normals_towards_camera_location(np.zeros(3))
 mesh=o3d.geometry.TriangleMesh.create_from_point_cloud_ball_pivoting(pc,o3d.utility.DoubleVector(cfg['ball_radii_m']))
 v=np.asarray(mesh.vertices); tri=np.asarray(mesh.triangles); edges=np.linalg.norm(v[tri]-v[np.roll(tri,1,axis=1)],axis=2); remove=(edges.max(axis=1)>cfg['max_triangle_edge_m']); mesh.remove_triangles_by_mask(remove);mesh.remove_degenerate_triangles();mesh.remove_duplicated_triangles();mesh.remove_unreferenced_vertices();mesh.compute_vertex_normals()
 v=np.asarray(mesh.vertices);tri=np.asarray(mesh.triangles);p=np.asarray(pc.points)
 assert len(tri)>0 and np.isfinite(v).all() and tri.min()>=0 and tri.max()<len(v)
 longest=float(np.linalg.norm(v[tri]-v[np.roll(tri,1,axis=1)],axis=2).max());assert longest<=cfg['max_triangle_edge_m']+1e-8
 o3d.io.write_triangle_mesh(str(out/(name+'.ply')),mesh,write_ascii=False);o3d.io.write_triangle_mesh(str(out/(name+'.obj')),mesh,write_ascii=True,write_triangle_uvs=False);o3d.io.write_point_cloud(str(out/(name+'_points.ply')),pc)
 fig.add_trace(go.Mesh3d(x=v[:,0],y=v[:,1],z=v[:,2],i=tri[:,0],j=tri[:,1],k=tri[:,2],intensity=v[:,2],colorscale='Viridis',showscale=False,name='观测表面',visible=group==0,flatshading=False,lighting={'ambient':.65,'diffuse':.8},hovertemplate='X %{x:.2f} m<br>Y %{y:.2f} m<br>Z %{z:.2f} m<extra>插值表面</extra>'))
 fig.add_trace(go.Scatter3d(x=p[:,0],y=p[:,1],z=p[:,2],mode='markers',marker={'size':1.6,'color':p[:,2],'colorscale':'Viridis','opacity':.65},name='实测点云',visible=group==0,hovertemplate='X %{x:.2f}<br>Y %{y:.2f}<br>Z %{z:.2f}<extra>观测点</extra>'))
 metrics.append({'name':name,'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'input_points':len(a),'filtered_points':len(p),'mesh_vertices':len(v),'mesh_triangles':len(tri),'maximum_edge_m':longest,'bounds_m':[v.min(axis=0).tolist(),v.max(axis=0).tolist()],'watertight':mesh.is_watertight(),'calibration_validated':False,'three_walls_verified':False})
 if group<2:
  ax=axs[group];ax.add_collection3d(Poly3DCollection(v[tri],facecolors=plt.colormaps['viridis'](np.clip((v[tri].mean(axis=1)[:,2]-v[:,2].min())/max(np.ptp(v[:,2]),1e-6),0,1)),linewidths=0,alpha=1));ax.auto_scale_xyz(v[:,0],v[:,1],v[:,2]);ax.set_box_aspect(np.maximum(np.ptp(v,axis=0),.1));ax.view_init(elev=24,azim=-115);ax.set(xlabel='X (m)',ylabel='Y (m)',zlabel='Z (m)',title=['Rotation + : unverified pose','Static observed surfaces'][group])
buttons=[]
for i,(_,label,_) in enumerate(sources):buttons.append({'label':label,'method':'update','args':[{'visible':[j//2==i for j in range(6)]}]})
fig.update_layout(title={'text':'教室观测表面 · 三维重建预览','x':.02},template='plotly_dark',scene={'aspectmode':'data','xaxis_title':'X / m','yaxis_title':'Y / m','zaxis_title':'Z / m（参考坐标，未验证重力方向）','camera':{'eye':{'x':-1.5,'y':-1.8,'z':1.1}}},updatemenus=[{'buttons':buttons,'x':.02,'y':1.04,'xanchor':'left'}],margin={'l':0,'r':0,'b':0,'t':105},legend={'x':.01,'y':.93},uirevision='keep-camera')
html=fig.to_html(full_html=True,include_plotlyjs=True,default_height='calc(100vh - 70px)',config={'responsive':True,'displaylogo':False})
html=html.replace('<body>','<body><div style="background:#182233;color:#e5edf7;padding:12px 20px;font:14px sans-serif">左键旋转 · 滚轮缩放 · 右键平移 · 点击图例切换点云/表面。表面来自局部点云连接，空洞保留。旋转场景采用未标定的姿态假设；三面墙、物体类别和房间尺寸尚未验证。</div>')
(out/'index.html').write_text(html,encoding='utf8');preview.suptitle('3D observed-surface reconstruction | open boundaries retained | geometry not validated',fontsize=12);preview.savefig(out/'surface_preview.png',dpi=160);plt.close(preview)
manifest={'status':'OBSERVED_SURFACE_PROTOTYPE','seconds':time.monotonic()-start,'python':platform.python_version(),'open3d':o3d.__version__,'config':cfg,'generator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'scenes':metrics,'method':'Open3D ball pivoting; statistical outliers; edge cap; no pose registration; no hole completion','sources':['https://www.open3d.org/docs/release/tutorial/geometry/surface_reconstruction.html']}
(out/'metrics.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf8');print(json.dumps(manifest,ensure_ascii=False,indent=2))

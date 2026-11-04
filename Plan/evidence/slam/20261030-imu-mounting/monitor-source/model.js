'use strict';
const $=id=>document.getElementById(id);
const inputNames=['width','depth','height','ix','iy','iz','boardx','boardy','boardz'];
let yaw=.77,elevation=.43,zoom=1,drag=null,latest=null,baseline=null;
const canvas=$('assembly'),ctx=canvas.getContext('2d');
const fmt=(n,k=3)=>typeof n==='number'?n.toFixed(k):'—';
const p=()=>Object.fromEntries(inputNames.map(k=>[k,Number($(k).value)]));
function validParams(v){return inputNames.every(k=>Number.isFinite(v[k])&&v[k]>=Number($(k).min)&&v[k]<=Number($(k).max))}
function boxes(v){
 const W=v.width,D=v.depth,H=v.height,b=[];
 const box=(name,x,y,z,dx,dy,dz,color)=>b.push({name,c:[x,y,z],s:[dx,dy,dz],color});
 box('housing',0,0,0,D,W*.90,H,'#242d37');
 box('top_plate',0,0,H/2-1,D,W,2,'#414a53');
 box('bottom_plate',0,0,-H/2+2,D,W,4,'#222a33');
 for(const side of [-1,1])for(let i=0;i<14;i++)box('heatsink',-D/2+(i+.5)*D/14,side*W*.47,-H*.02,D/46,W*.06,H*.94,'#303b47');
 box('front_frame',D/2+.3,0,H*.04,2,W*.96,H*.89,'#1b2631');
 box('optical_window',D/2+1.5,0,H*.13,.7,W*.81,H*.65,'#775a32');
 box('window_upper_reflection',D/2+2,0,H*.36,.3,W*.77,H*.07,'#a08757');
 box('front_lower_vent',D/2+1.8,0,-H*.38,2,W*.87,H*.16,'#101c26');
 for(let i=0;i<9;i++)box('front_vent_fin',D/2+3,-W*.4+i*W*.1,-H*.38,3,W*.025,H*.16,'#33404e');
 box('left_lower_connector',-D*.30,W/2+4,-H*.32,W*.31,7,H*.11,'#a6a6a0');
 box('left_lower_socket',-D*.30,W/2+8,-H*.32,W*.23,2,H*.06,'#263444');
 box('imu_spacer',v.ix,v.iy,v.iz-v.boardz*.30,v.boardx,v.boardy,v.boardz*.5,'#333d48');
 box('imu_pcb',v.ix,v.iy,v.iz,v.boardx,v.boardy,1.7,'#1f655e');
 box('imu_core_visual',v.ix+v.boardx*.05,v.iy,v.iz+v.boardz*.28,v.boardx*.30,v.boardy*.40,v.boardz*.35,'#b3b6ab');
 box('usb_port',v.ix-v.boardx*.44,v.iy,v.iz+v.boardz*.23,v.boardx*.20,v.boardy*.3,v.boardz*.5,'#9aa5b2');
 box('status_led',v.ix+v.boardx*.32,v.iy-v.boardy*.32,v.iz+2,2,2,1,'#57f9c0');
 for(let i=0;i<6;i++)box('imu_pin',v.ix+v.boardx*.37,v.iy-v.boardy*.32+i*v.boardy*.12,v.iz+1,3,1,1,'#d8c6a1');
 return b;
}
function vertices(b){const [x,y,z]=b.c,[dx,dy,dz]=b.s.map(v=>v/2);return [[x-dx,y-dy,z-dz],[x+dx,y-dy,z-dz],[x+dx,y+dy,z-dz],[x-dx,y+dy,z-dz],[x-dx,y-dy,z+dz],[x+dx,y-dy,z+dz],[x+dx,y+dy,z+dz],[x-dx,y+dy,z+dz]]}
const faceIndices=[[0,3,2,1],[4,5,6,7],[0,1,5,4],[3,7,6,2],[1,2,6,5],[0,4,7,3]];
function rotate(v){
 if(!$('follow').checked||!latest?.fields.euler||!baseline)return v;
 const a=latest.fields.euler.map((n,i)=>((n-baseline[i]+540)%360-180)*Math.PI/180);
 const [pitch,roll,y]=a;let [x,yy,z]=v;
 [yy,z]=[Math.cos(roll)*yy-Math.sin(roll)*z,Math.sin(roll)*yy+Math.cos(roll)*z];
 [x,z]=[Math.cos(pitch)*x+Math.sin(pitch)*z,-Math.sin(pitch)*x+Math.cos(pitch)*z];
 return [Math.cos(y)*x-Math.sin(y)*yy,Math.sin(y)*x+Math.cos(y)*yy,z];
}
function shade(hex,factor){return '#'+hex.slice(1).match(/../g).map(n=>Math.min(255,Math.round(parseInt(n,16)*factor)).toString(16).padStart(2,'0')).join('')}
function render(){
 const v=p();if(!validParams(v))return;
 const rect=canvas.getBoundingClientRect(),w=rect.width,h=rect.height,dpr=window.devicePixelRatio||1;
 canvas.width=w*dpr;canvas.height=h*dpr;ctx.scale(dpr,dpr);ctx.clearRect(0,0,w,h);
 const scale=Math.min(w,h)/(Math.max(v.width,v.depth,v.height)*1.45)*zoom;
 const right=[-Math.sin(yaw),Math.cos(yaw),0],up=[-Math.sin(elevation)*Math.cos(yaw),-Math.sin(elevation)*Math.sin(yaw),Math.cos(elevation)],eye=[Math.cos(elevation)*Math.cos(yaw),Math.cos(elevation)*Math.sin(yaw),Math.sin(elevation)];
 const dot=(a,b)=>a.reduce((s,n,i)=>s+n*b[i],0);
 const project=(point,turn=true)=>{const q=turn?rotate(point):point;return [w/2+dot(q,right)*scale,h*.50-dot(q,up)*scale,dot(q,eye)]};
 const line=(a,b,color,width=1,dashed=false)=>{const pa=project(a),pb=project(b);ctx.strokeStyle=color;ctx.lineWidth=width;ctx.setLineDash(dashed?[4,4]:[]);ctx.beginPath();ctx.moveTo(pa[0],pa[1]);ctx.lineTo(pb[0],pb[1]);ctx.stroke();ctx.setLineDash([])};
 const ground=-v.height/2-4;
 for(let i=-100;i<=100;i+=20){line([i,-100,ground],[i,100,ground],'#283647');line([-100,i,ground],[100,i,ground],'#283647')}
 const faces=[];
 for(const box of boxes(v)){const vs=vertices(box).map(q=>project(q));const mounted=/^(imu_|usb_port|status_led)/.test(box.name);faceIndices.forEach((idx,i)=>{const poly=idx.map(j=>vs[j]);faces.push({poly,layer:mounted?((box.name==='imu_spacer'?1:box.name==='imu_pcb'?2:3)*(elevation>=0?1:-1)):0,depth:poly.reduce((sum,q)=>sum+q[2],0)/4,color:shade(box.color,[.60,1.22,.85,.70,1,.72][i])})})}
 // Mounted components form a schematic overlay above the housing roof.
 faces.sort((a,b)=>a.layer-b.layer||a.depth-b.depth);
 for(const f of faces){ctx.beginPath();f.poly.forEach((q,i)=>i?ctx.lineTo(q[0],q[1]):ctx.moveTo(q[0],q[1]));ctx.closePath();ctx.fillStyle=f.color;ctx.fill();ctx.strokeStyle='#060c1255';ctx.lineWidth=.5;ctx.stroke()}
 ctx.font='12px Consolas,monospace';
 const label=(text,x,y)=>ctx.fillText(text,Math.max(5,Math.min(w-ctx.measureText(text).width-5,x)),Math.max(14,Math.min(h-12,y)));
 if($('axes').checked){const len=Math.max(v.width,v.depth)*.60;const origin=[0,0,0];for(const [point,name,color] of [[[len,0,0],'B:X FRONT','#ff907d'],[[0,len,0],'B:Y LEFT','#70e6b5'],[[0,0,len],'B:Z UP','#7eb9ff']]){line(origin,point,color,2,true);const q=project(point);ctx.fillStyle=color;label(name,q[0]+5,q[1]-5)}const o=project(origin);ctx.fillStyle='#e9f3ff';label('B',o[0]+4,o[1]-4)}
 const i=project([v.ix,v.iy,v.iz+v.boardz]);ctx.fillStyle='#8ff7d5';ctx.beginPath();ctx.arc(i[0],i[1],3,0,Math.PI*2);ctx.fill();label('I* (visual)',i[0]+8,i[1]-8);
 $('model-readout').textContent=`W ${v.width} × D ${v.depth} × H ${v.height} mm | I* [${v.ix}, ${v.iy}, ${v.iz}] mm（粗估） | 正交投影`;
}
canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY};canvas.setPointerCapture(e.pointerId);canvas.style.cursor='grabbing'});
canvas.addEventListener('pointermove',e=>{if(!drag)return;yaw-=(e.clientX-drag.x)*.009;elevation=Math.max(-1.45,Math.min(1.55,elevation+(e.clientY-drag.y)*.009));drag={x:e.clientX,y:e.clientY};render()});
canvas.addEventListener('pointerup',()=>{drag=null;canvas.style.cursor='grab'});canvas.addEventListener('pointercancel',()=>{drag=null});
canvas.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.min(2.5,Math.max(.4,zoom*Math.exp(-e.deltaY*.001)));render()},{passive:false});
document.querySelectorAll('[data-view]').forEach(btn=>btn.onclick=()=>{const view=btn.dataset.view;[yaw,elevation]=({iso:[.77,.43],top:[0,Math.PI/2],front:[0,0],side:[Math.PI/2,0]})[view];$('follow').checked=false;zoom=1;render()});
inputNames.forEach(k=>$(k).addEventListener('input',render));$('axes').onchange=render;
$('follow').onchange=()=>{if(!baseline&&latest?.fields.euler)baseline=[...latest.fields.euler];render()};
$('zero').onclick=()=>{baseline=latest?.fields.euler?[...latest.fields.euler]:null;render()};
function download(name,text,type){const url=URL.createObjectURL(new Blob([text],{type}));const link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
$('export-model').onclick=()=>{const v=p();if(!validParams(v))return;download('na70-yesense-model.json',JSON.stringify({schema:'na70.mounting-visual.v1',status:'APPROXIMATE_NOT_CALIBRATED',usable_for_sensor_fusion:false,units:'mm',body_dimensions:{width:v.width,depth:v.depth,height:v.height,source:'user-provided 9cm 13cm 11cm, interpreted W D H'},body_frame:'Geometric housing center, X front optical window, Y left, Z up; not verified LiDAR frame',imu_visual_center_mm:[v.ix,v.iy,v.iz],imu_position_source:'user-confirmed rear-top centerline Y=0, moved further toward negative X; X/Z photo-estimate / editable illustration',imu_board_dimensions_estimated_mm:[v.boardx,v.boardy,v.boardz],housing_connector:{face:'+Y LEFT',view:'from +Y toward body center',screen_corner:'lower-right',center_estimated_mm:[-v.depth*.30,v.width/2+4,-v.height*.32],source:'user-confirmed housing large connector at Y LEFT lower-right; numeric position is illustrative'},T_IL:null,time_offset_s:null,notes:['Visible board center is not confirmed inertial measurement origin.','Optical origin and IMU axes unknown.','Mesh details and sensor geometry are approximate.']},null,2),'application/json')};
$('export-obj').onclick=()=>{const v=p();if(!validParams(v))return;let offset=1,lines=['# APPROXIMATE NOT CALIBRATED; units mm; housing center; X front Y left Z up'];for(const b of boxes(v)){lines.push('g '+b.name);vertices(b).forEach(q=>lines.push('v '+q.map(n=>n.toFixed(4)).join(' ')));faceIndices.forEach(f=>lines.push('f '+f.map(i=>i+offset).join(' ')));offset+=8}download('na70-yesense-approx-mm.obj',lines.join('\n'),'text/plain')};
async function live(){try{const response=await fetch('/api/data?after=999999999',{signal:AbortSignal.timeout(2000)});if(!response.ok)throw Error();const s=await response.json();latest=s.latest;$('sensor-state').textContent=s.status==='live'?`实时 ${s.hz.toFixed(1)} Hz`:'等待 IMU 数据';if($('follow').checked){if(!baseline&&latest?.fields.euler)baseline=[...latest.fields.euler];render()}}catch{$('sensor-state').textContent='采集服务未连接'}finally{setTimeout(live,120)}}
const checkNames={sample_count:'样本数',host_span:'时长',accel_axis_std:'加速度波动',accel_norm_std:'模长波动',gyro_axis_std:'陀螺波动',gyro_mean:'陀螺均值',accel_norm:'重力模长',host_continuity:'接收连续',sequence_continuity:'帧序号',stream_integrity:'数据完整性',complete_fields:'字段完整'};
async function calibration(){
 try{const response=await fetch('/api/calibration',{signal:AbortSignal.timeout(2000)});if(!response.ok)throw Error('无法读取检查状态');const job=await response.json();
  $('cal-start').disabled=job.status==='collecting';$('progress').style.width=(job.progress||0)+'%';
  if(job.status==='collecting'){$('cal-status').textContent=`采集中 ${Math.floor((job.progress||0)/10)} / 10 秒 · 请保持静止`}
  else if(job.status==='complete'){
   const r=job.result,accepted=r.status==='STATIC_LIKE_WINDOW';$('cal-status').textContent=accepted?'静置一致性符合 · 候选估计待人工确认':r.status==='INSUFFICIENT_DATA'?'样本不足 · 请确认串口有数据后重试':'检查未通过 · 请查看失败项';
   $('cal-samples').textContent=`${r.sample_count} / ${fmt(r.host_received_hz,1)} Hz`;$('cal-norm').textContent=fmt(r.accel_mean_norm_m_s2,5);
   $('cal-tilt').textContent=r.gravity_tilt_deg?`${fmt(r.gravity_tilt_deg.roll,2)} / ${fmt(r.gravity_tilt_deg.pitch,2)}`:'—';$('cal-clock').textContent=!r.sample_count?'无有效样本':r.device_timestamp_present?'本次存在':'未输出';
   $('checks').replaceChildren();for(const [key,value] of Object.entries(r.checks||{})){const el=document.createElement('span');el.className='check'+(value?'':' fail');el.textContent=(value?'✓ ':'× ')+(checkNames[key]||key);$('checks').append(el)}
   const vector=v=>v?v.map(n=>n.toFixed(6)).join(', '):'不可估计';
   $('cal-report').textContent=`加速度均值 [m/s²]  [${vector(r.accel_mean_m_s2)}]\n加速度标准差       [${vector(r.accel_std_m_s2)}]\n陀螺均值 [°/s]     [${vector(r.gyro_mean_deg_s)}]\n陀螺标准差         [${vector(r.gyro_std_deg_s)}]\n候选 gyro bias [rad/s] [${vector(r.gyro_bias_candidate_rad_s)}]\n重力方向（IMU系）  [${vector(r.apparent_gravity_direction_imu)}]\n窗口：${fmt(r.host_span_s,3)} s / 最大接收间隔 ${fmt(r.max_host_gap_s,3)} s\n证据：${r.source_file||'保存失败'}\nSHA-256: ${r.source_sha256||'—'}\n完整标定接受：false / 所有候选均未应用${r.persistence_error?'\n保存错误：'+r.persistence_error:''}`;
  }
 }catch(e){$('cal-status').textContent=e.message||'服务未连接'}finally{setTimeout(calibration,500)}
}
$('cal-start').onclick=async()=>{try{$('cal-start').disabled=true;const response=await fetch('/api/calibration/start',{method:'POST'});if(!response.ok&&response.status!==409)throw Error('无法启动采集');$('cal-status').textContent='采集启动，请保持整机静止'}catch(e){$('cal-status').textContent=e.message;$('cal-start').disabled=false}};
window.addEventListener('resize',render);render();live();calibration();

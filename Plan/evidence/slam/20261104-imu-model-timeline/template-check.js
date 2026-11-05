const DATA={};
const $=id=>document.getElementById(id), F=DATA.frames, B=DATA.batches, P=DATA.poses, end=F[F.length-1].end;
const cloud=$('cloud'), trace=$('trajectory'), chart=$('chart'), cursor=$('cursor'), seek=$('seek'), assembly=$('assembly');seek.max=F.length-1;
let frame=0, playing=false, last=0, virtual=0, az=-0.65, elev=0.62, scale=1, dragging=false, ox=0, oy=0;
let batchIndex=-1, forcedBatchIndex=null, sampleIndex=0, sampleTimer=null, modelYaw=.77, modelElev=.43, modelDrag=null;$('batchSeek').max=B.length-1;
const q0=B[0].samples[0].quaternion;
function setup(c){const d=devicePixelRatio||1,w=c.clientWidth,h=c.clientHeight;if(c.width!==Math.round(w*d)||c.height!==Math.round(h*d)){c.width=Math.round(w*d);c.height=Math.round(h*d)}const x=c.getContext('2d');x.setTransform(d,0,0,d,0,0);x.clearRect(0,0,w,h);return [x,w,h]}
function atOrBefore(a,t){let lo=0,hi=a.length;while(lo<hi){let m=(lo+hi)>>1;if(a[m].t<=t)lo=m+1;else hi=m}return lo-1}
function bytes(s){const b=atob(s), a=new Uint8Array(b.length);for(let i=0;i<b.length;i++)a[i]=b.charCodeAt(i);return a}
function pointValues(f){if(!f._points){const b=bytes(f.xyzi);f._points=new Float32Array(b.buffer)}return f._points}
// wxyz device orientation; IMU-to-world and alignment with the housing axes are assumptions.
function qNormalize(q){const n=Math.hypot(...q);return n>0?q.map(x=>x/n):[1,0,0,0]}
function qMultiply(a,b){const [w,x,y,z]=a,[v,i,j,k]=b;return [w*v-x*i-y*j-z*k,w*i+x*v+y*k-z*j,w*j-x*k+y*v+z*i,w*k+x*j-y*i+z*v]}
function qRelative(base,current){const [w,x,y,z]=qNormalize(base);return qMultiply([w,-x,-y,-z],qNormalize(current))}
function qRotate(q,p){const [w,x,y,z]=qNormalize(q), v=qMultiply(qMultiply([w,x,y,z],[0,...p]),[w,-x,-y,-z]);return v.slice(1)}
function drawAssembly(sample){
 const [x,w,h]=setup(assembly), right=[-Math.sin(modelYaw),Math.cos(modelYaw),0], up=[-Math.sin(modelElev)*Math.cos(modelYaw),-Math.sin(modelElev)*Math.sin(modelYaw),Math.cos(modelElev)], eye=[Math.cos(modelElev)*Math.cos(modelYaw),Math.cos(modelElev)*Math.sin(modelYaw),Math.sin(modelElev)], k=Math.min(w,h)/195;
 const dot=(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2], q=sample?qRelative(q0,sample.quaternion):[1,0,0,0];
 const project=(p,rot=true)=>{const v=rot?qRotate(q,p):p;return [w/2+dot(v,right)*k,h*.52-dot(v,up)*k,dot(v,eye)]};
 const line=(a,b,color,rot=false)=>{const u=project(a,rot),v=project(b,rot);x.strokeStyle=color;x.beginPath();x.moveTo(u[0],u[1]);x.lineTo(v[0],v[1]);x.stroke()};
 x.lineWidth=1;for(let i=-100;i<=100;i+=25){line([i,-100,-75],[i,100,-75],'#26394c');line([-100,i,-75],[100,i,-75],'#26394c')}
 const color=g=>g.includes('window')?'#8f713f':g.includes('imu_pcb')?'#298575':g.includes('imu_')?'#85928d':g.includes('usb')||g.includes('connector')?'#a6a6a0':g.includes('led')||g.includes('pin')?'#72ddb8':g.includes('vent')?'#203441':g.includes('plate')?'#535f6b':'#344351';
 const verts=DATA.model.vertices.map(v=>project(v)), faces=DATA.model.faces.map(f=>({p:f.v.map(i=>verts[i]),c:color(f.group)}));faces.forEach(f=>f.d=f.p.reduce((s,p)=>s+p[2],0)/f.p.length);faces.sort((a,b)=>a.d-b.d);
 for(const f of faces){x.beginPath();f.p.forEach((p,i)=>i?x.lineTo(p[0],p[1]):x.moveTo(p[0],p[1]));x.closePath();x.fillStyle=f.c;x.fill();x.strokeStyle='#111b26';x.lineWidth=.55;x.stroke()}
 x.font='12px sans-serif';for(const [p,c,label] of [[[90,0,0],'#ff7d79','X'],[[0,90,0],'#6ee7b7','Y'],[[0,0,90],'#80bfff','Z']]){line([0,0,0],p,c,true);const v=project(p);x.fillStyle=c;x.fillText(label,v[0]+4,v[1])}
 if(!sample){x.fillStyle='#f7c77d';x.fillText('此时刻尚无 IMU 接收批次',12,22)}
}
function drawCloud(){const [x,w,h]=setup(cloud),f=F[frame],d=pointValues(f),radius=+$('range').value, k=Math.min(w,h)*.45/radius*scale;
 const project=(a,b,c)=>{let xx=Math.cos(az)*a-Math.sin(az)*b,yy=Math.sin(az)*a+Math.cos(az)*b;return [w/2+xx*k,h/2-(c*Math.cos(elev)-yy*Math.sin(elev))*k]};
 x.strokeStyle='#23374b';x.lineWidth=1;for(let a=-2;a<=2;a++){x.beginPath();let q=project(-radius,a*radius/2,0),r=project(radius,a*radius/2,0);x.moveTo(...q);x.lineTo(...r);x.stroke();x.beginPath();q=project(a*radius/2,-radius,0);r=project(a*radius/2,radius,0);x.moveTo(...q);x.lineTo(...r);x.stroke()}
 let visible=0;for(let i=0;i<d.length;i+=4){let a=d[i],b=d[i+1],c=d[i+2];if(Math.hypot(a,b,c)>radius)continue;let q=project(a,b,c);if(q[0]<0||q[0]>=w||q[1]<0||q[1]>=h)continue;visible++;x.fillStyle=c>1?'#f5c06c':c< -1?'#56a8ed':'#8ce3ce';x.fillRect(q[0],q[1],2,2)}
 for(const [v,col,label] of [[[radius*.25,0,0],'#ff7d79','X'],[[0,radius*.25,0],'#6ee7b7','Y'],[[0,0,radius*.25],'#80bfff','Z']]){let a=project(0,0,0),b=project(...v);x.strokeStyle=col;x.fillStyle=col;x.lineWidth=2;x.beginPath();x.moveTo(...a);x.lineTo(...b);x.stroke();x.fillText(label,b[0]+3,b[1])}
 $('cloudInfo').textContent=`源帧 ${f.index}（${f.complete?'100 包完整分组':'尾部分组'}），包主机时间 ${f.start.toFixed(3)}–${f.end.toFixed(3)} s；${f.rays} 射线，候选 ${f.eligible}，抽样 ${f.count}，当前视口绘制 ${visible}。`;}
function drawTrajectory(t){const [x,w,h]=setup(trace);const minX=Math.min(...P.map(p=>p.x)),maxX=Math.max(...P.map(p=>p.x)),minY=Math.min(...P.map(p=>p.y)),maxY=Math.max(...P.map(p=>p.y)),span=Math.max(maxX-minX,maxY-minY,1),k=Math.min(w-40,h-40)/span;
 const xy=p=>[w/2+(p.x-(minX+maxX)/2)*k,h/2-(p.y-(minY+maxY)/2)*k];x.strokeStyle='#5b718c';x.beginPath();P.forEach((p,i)=>{let q=xy(p);i?x.lineTo(...q):x.moveTo(...q)});x.stroke();
 let available=t>=P[0].t&&t<=P[P.length-1].t;if(available){let i=atOrBefore(P,t),q=xy(P[i]);x.fillStyle='#ffce65';x.beginPath();x.arc(q[0],q[1],5,0,2*Math.PI);x.fill();$('poseInfo').textContent=`诊断 LIO 当前位姿 ${i+1}/${P.length}；记录时刻 ${P[i].t.toFixed(3)} s，距帧末 ${(t-P[i].t).toFixed(3)} s。`}
 else $('poseInfo').textContent=`当前时刻无历史 LIO 轨迹；仅覆盖 ${P[0].t.toFixed(3)}–${P[P.length-1].t.toFixed(3)} s。`;
 x.fillStyle='#9eafc2';x.fillText(`XY 显示范围约 ${span.toFixed(1)} m`,12,h-10)}
function drawChart(){const [x,w,h]=setup(chart), colors=['#ff7d79','#6ee7b7','#80bfff'], min=-180,max=180,left=38,right=w-12,top=12,bottom=h-24, xp=t=>left+(right-left)*t/end,yp=v=>bottom-(v-min)/(max-min)*(bottom-top);
 x.strokeStyle='#31445b';x.fillStyle='#9eafc2';for(let v=-180;v<=180;v+=90){let y=yp(v);x.beginPath();x.moveTo(left,y);x.lineTo(right,y);x.stroke();x.fillText(v+'°',3,y+4)}
 colors.forEach((col,j)=>{x.strokeStyle=col;x.globalAlpha=.4;x.setLineDash([]);for(const b of B){x.beginPath();x.moveTo(xp(b.t),yp(b.min[j]));x.lineTo(xp(b.t),yp(b.max[j]));x.stroke()}x.globalAlpha=.8;x.setLineDash([4,3]);x.beginPath();B.forEach((b,i)=>{let a=xp(b.t),c=yp(b.euler[j]);i?x.lineTo(a,c):x.moveTo(a,c)});x.stroke();x.setLineDash([]);x.globalAlpha=1});
 x.fillStyle='#9eafc2';x.fillText('0 s',left,h-5);x.fillText(end.toFixed(1)+' s',right-45,h-5)}
function drawGyro(){const [x,w,h]=setup($('gyroChart')),colors=['#ff7d79','#6ee7b7','#80bfff'], left=48,right=w-12,top=12,bottom=h-24,limit=Math.max(1,...B.flatMap(b=>[...b.gyro_min,...b.gyro_max].map(Math.abs))),xp=t=>left+(right-left)*t/end,yp=v=>bottom-(v+limit)/(2*limit)*(bottom-top);
 x.strokeStyle='#31445b';x.fillStyle='#9eafc2';for(const v of [-limit,0,limit]){const y=yp(v);x.beginPath();x.moveTo(left,y);x.lineTo(right,y);x.stroke();x.fillText(v.toFixed(1),2,y+4)}
 colors.forEach((c,j)=>{x.strokeStyle=c;x.globalAlpha=.4;for(const b of B){x.beginPath();x.moveTo(xp(b.t),yp(b.gyro_min[j]));x.lineTo(xp(b.t),yp(b.gyro_max[j]));x.stroke()}x.globalAlpha=.85;x.setLineDash([4,3]);x.beginPath();B.forEach((b,i)=>{const px=xp(b.t),py=yp(b.gyro[j]);i?x.lineTo(px,py):x.moveTo(px,py)});x.stroke();x.setLineDash([]);x.globalAlpha=1});x.fillText('0 s',left,h-5);x.fillText(end.toFixed(1)+' s',right-45,h-5)}
function drawCursor(t){const [x,w,h]=setup(cursor),left=38,right=w-12;x.strokeStyle='#fff';x.setLineDash([4,4]);x.beginPath();x.moveTo(left+(right-left)*t/end,8);x.lineTo(left+(right-left)*t/end,h-20);x.stroke()}
function update(){let f=F[frame], t=f.end;seek.value=frame;$('time').textContent=`${t.toFixed(3)} / ${end.toFixed(3)} s`;
 let i=forcedBatchIndex===null?atOrBefore(B,t):forcedBatchIndex;if(i<0||(forcedBatchIndex===null&&t>B[B.length-1].t)){if(batchIndex!==-1)stopBatch();batchIndex=-1;$('sampleSeek').disabled=true;$('batchPlay').disabled=true;$('sampleLabel').textContent='无数据';$('batchLabel').textContent='按时间匹配：无';$('imuInfo').textContent='当前时刻不在 IMU 主机接收时间覆盖范围内：无 IMU 数据。';drawAssembly(null)}else{let b=B[i];if(batchIndex!==i){batchIndex=i;sampleIndex=b.samples.length-1;stopBatch()}$('batchSeek').value=i;$('batchLabel').textContent=`${forcedBatchIndex===null?'时间匹配':'手动选批'} ${i+1}/${B.length}`;$('sampleSeek').disabled=false;$('batchPlay').disabled=false;$('sampleSeek').max=b.samples.length-1;sampleIndex=Math.min(sampleIndex,b.samples.length-1);$('sampleSeek').value=sampleIndex;let s=b.samples[sampleIndex],fm=a=>a.map(v=>Number(v).toFixed(2)).join(', ');$('sampleLabel').textContent=`帧序 ${sampleIndex+1}/${b.count}`;$('imuInfo').textContent=`IMU 批次 ${i+1}/${B.length} · ${b.count} 帧 · 接收时刻 ${b.t.toFixed(3)} s · 雷达帧末减批次时刻 ${(t-b.t).toFixed(3)} s。当前批内帧序 ${sampleIndex+1}，tid=${s.tid}；欧拉角 [pitch, roll, yaw]（推断度）：${fm(s.euler)}；角速度 XYZ（推断 °/s）：${fm(s.gyro)}；加速度（记录单位 m/s²）：${fm(s.accel)}。`;drawAssembly(s)}
 drawCloud();drawTrajectory(t);drawCursor(t)}
function stopBatch(){if(sampleTimer!==null){clearInterval(sampleTimer);sampleTimer=null}$('batchPlay').textContent='按帧序演示本批'}
$('sampleSeek').oninput=()=>{stopBatch();sampleIndex=+$('sampleSeek').value;update()};
$('batchSeek').oninput=()=>{stopBatch();playing=false;$('play').textContent='▶ 播放';forcedBatchIndex=+$('batchSeek').value;update()};
$('batchPlay').onclick=()=>{if(sampleTimer!==null){stopBatch();return}playing=false;$('play').textContent='▶ 播放';sampleIndex=0;update();$('batchPlay').textContent='停止帧序演示';sampleTimer=setInterval(()=>{if(batchIndex<0||sampleIndex>=B[batchIndex].samples.length-1){stopBatch();return}sampleIndex++;update()},35)};
seek.oninput=()=>{stopBatch();forcedBatchIndex=null;frame=+seek.value;virtual=F[frame].end;update()};$('range').onchange=drawCloud;$('reset').onclick=()=>{az=-.65;elev=.62;scale=1;drawCloud()};$('play').onclick=()=>{stopBatch();forcedBatchIndex=null;if(frame===F.length-1&&!playing){frame=0;virtual=0}playing=!playing;last=0;$('play').textContent=playing?'❚❚ 暂停':'▶ 播放'};
function tick(now){if(playing){if(last)virtual+=Math.min((now-last)/1000,.25)*(+$('speed').value);last=now;while(frame<F.length-1&&F[frame+1].end<=virtual)frame++;if(virtual>=end){frame=F.length-1;playing=false;$('play').textContent='▶ 播放'}update()}requestAnimationFrame(tick)}
cloud.onpointerdown=e=>{dragging=true;ox=e.clientX;oy=e.clientY;cloud.setPointerCapture(e.pointerId)};cloud.onpointerup=()=>dragging=false;cloud.onpointermove=e=>{if(dragging){az+=(e.clientX-ox)*.008;elev=Math.max(-1.4,Math.min(1.4,elev+(e.clientY-oy)*.008));ox=e.clientX;oy=e.clientY;drawCloud()}};cloud.onwheel=e=>{e.preventDefault();scale=Math.max(.3,Math.min(5,scale*Math.exp(-e.deltaY*.001)));drawCloud()};
assembly.onpointerdown=e=>{modelDrag={x:e.clientX,y:e.clientY};assembly.setPointerCapture(e.pointerId)};assembly.onpointerup=()=>modelDrag=null;assembly.onpointercancel=()=>modelDrag=null;assembly.onpointermove=e=>{if(modelDrag){modelYaw-=(e.clientX-modelDrag.x)*.009;modelElev=Math.max(-1.45,Math.min(1.55,modelElev+(e.clientY-modelDrag.y)*.009));modelDrag={x:e.clientX,y:e.clientY};update()}};
window.onresize=()=>{drawChart();drawGyro();update()};drawChart();drawGyro();update();requestAnimationFrame(tick);

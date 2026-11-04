'use strict';
// Reuse the existing fixed ICP and unchanged gate, in a diagnostic process only.
const fs=require('fs'),path=require('path');
const manifest=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const out=process.argv[3],root=manifest.source_root;
const REG=require(path.join(root,'pc_apps/human_replay/registration.js'));
const M=require(path.join(root,'pc_apps/human_replay/registration_method.js'));
const PL=require(path.join(root,'pc_apps/human_replay/human_pipe_lib.js'));
const {cloud}=require(path.join(root,'sidequests/human_fusion_lab_next/real_fall/r04/background_ab.js'));
const reference=cloud(manifest.reference,manifest.R,manifest.T).filter((_,i)=>i%3===0).map(c=>c.buf);
const refmap=PL.build_reference_map(reference);
for(const s of manifest.sessions){
 const file=path.join(out,s.sid+'_registration.json');
 if(fs.existsSync(file))throw Error('Output exists: '+file);
 const cur=cloud(s.sid,manifest.R,manifest.T).map(c=>c.buf);
 const icp=M.fixedICP(PL.build_static_map(cur),refmap);
 const R=M.m3.t(icp.R),t=M.m3.apply(R,...icp.t.map(v=>-v));
 const diagnostics=REG.measure(cur,reference,R,t,M.I3);
 fs.writeFileSync(file,JSON.stringify({sid:s.sid,reference:manifest.reference,R,t,diagnostics,
  gate:diagnostics.gate,official_artifact:false,runtime_eligible:false,
  nominal_leveling_only:true,source_static_may_include_stationary_person:true,
  direction:'action_nominal_to_empty_nominal',reference_frames:'FIT mod3=0 only',
  physical_ground_and_scene_identity:'BLOCKED',code:REG.codeBinding(root)},null,2),{flag:'wx'});
 console.log(s.sid,diagnostics.gate.status,diagnostics.gate.reasons.join(','));
}

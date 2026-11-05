"""Read-only NA70 raw-code census, not a statement of protocol semantics."""
import hashlib, json, platform, subprocess, sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / 'web'), str(ROOT / 'packages/na70_core')]
from backend import decoder
from na70_core.observations import direction_known_from_codes

src = ROOT / 'radar_capture_data/na70_sessions/20261104-170341/raw/lidar.pcap'
out = Path(__file__).parent

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()

def code_masks(c):
    d,p,y,i = c.T
    return {'distance_zero': d==0, 'distance_ffff': d==65535,
            'pitch_ffff': p==65535, 'yaw_ffff': y==65535,
            'any_angle_ffff': (p==65535)|(y==65535),
            'both_angle_ffff': (p==65535)&(y==65535),
            'all_seven_ff': (d==65535)&(p==65535)&(y==65535)&(i==255),
            'distance_positive_any_angle_ffff': (d>0)&((p==65535)|(y==65535)),
            'distance_ffff_angles_not_ffff': (d==65535)&(p!=65535)&(y!=65535),
            'sweep_valid': (d>0)&(p!=65535)&(y!=65535),
            'raybatch_valid': d>0}
scan=decoder.scan_pcap(src)
near_boundaries=[]
frames=[]; sample_keys=['all_seven_ff','distance_ffff_angles_not_ffff','distance_positive_any_angle_ffff']
samples={key:[] for key in sample_keys}
with src.open('rb') as f:
    for idx,frame in enumerate(scan.frames):
        c=decoder.decode_frame_codes(src,frame,source=f)
        masks=code_masks(c)
        decoded=decoder.decode_frame(src,frame,source=f)
        masks['nonfinite_decoded'] = ~np.isfinite(decoded).all(axis=1)
        masks['raybatch_valid'] &= ~masks['nonfinite_decoded']
        masks['raybatch_direction_known'] = direction_known_from_codes(c)
        row={'frame':idx,'ray_count':len(c),'host_start_s':frame['packet_capture_times'][0],
             'host_end_s':frame['packet_capture_times'][-1], **{k:int(m.sum()) for k,m in masks.items()}}
        frames.append(row)
        for packet, dev in enumerate(frame['packet_device_times']):
            if dev is not None and 30 <= round(dev / 10) * 10 <= 120 and abs(dev - round(dev / 10) * 10) <= .004:
                near_boundaries.append({'frame':idx,'packet_in_frame':packet,'absolute_packet_index':int(frame['packet_start_index'])+packet,'device_time_interpreted_s':dev,'all_ff_ray_indices':np.flatnonzero(masks['all_seven_ff'][packet*200:(packet+1)*200]).tolist()})
        for key in sample_keys:
            for ray in np.flatnonzero(masks[key])[:max(0,20-len(samples[key]))]:
                packet=int(ray)//200; point=int(ray)%200
                ref={'frame':idx,'packet_in_frame':packet,'point_in_packet':point,
                     'frame_local_point':int(ray),'absolute_packet_index':int(frame['packet_start_index'])+packet,
                     'pcap_ray_offset':int(frame['offsets'][packet])+point*7,
                     'codes':[int(x) for x in c[ray]],'packet_host_time_s':frame['packet_capture_times'][packet],
                     'packet_device_time_interpreted_s':frame['packet_device_times'][packet]}
                f.seek(max(0,ref['pcap_ray_offset']-14)); ref['surrounding_35_bytes_hex']=f.read(35).hex(); samples[key].append(ref)

def totals(rows):
    return {'frames':len(rows), **{k:sum(r[k] for r in rows) for k in ['ray_count',*code_masks(np.zeros((1,4),dtype=np.uint16)).keys(),'nonfinite_decoded','raybatch_direction_known']}}
paths=['web/backend/decoder.py','web/backend/sweeps.py','packages/na70_core/na70_core/observations.py',
       'packages/na70_core/na70_core/scene.py','packages/na70_core/na70_core/lio_scans.py',
       'tools/build_na70_scene.py','ROS2/src/double_wedge_driver/src/inno_device_lidar_node.cpp']
result={'status':'DIAGNOSTIC_ONLY_UNVERIFIED_PROTOCOL',
        'input':str(src.relative_to(ROOT)),'input_sha256':sha(src),'decoder_version':decoder.DECODER_VERSION,
        'full_session':totals(frames), 'diagnostic_frames_10_through_109_inclusive':totals(frames[10:110]),
        'frame_counts':frames,'first_examples':samples,'packets_near_device_tail_decade_boundaries':near_boundaries,
        'method':'Canonical decoder scan and raw-code accessor, 100-packet frame index; no input modification; count overlapping masks, not exclusive categories.',
        'limitations':['0xFFFF interpretation not independently verified with manufacturer', 'host time is reception time; interpreted device time unvalidated', 'Neither mask proves physical return validity'],
        'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'python':sys.version,'numpy':np.__version__,'platform':platform.platform(),
        'source_sha256':{p:sha(ROOT/p) for p in paths},'script_sha256':sha(Path(__file__))}
(out/'raw-code-census.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
print(json.dumps({k:result[k] for k in ['status','full_session','diagnostic_frames_10_through_109_inclusive','first_examples']},indent=2))




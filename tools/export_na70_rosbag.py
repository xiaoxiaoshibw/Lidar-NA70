"""WSL/Humble: export reference decoding with measured packet-level device times."""
import argparse
import json
from pathlib import Path
import sys
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
from backend.decoder import decode_frame, device_time
import rosbag2_py
from rclpy.serialization import serialize_message
from sensor_msgs.msg import PointCloud2, PointField

parser=argparse.ArgumentParser()
parser.add_argument('capture',type=Path)
parser.add_argument('output',type=Path)
parser.add_argument('--limit',type=int,default=0)
parser.add_argument('--reject-invalid-time',action='store_true')
parser.add_argument('--bootstrap',type=int,default=0)
parser.add_argument('--bootstrap-snapshot',action='store_true')
args=parser.parse_args()
frames=json.loads((args.capture/'frames.json').read_text())
if args.limit: frames=frames[:args.limit]
writer=rosbag2_py.SequentialWriter()
writer.open(rosbag2_py.StorageOptions(uri=str(args.output),storage_id='sqlite3'),rosbag2_py.ConverterOptions('',''))
writer.create_topic(rosbag2_py.TopicMetadata(name='/na70/points',type='sensor_msgs/msg/PointCloud2',serialization_format='cdr'))
previous=None
rejected=[]
written=0
bootstrap_arrays=[]
bootstrap_stamp=None
if not 0 <= args.bootstrap <= 30:
    raise ValueError('bootstrap must stay inside the existing 30-frame budget')
with (args.capture/'capture.pcap').open('rb') as source:
    for i,frame in enumerate(frames):
        points=decode_frame(args.capture/'capture.pcap',frame,source=source)
        times=[]
        for offset in frame['offsets']:
            source.seek(offset);times.append(device_time(source.read(1410)))
        if any(t is None for t in times) or np.any(np.diff(times)<0) or (previous is not None and times[0]<=previous):
            if not args.reject_invalid_time:
                raise ValueError(f'Invalid device timing in frame {i}')
            rejected.append(dict(frame=i,state='timestamp_invalid',packet_times=times))
            print('REJECT timestamp_invalid',i,flush=True)
            continue
        # Packet timestamps may tie across a frame boundary at 1 ms precision.
        # Frame start times must still be strictly increasing.
        previous=times[0]
        stamp=round(times[0]*1e9)
        array=np.empty(len(points),dtype=[('x','<f4'),('y','<f4'),('z','<f4'),('intensity','<f4'),('time','<f8')])
        for axis,name in enumerate(('x','y','z','intensity')): array[name]=points[:,axis]
        array['time']=np.repeat(np.array(times)-times[0],200)
        keep=np.isfinite(points).all(axis=1)&(np.linalg.norm(points[:,:3],axis=1)>0)
        array=array[keep]
        if i < args.bootstrap:
            if bootstrap_stamp is None: bootstrap_stamp=stamp
            array['time'] += (stamp-bootstrap_stamp)/1e9
            bootstrap_arrays.append(array)
            if i+1 < args.bootstrap: continue
            array=np.concatenate(bootstrap_arrays)
            xyz=np.column_stack([array[name] for name in ('x','y','z')])
            # Retain measured points and packet-time correspondence, no invented time.
            _,indices=np.unique(np.floor(xyz/.2).astype(np.int64),axis=0,return_index=True)
            indices=np.sort(indices)
            if len(indices)>40000: indices=indices[np.linspace(0,len(indices)-1,40000,dtype=int)]
            array=array[indices];stamp=bootstrap_stamp;bootstrap_arrays.clear()
            if args.bootstrap_snapshot:
                stamp=round(times[-1]*1e9)
                array['time']=0.0  # stationary composite, explicitly NOT measured point timestamps
        msg=PointCloud2()
        msg.header.frame_id='na70_lidar';msg.header.stamp.sec=stamp//1000000000;msg.header.stamp.nanosec=stamp%1000000000
        msg.height=1;msg.width=len(array);msg.is_bigendian=False;msg.is_dense=True;msg.point_step=24;msg.row_step=24*len(array)
        msg.fields=[PointField(name=name,offset=axis*4,datatype=PointField.FLOAT32,count=1) for axis,name in enumerate(('x','y','z','intensity'))]
        msg.fields.append(PointField(name='time',offset=16,datatype=PointField.FLOAT64,count=1))
        msg.data=array.tobytes()
        writer.write('/na70/points',serialize_message(msg),stamp)
        written+=1
        if i%100==0: print(i,flush=True)
del writer
(args.output/'timing_provenance.json').write_text(json.dumps(dict(source='NA70 measured packet device timestamp',resolution_seconds=.001,points_per_packet=200,within_packet_timing='unknown; all 200 points share packet time',imu_used=False,frame_count=len(frames),written_frames=written,rejected_frames=rejected,bootstrap_frames=args.bootstrap,bootstrap_declaration='User confirmed first 60 seconds stationary' if args.bootstrap else None,bootstrap_snapshot=args.bootstrap_snapshot,bootstrap_time_semantics='composite stationary map snapshot; zero relative times are an explicit zero-motion assumption, not measured point timestamps' if args.bootstrap_snapshot else 'measured packet offsets'),indent=2))

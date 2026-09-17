"""SYNTHETIC preparation checks, not real motion acceptance."""
import json
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.preflight import metadata, evaluate, STATIC_SHA
from test_mapping_api import upload, start_job, wait_job

@pytest.mark.parametrize("change,diagnostic,status", [
    ({"mode":"fixed"},{},"固定视点静态点云"),
    ({"translation":False},{},"缺少真实移动"),
    ({},{"sha256":STATIC_SHA},"缺少真实移动"),
    ({},{"timestamp_backwards":[2]},"时间戳异常"),
    ({"near_geometry":False},{},"近场几何不足"),
    ({},{},"可进入建图"),
    ({"movement_start":120},{},"需要重新采集"),
])
def test_status(change, diagnostic, status):
    m=metadata(dict(mode="manual",scene_static=True,near_geometry=True,translation=True,
                    start_return=True,no_imu=True,movement_start=30,route="SYNTHETIC translation",scene="SYNTHETIC walls"))
    m.update(change)
    d=dict(frame_count=1000,duration=100,invalid_packets=0)
    d.update(diagnostic)
    result=evaluate(d,m)
    assert result["status"]==status
    assert result["real_movement_acceptance"]=="NOT_EVALUATED"

@pytest.mark.parametrize("value", [{"no_imu":"yes"},{"movement_start":float('nan')},{"initialization_frames":-1},{"unknown":True}])
def test_invalid_metadata(value):
    with pytest.raises(ValueError): metadata(value)

def test_preflight_job_snapshot_and_restart(tmp_path):
    root=tmp_path/'store'
    with TestClient(create_app(root)) as client:
        identifier, meta=upload(client,tmp_path/'synthetic.pcap',frames=2)
        assert meta['preflight_diagnostics']['size']>0
        record=metadata(dict(mode='fixed',route='SYNTHETIC stationary'))
        result=client.put(f'/api/captures/{identifier}/preflight',json=record)
        assert result.status_code==200, result.text
        job=wait_job(client,start_job(client,identifier)['job_id'])
        assert job['capture_preflight']==result.json()
        validation=json.loads((root/identifier/'mapping'/job['job_id']/'validation.json').read_text('utf-8'))
        assert validation['capture_preflight']==result.json()
    with TestClient(create_app(root)) as client:
        assert client.get(f"/api/mapping/jobs/{job['job_id']}").json()['capture_preflight']==result.json()


def test_stationary_initialization_not_drift_evidence():
    from backend.capture_validation import summarize
    import numpy as np
    records=[dict(state='reference',pose=np.eye(4).tolist(),timestamp=i,segment_id=0) for i in range(3)]
    result=summarize(records,dict(metadata=dict(movement_start=3)))
    assert result['stationary_drift']['translation_m'] is None
    assert result['registered_frames']==0

def test_formal_drift_and_failure_coverage():
    from backend.capture_validation import summarize
    import numpy as np
    records=[]
    for i in range(5):
        pose=np.eye(4); pose[0,3]=i*.1
        records.append(dict(state='accepted' if i != 3 else 'tracking_lost',pose=pose.tolist(),timestamp=i,segment_id=0))
    result=summarize(records,dict(metadata=dict(movement_start=2)))
    assert result['stationary_drift']['translation_m']==pytest.approx(.1)
    assert result['movement_registered_ratio']==pytest.approx(2/3)
    assert result['movement_max_continuous_registered_seconds']==0


def test_packet_backwards_location_preserved(tmp_path):
    from backend.decoder import scan_pcap
    from test_decoder import make_pcap, payload
    path=make_pcap(tmp_path/'SYNTHETIC-time.pcap',[payload()]*3,timestamps=[0,.2,.1])
    scan=scan_pcap(path)
    anomaly=scan.frames[0]['packet_time_anomalies'][0]
    assert anomaly['packet']==2
    assert anomaly['kind']=='capture_backwards'
    assert anomaly['time']<anomaly['previous']

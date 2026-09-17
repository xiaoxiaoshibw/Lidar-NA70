"""Read existing mapping chunks; write a new immutable geometry evidence file.

Contract: JSON object with regions (each with name, segment_id, minimum, maximum,
origin, normal, histogram_bin_m, max_thickness_m, min_layer_separation_m,
min_peak_fraction, min_points). Thresholds must be frozen before acceptance.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'web'))
from backend.geometry_validation import measure

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('job',type=Path); p.add_argument('contract',type=Path); p.add_argument('output',type=Path)
    a=p.parse_args()
    if a.output.exists(): p.error('output exists; choose a new evidence path')
    contract=json.loads(a.contract.read_text('utf-8'))
    rows=[]; hashes={}
    for path in sorted((a.job/'map_chunks').glob('*.jsonl')):
        hashes[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        rows.extend(json.loads(line) for line in path.read_text('utf-8').splitlines() if line)
    points=np.asarray(rows,dtype=float).reshape((-1,8))
    results=[]
    for region in contract['regions']:
        selected=points[points[:,7]==region['segment_id'],:3]
        results.append(dict(name=region['name'],segment_id=region['segment_id'],measurement=measure(selected,region)))
    result=dict(stage='point_cloud_odometry',r7='NOT_EVALUATED',contract=contract,
                contract_sha256=hashlib.sha256(a.contract.read_bytes()).hexdigest(),
                input_chunks_sha256=hashes,regions=results)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

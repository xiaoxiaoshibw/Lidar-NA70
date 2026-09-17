"""Measurements against an explicitly supplied fixed-structure contract."""
import numpy as np
from scipy.signal import find_peaks

def measure(points, contract):
    xyz=np.asarray(points,dtype=float)
    if xyz.ndim != 2 or xyz.shape[1] != 3 or not np.isfinite(xyz).all():
        raise ValueError("points must be finite Nx3")
    normal=np.asarray(contract['normal'],dtype=float)
    low=np.asarray(contract['minimum'],dtype=float)
    high=np.asarray(contract['maximum'],dtype=float)
    origin=np.asarray(contract['origin'],dtype=float)
    if any(v.shape != (3,) or not np.isfinite(v).all() for v in (normal,low,high,origin)) or np.linalg.norm(normal)==0 or np.any(high<=low):
        raise ValueError("invalid ROI/plane")
    width=contract['histogram_bin_m']
    limit=contract['max_thickness_m']
    separation=contract['min_layer_separation_m']
    mass=contract['min_peak_fraction']
    minimum=contract['min_points']
    if not all(type(v) in (int,float) and np.isfinite(v) and v>0 for v in (width,limit,separation,mass,minimum)) or mass>=1 or type(minimum) is not int:
        raise ValueError("invalid frozen geometry thresholds")
    selected=xyz[np.all((xyz>=low)&(xyz<=high),axis=1)]
    if len(selected)<minimum:
        return dict(status='NOT_EVALUATED',points=len(selected),reason='insufficient ROI points')
    distances=(selected-origin) @ (normal/np.linalg.norm(normal))
    q05,q95=np.percentile(distances,[5,95])
    bins=int(np.ceil((distances.max()-distances.min())/width))+1
    if bins>100000:
        raise ValueError("histogram budget exceeded; review ROI and bin width")
    histogram,edges=np.histogram(distances,bins=np.arange(bins+1)*width+distances.min()-width/2)
    padded=np.pad(histogram,(1,1))
    peaks,_=find_peaks(padded,height=mass*len(distances),distance=max(1,int(np.ceil(separation/width))))
    centres=[float((edges[i-1]+edges[i])/2) for i in peaks if 0<i<=len(histogram)]
    thickness=float(q95-q05)
    return dict(status='MEASURED',points=len(selected),thickness_p95_p05_m=thickness,
                thickness_within_frozen_limit=thickness<=limit,
                layer_peak_offsets_m=centres,double_layer_candidate=len(centres)>1,
                note='ROI and plane are supplied; peaks are candidates, not fixed-object semantic truth or R7 PASS')

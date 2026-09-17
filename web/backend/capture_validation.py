"""Evidence summaries; no inferred route, closure truth or automatic R7 PASS."""
import numpy as np

def summarize(records, preflight):
    m = (preflight or {}).get("metadata") or {}
    moving_start = m.get("movement_start")
    registered = [r for r in records if r["state"] == "accepted"]
    movement = [r for r in records if moving_start is not None and r.get("timestamp") is not None and r["timestamp"] >= moving_start]
    stationary = [r for r in registered if moving_start is not None and r["timestamp"] < moving_start and r.get("pose") is not None]
    drift = dict(translation_m=None, rotation_deg=None, reason="缺少同坐标段内的至少两帧正式静止配准；初始化恒等位姿不计入漂移")
    if len(stationary)>1 and len({r["segment_id"] for r in stationary})==1:
        poses = [np.asarray(r["pose"]) for r in stationary]
        drift = dict(translation_m=max(float(np.linalg.norm(p[:3,3]-poses[0][:3,3])) for p in poses),
                     rotation_deg=max(float(np.degrees(np.arccos(np.clip((np.trace(poses[0][:3,:3].T @ p[:3,:3])-1)/2,-1,1)))) for p in poses),
                     reason="用户声明静止区间内正式配准相对首个正式位姿的最大漂移")
    longest=0.0
    start=None
    segment=None
    for r in movement:
        if r["state"] != "accepted":
            start=None
            continue
        if start is None or segment != r["segment_id"]:
            start=r["timestamp"]
            segment=r["segment_id"]
        longest=max(longest,r["timestamp"]-start)
    return dict(status="NOT_EVALUATED", stationary_drift=drift,
                registered_frames=len(registered),
                movement_registered_ratio=sum(r["state"]=="accepted" for r in movement)/len(movement) if movement else None,
                movement_max_continuous_registered_seconds=longest if movement else None,
                geometry=dict(wall_thickness_m=None,double_wall=None,fixed_object_ghosting=None,
                              reason="需要固定结构ROI和冻结标准；地图存在或点数非零不代表几何通过"),
                return_to_start_acceptance="NOT_EVALUATED: needs recorded return and frozen tolerance",
                r7="BLOCKED: requires independent real movement and geometry evidence")

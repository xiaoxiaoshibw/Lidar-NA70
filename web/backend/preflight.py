"""Capture preparation diagnostics; never a registration or R7 acceptance engine."""
from pathlib import Path
import json
import numpy as np
from scipy.spatial import cKDTree
from .decoder import decode_frame, decode_frame_codes

STATIC_SHA = "5ce9d5999051ae9ee6f42d067c70e50aaa3fa8272634cdf8ad64f82657673409"

def metadata(value=None):
    defaults = dict(mode="unknown", scene_static=False, near_geometry=False,
                    translation=False, start_return=False, no_imu=False,
                    movement_start=None, return_time=None, pauses="", route="",
                    scene="", device="", initialization_frames=0)
    if value is None:
        return defaults
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise ValueError("采集元数据字段无效")
    defaults.update(value)
    if defaults["mode"] not in ("unknown", "fixed", "manual"):
        raise ValueError("采集模式无效")
    for key in ("scene_static", "near_geometry", "translation", "start_return", "no_imu"):
        if type(defaults[key]) is not bool:
            raise ValueError(key + " 必须为布尔值")
    for key in ("movement_start", "return_time"):
        v = defaults[key]
        if v is not None and (type(v) not in (int, float) or not np.isfinite(v) or v < 0):
            raise ValueError(key + " 必须为非负秒数")
    for key in ("pauses", "route", "scene", "device"):
        if not isinstance(defaults[key], str) or len(defaults[key]) > 4000:
            raise ValueError(key + " 文本无效")
    v = defaults["initialization_frames"]
    if type(v) is not int or not 0 <= v <= 300:
        raise ValueError("静止初始化帧数无效")
    return defaults

def diagnostics(path, frames, meta):
    cfg = json.loads(Path(__file__).with_suffix('.yaml').read_text('utf-8'))
    sampled = []
    rays = {}
    previous = None
    overlaps = []
    for i in np.unique(np.linspace(0, max(0, len(frames)-1), min(cfg["sample_frames"], len(frames)), dtype=int)):
        xyz = decode_frame(path, frames[int(i)])[:, :3]
        xyz = xyz[np.isfinite(xyz).all(axis=1) & (np.linalg.norm(xyz, axis=1) > 0)]
        full_xyz = xyz
        xyz = xyz[::max(1, int(np.ceil(len(xyz)/cfg["sample_points"])))]
        codes = decode_frame_codes(path, frames[int(i)])
        for distance, pitch, yaw, _ in codes:
            if distance:
                rays.setdefault((int(pitch), int(yaw)), []).append(float(distance)*0.016)
        if previous is not None and len(previous) and len(xyz):
            distances = cKDTree(previous).query(xyz)[0]
            overlaps.append(dict(to_frame=int(i), nearest_distance_median_m=float(np.median(distances)),
                                 note="sampled frame distances; not motion or registration coverage"))
        previous = xyz
        spacing = float(np.median(cKDTree(full_xyz).query(xyz, k=2)[0][:, 1])) if len(xyz)>1 else None
        sampled.append(dict(frame=int(i), points=len(full_xyz), sampled_points=len(xyz), spacing_m=spacing,
                            near_points=int(np.count_nonzero(np.linalg.norm(full_xyz,axis=1)<=cfg["near_range_m"])),
                            range_p25_m=float(np.percentile(np.linalg.norm(xyz, axis=1),25)) if len(xyz) else None))
    backwards = [i for i in range(1,len(frames)) if frames[i]["capture_time"] <= frames[i-1]["capture_time"]]
    device_backwards = [i for i in range(1,len(frames)) if frames[i].get("device_time") is not None and frames[i-1].get("device_time") is not None and frames[i]["device_time"] < frames[i-1]["device_time"]]
    repeated = [float(np.std(values)) for values in rays.values() if len(values)>1]
    packet_anomalies = [dict(frame=i, **entry) for i,f in enumerate(frames) for entry in f.get("packet_time_anomalies",[])]
    return dict(config=cfg, packet_time_anomalies=packet_anomalies,
                packet_time_diagnostics_available=all("packet_time_anomalies" in f for f in frames),
                repeated_ray_range_std_m=float(np.median(repeated)) if repeated else None,
                repeated_ray_count=len(repeated), sampled_frame_distances=overlaps,
                near_geometry_hint="近距点只提示距离，固定结构及多方向约束须现场确认",
                sha256=meta.get("sha256"), size=path.stat().st_size, frame_count=len(frames),
                duration=meta.get("duration",0), fps=len(frames)/meta["duration"] if meta.get("duration",0)>0 else None,
                valid_packets=meta.get("valid_packets",0), invalid_packets=meta.get("invalid_packets",0),
                warnings=meta.get("warnings",[]), timestamp_backwards=backwards,
                device_timestamp_backwards=device_backwards, samples=sampled,
                stationary_noise=None, stationary_noise_reason="需要可信静止区间和重复射线对应；帧间差异不能作为噪声或真实运动真值",
                motion_evidence="user_record_only; point_cloud_motion_not_verified")

def evaluate(d, m):
    d = dict(d)
    if m["mode"] == "fixed":
        d["stationary_noise"] = d.get("repeated_ray_range_std_m")
        d["stationary_noise_reason"] = "声明固定视点下抽样重复射线距离标准差中位数；包括动态干扰，不是传感器标定精度"
    reasons=[]
    status="可进入建图"
    if not d.get("frame_count") or d.get("invalid_packets",0):
        status="需要重新采集"; reasons.append("空数据或异常包，先检查采集完整性")
    elif d.get("packet_time_anomalies") or d.get("timestamp_backwards") or d.get("device_timestamp_backwards") or any("时间" in w for w in d.get("warnings",[])):
        status="时间戳异常"; reasons.append("设备或采集时间异常，不能进行真实移动验收")
    elif m["mode"] == "fixed":
        status="固定视点静态点云"; reasons.append("仅用于固定视点与静止基线")
    elif d.get("sha256") == STATIC_SHA or not m["translation"] or m["movement_start"] is None or not m["route"].strip():
        status="缺少真实移动"; reasons.append("缺少实际平移记录，或输入为已确认全静止的 006")
    elif not m["near_geometry"] or (d.get("samples") and not any(s.get("near_points", 0) for s in d["samples"])):
        status="近场几何不足"; reasons.append("未确认近处固定结构；距离统计不能识别固定物体")
    elif not all(m[k] for k in ("scene_static","no_imu","start_return")) or not m["scene"].strip() or m["mode"] != "manual":
        status="需要重新采集"; reasons.append("采集检查项或场景记录不完整")
    elif m["movement_start"] >= d["duration"] or (m["return_time"] is not None and not m["movement_start"] < m["return_time"] <= d["duration"]):
        status="需要重新采集"; reasons.append("路线时间超出采集或顺序无效")
    else:
        reasons.append("采集声明完整，可运行点云里程计；真实运动与R7仍须轨迹和地图证据核验")
    if d.get("sha256") == STATIC_SHA:
        reasons.append("006 的 SHA-256 与已确认全程静止基线一致；不能评价真实移动定位")
    return dict(version="capture-preflight-v1", status=status, reasons=reasons,
                can_enter_mapping=status=="可进入建图", real_movement_acceptance="NOT_EVALUATED",
                diagnostics=d, metadata=m)

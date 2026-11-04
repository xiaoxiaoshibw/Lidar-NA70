"""Read-only low-pose experiment; protected points remain UNKNOWN, never human GT."""
import argparse
import hashlib
import importlib.util
import json
import platform
import subprocess
import sys
from pathlib import Path

import numpy as np
import yaml
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent


def read(p):
    return json.loads(Path(p).read_text(encoding='utf8'))


def sha(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def dump(p, obj):
    with Path(p).open('x', encoding='utf8') as f:
        json.dump(obj, f, ensure_ascii=False, allow_nan=False)


def masks(p, distance, tolerance, cfg):
    finite = np.isfinite(p).all(axis=1) & np.isfinite(distance)
    domain = finite & (p[:, 2] >= cfg['z_min_m']) & (p[:, 2] <= cfg['z_max_m'])
    frozen = domain & (distance > tolerance)
    low = domain & (p[:, 2] < cfg['low_band_upper_m'])
    # Explicit abstention: no evidence here distinguishes a floor return from body contact.
    rescued = low & ~frozen
    return domain, frozen, rescued, frozen | rescued


def load(root, sid):
    folder = root / 'captures/remote' / sid
    m = read(folder / 'meta.json')
    if m['session_id'] != sid or m['point_stride_bytes'] != 28:
        raise ValueError('Wrong source contract')
    if (folder / 'points.bin').stat().st_size != m['total_points'] * 28:
        raise ValueError('Truncated source')
    end = 0
    stamps = []
    for f in m['frames']:
        if f['offset_points'] != end or f['count_points'] < 0:
            raise ValueError('Bad frame offset')
        end += f['count_points']
        stamps.append(f['stamp_sec'] + f['stamp_nanosec'] * 1e-9)
    if end != m['total_points'] or not np.isfinite(stamps).all() or np.any(np.diff(stamps) <= 0):
        raise ValueError('Bad source time')
    buf = np.memmap(folder / 'points.bin', dtype='u1', mode='r')
    xyz = np.ndarray((m['total_points'], 3), dtype='<f4', buffer=buf, strides=(28, 4))
    return m, xyz, {n: sha(folder / n) for n in ['meta.json', 'points.bin']}


def frame(raw, f, R, T, cfg, alignment=None):
    p = raw[f['offset_points']:f['offset_points'] + f['count_points']].astype('f8')
    radius = np.linalg.norm(p, axis=1)
    valid = np.isfinite(p).all(axis=1) & np.any(p != 0, axis=1)
    valid &= (radius > cfg['range_min_m']) & (radius < cfg['range_max_m'])
    q = p @ R.T + T
    if alignment:
        q = q @ np.array(alignment['R']).reshape(3, 3).T + alignment['t']
    return q, valid


def sample(p, ids, limit):
    ix = np.linspace(0, len(ids)-1, min(limit, len(ids)), dtype=int) if len(ids) else []
    return {'xyz': np.round(p[ids[ix]], 4).tolist(), 'source_indices': ids[ix].tolist(), 'count': len(ids)}


def main(out):
    out.mkdir(parents=True, exist_ok=False)
    cfg = yaml.safe_load((HERE / 'config.yaml').read_text(encoding='utf8'))
    root = Path(cfg['source_root'])
    corefile = root / cfg['nominal_transform_source']
    spec = importlib.util.spec_from_file_location('frozen_pointset_core', corefile)
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    R, T = core.R, core.T
    protected = [corefile, HERE/'config.yaml', HERE/'run.py', HERE/'register.js']
    protected += [root/'pc_apps/human_replay'/n for n in ['registration.js', 'registration_method.js', 'human_pipe_lib.js', 'human_replay_lib.js']]
    baseline = {str(p): sha(p) for p in protected}
    records = []
    for sid, name, count in cfg['sessions']:
        m, _, hashes = load(root, sid)
        assert len(m['frames']) == count
        records.append({'sid': sid, 'name': name, 'frames': count, 'duration_sec': m['duration_sec'],
                        'source_sha256': hashes, 'human_identity_gt': 'MISSING',
                        'name_binding': 'USER_TABLE_UNIQUE_FRAME_COUNT_AND_DURATION; metadata name is corrupted'})
    refmeta, raw, hashes = load(root, cfg['reference'])
    manifest = {'source_root': str(root), 'sessions': records, 'reference': cfg['reference'],
                'reference_sha256': hashes, 'R': R.tolist(), 'T': T.tolist(), 'config': cfg,
                'coordinate_status': 'NOMINAL_OLD_REF_RT_NOT_PHYSICAL_GROUND_CALIBRATION',
                'environment': {'python': sys.version, 'platform': platform.platform(), 'numpy': np.__version__},
                'command': sys.argv, 'input_methods_sha256': baseline,
                'skills': ['ponytail', 'scientific-toolkit-skill']}
    dump(out/'manifest.json', manifest)
    print('manifest: 8 sessions, 1934 frames', flush=True)
    regcmd = ['node', str(HERE/'register.js'), str(out/'manifest.json'), str(out)]
    with (out/'registration.log').open('x', encoding='utf8') as log:
        result = subprocess.run(regcmd, stdout=log, stderr=subprocess.STDOUT, text=True)
    dump(out/'registration_run.json', {'command': regcmd, 'exit_code': result.returncode})
    if result.returncode:
        raise RuntimeError('Registration failed; see preserved registration.log')
    print('registration diagnostics complete', flush=True)
    bank = {}
    for fi in range(0, len(refmeta['frames']), 3):
        p, valid = frame(raw, refmeta['frames'][fi], R, T, cfg)
        valid &= (p[:, 2] >= cfg['z_min_m']) & (p[:, 2] <= cfg['z_max_m'])
        keys, centers, _, _ = core.voxelize(p[valid], cfg['voxel_m'])
        for k, q in zip(keys, centers):
            key = tuple(k)
            total, n = bank.get(key, (np.zeros(3), 0))
            bank[key] = (total + q, n + 1)
    fitcount = len(range(0, len(refmeta['frames']), 3))
    bg = np.array([p/n for p, n in bank.values() if n >= np.ceil(fitcount*cfg['background_min_frame_fraction'])])
    tree = cKDTree(bg)
    calibration = []
    for fi in range(1, len(refmeta['frames']), 3):
        p, valid = frame(raw, refmeta['frames'][fi], R, T, cfg)
        domain, _, _, _ = masks(p, np.zeros(len(p)), 0, cfg)
        calibration.append(tree.query(p[valid & domain])[0])
    measured = float(np.quantile(np.concatenate(calibration), cfg['calibration_quantile']))
    tolerance = min(measured + cfg['voxel_m']/2, cfg['tolerance_cap_m'])
    dump(out/'background.json', {'source_sha256': hashes, 'fit': list(range(0,len(refmeta['frames']),3)),
         'calibration': list(range(1,len(refmeta['frames']),3)), 'holdout': list(range(2,len(refmeta['frames']),3)),
         'calibration_quantile_m': measured, 'tolerance_m': tolerance, 'static_voxels': len(bg),
         'cap_reached': measured+cfg['voxel_m']/2 > cfg['tolerance_cap_m'],
         'ground_identity': 'UNKNOWN', 'never_updated_from_action': True})
    np.savez_compressed(out/'background.npz', xyz=bg)
    summaries, queue = [], []
    all_sessions = records + [{'sid': cfg['reference'], 'name':'空场独立留帧', 'frames':len(refmeta['frames'])}]
    for rec in all_sessions:
        sid = rec['sid']
        m, raw, source_hash = load(root, sid)
        registration = read(out/(sid+'_registration.json')) if sid != cfg['reference'] else None
        # A rejected transform may be visualized as a hypothesis, never enabled by a loader.
        target = out/sid
        target.mkdir()
        totals = dict(domain=0, low=0, frozen=0, low_frozen=0, rescued_unknown=0, protected=0)
        frame_metrics, members, offsets = [], [], [0]
        fis = list(range(2,len(m['frames']),3)) if sid == cfg['reference'] else list(range(len(m['frames'])))
        for fi in fis:
            p, valid = frame(raw, m['frames'][fi], R, T, cfg, registration)
            distance = np.full(len(p), np.inf)
            distance[valid] = tree.query(p[valid])[0]
            domain, frozen, rescue, protected_mask = masks(p,distance,tolerance,cfg)
            domain &= valid; frozen &= valid; rescue &= valid; protected_mask &= valid
            low = domain & (p[:,2] < cfg['low_band_upper_m'])
            counts = {k:int(np.count_nonzero(v)) for k,v in dict(domain=domain,low=low,frozen=frozen,
                     low_frozen=frozen & low,rescued_unknown=rescue,protected=protected_mask).items()}
            for k,v in counts.items(): totals[k] += v
            members.append(np.flatnonzero(protected_mask).astype('u4'));offsets.append(offsets[-1]+counts['protected'])
            row = {'sid':sid,'frame':fi,'device_time':m['frames'][fi]['stamp_sec']+m['frames'][fi]['stamp_nanosec']*1e-9,
                   **counts, 'reviewed_person_denominator':None, 'human_precision':None,
                   'identity':'UNKNOWN_NO_POINT_GT', 'alignment_gate':registration['gate']['status'] if registration else 'SAME_RECORDING_HOLDOUT'}
            frame_metrics.append(row)
            dump(target/f'{fi:04d}.json', {**row,'source_sha256':source_hash,
                 'context':sample(p,np.flatnonzero(domain),cfg['display_max_points']),
                 'frozen_points':sample(p,np.flatnonzero(frozen),cfg['display_max_points']),
                 'protected_unknown':sample(p,np.flatnonzero(rescue),cfg['display_max_points']),
                 'note':'Amber points include floor/furniture/body UNKNOWN; no anatomical skeleton or confirmed event'})
        np.savez_compressed(target/'retained_source_indices.npz',frames=np.array(fis),offsets=np.array(offsets),indices=np.concatenate(members))
        dump(target/'frame_metrics.json',frame_metrics)
        # Review key start/middle/end plus the largest rescue and smallest frozen-low fractions.
        picks = {fis[0],fis[len(fis)//2],fis[-1],max(frame_metrics,key=lambda r:r['rescued_unknown'])['frame'],
                 min(frame_metrics,key=lambda r:r['low_frozen']/max(1,r['low']))['frame']}
        for fi in sorted(picks): queue.append({'sid':sid,'frame':fi,'status':'PROPOSED','identity':'UNKNOWN',
             'review':['human/furniture/floor point identity','ground plane and low band','registration scene consistency'],
             'frame_file':f'{sid}/{fi:04d}.json','source_sha256':source_hash})
        summary={**rec, 'evaluated_frames':len(fis),'totals':totals,'registration_gate':registration['gate'] if registration else None,
                 'human_retention':None,'human_precision':None,'qualification':'BLOCKED_NO_IDENTITY_GT_OR_PHYSICAL_GROUND'}
        summaries.append(summary)
        print(sid, len(fis), 'frames; rescued UNKNOWN',totals['rescued_unknown'],flush=True)
    dump(out/'metrics.json',{'sessions':summaries,'review_queue':queue,'runtime_eligible':False,
         'confirmed_events':None,'anatomical_joints':None,'low_retention_is_set_construction_not_accuracy':True})
    after={str(p):sha(p) for p in protected}
    for rec in records:
        _, _, current = load(root,rec['sid'])
        if current != rec['source_sha256']: raise ValueError('Input changed')
    _, _, current=load(root,cfg['reference'])
    dump(out/'preservation.json',{'protected_code_unchanged':after==baseline,'action_input_hashes_unchanged':True,
                                 'reference_hashes_unchanged':current==hashes})


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--out',required=True)
    main(Path(p.parse_args().out).resolve())

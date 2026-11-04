"""Partial reviewed S8 regression, separate from eight unlabeled action recordings."""
import argparse
import importlib.util
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
import yaml
from run import read, dump, load, masks, frame, sha, HERE


def main(out):
    cfg=yaml.safe_load((HERE/'config.yaml').read_text(encoding='utf8'))
    root=Path(cfg['source_root']);ps=root/'sidequests/human_fusion_lab_next/pointset/r01'
    spec=importlib.util.spec_from_file_location('core_s8',ps/'core.py')
    core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)
    sid='cap_20261010_180557';meta,raw,hashes=load(root,sid)
    cat=read(ps/'output/catalog.json')
    rec=next(s for s in cat['sessions'] if s['session_id']==sid)
    assert rec['source_sha256']=={'meta':hashes['meta.json'],'points':hashes['points.bin']}
    gtpath=root/'pc_apps/human_replay/gt_store'/sid/'GT_V1.json'
    gt=read(gtpath)
    assert gt['session_id']==sid and gt['review_status']=='reviewed'
    assert gt['session_sha256']==rec['source_sha256']
    bg=read(ps/'output/background.json')
    _,_,refhash=load(root,bg['source_session'])
    assert bg['source_sha']=={'meta':refhash['meta.json'],'points':refhash['points.bin']}
    tree=cKDTree(np.load(ps/'output/background.npz')['xyz'])
    sums={k:0 for k in ['person','low_person','frozen_person','frozen_low_person','protected_person','protected_low_person','rescued_unknown','domain_low']}
    rows=[]
    for fi,f in enumerate(meta['frames']):
        old=read(ps/'output'/sid/f'{fi:04d}.json')
        if not old['reference']:continue
        ids=np.array(sorted(set(i for obj in old['reference'] for i in obj['source_indices'])),dtype=int)
        original=raw[f['offset_points']:f['offset_points']+f['count_points']]
        valid_ids=np.flatnonzero(core.valid_raw(original))
        expected=set()
        for oid in gt['frames_objects'].get(str(fi),[]):
            obj=gt['objects'][oid]
            if obj['class_name']=='PERSON':
                expected.update(valid_ids[core.decode_indices(obj['raw_point_indices'])].tolist())
        assert expected==set(ids.tolist()), 'Reference no longer matches bound GT'
        p,valid=frame(raw,f,core.R,core.T,cfg)
        distance=np.full(len(p),np.inf);distance[valid]=tree.query(p[valid])[0]
        domain,frozen,rescue,protected=masks(p,distance,bg['tolerance_m'],cfg)
        domain&=valid;frozen&=valid;rescue&=valid;protected&=valid
        low=p[ids,2]<cfg['low_band_upper_m']
        row={'frame':fi,'person':len(ids),'low_person':int(low.sum()),'frozen_person':int(frozen[ids].sum()),
             'frozen_low_person':int((frozen[ids]&low).sum()),'protected_person':int(protected[ids].sum()),
             'protected_low_person':int((protected[ids]&low).sum()),'rescued_unknown':int(rescue.sum()),
             'domain_low':int((domain&(p[:,2]<cfg['low_band_upper_m'])).sum())}
        for k in sums:sums[k]+=row[k]
        rows.append(row)
    dump(out/'S8_PARTIAL_REGRESSION_BOUND.json',{'sid':sid,'totals':sums,'frames':rows,'source_sha256':hashes,
       'live_gt_sha256':sha(gtpath),'reference_matches_live_source_bound_gt':True,
       'source_reference':'pointset r01 source-bound PARTIAL reviewed reference only',
       'pointset_background_tolerance_m':bg['tolerance_m'],
       'warning':'Low retention is guaranteed by abstention; many floor/furniture UNKNOWN points also retained. Not pure human extraction.',
       'old_voxel_B_180_and_C_4_not_recomputed_here':True,
       'reference_json_sha256':{str(fi):sha(ps/'output'/sid/f'{fi:04d}.json') for fi in [r['frame'] for r in rows]}})
    print(sums,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);main(Path(p.parse_args().out).resolve())

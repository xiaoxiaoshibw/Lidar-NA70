import { useEffect, useState } from 'react';
import { api } from './api';
import type { Capture } from './api';

type Submap = { segment_id: string; source_pcap: string; valid_start_s: number; valid_end_s: number; yaw_deg: number; angle_source: string; estimated: boolean; point_count: number; angular_coverage_deg: number; quality_status: string; rejection_reason: string | null };
type TurntableJob = { job_id: string; status: string; progress: number; error?: string | null; submaps?: Submap[]; validation?: { comparisons: Record<string,{median_m:number|null;p95_m:number|null}>; excluded_rotation_time: Record<string,{excluded_seconds:number}> }; artifacts?: Record<string,string> };

export function TurntableWorkbench({ captures }: { captures: Capture[] }) {
  const ready = captures.filter(c => c.status === 'ready');
  const [selected, setSelected] = useState<string[]>([]);
  const [angleLog, setAngleLog] = useState('');
  const [calibration, setCalibration] = useState<Record<string,unknown>|null>(null);
  const [job, setJob] = useState<TurntableJob|null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (job) return;
    void api<{jobs:TurntableJob[]}>('/mapping/turntable/jobs').then(value => {
      const latest = value.jobs?.[0]; if (latest) setJob(latest);
    }).catch(() => { /* service may be starting while captures are indexed */ });
  }, [job]);
  useEffect(() => {
    let timer = 0; if (!job || !['queued','running'].includes(job.status)) return;
    const poll = async () => { try { const value = await api<TurntableJob>(`/mapping/turntable/jobs/${job.job_id}`); setJob(value); if (['queued','running'].includes(value.status)) timer = window.setTimeout(poll, 800); } catch(e) { setError((e as Error).message); } };
    timer = window.setTimeout(poll, 500); return () => clearTimeout(timer);
  }, [job?.job_id, job?.status]);
  const readText = (file: File|undefined, action: (value:string)=>void) => { if (file) void file.text().then(action).catch(e => setError(String(e))); };
  async function start() {
    if (!calibration) return;
    try { setError(''); setJob(await api<TurntableJob>('/mapping/turntable/jobs', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({capture_ids:selected, angle_log:angleLog, calibration}) })); }
    catch(e) { setError((e as Error).message); }
  }
  async function exportEvidence() {
    if (!job) return; const result = await api<{bundle:string}>(`/mapping/turntable/jobs/${job.job_id}/export`, {method:'POST'});
    const link=document.createElement('a'); link.href=`/api/mapping/turntable/jobs/${job.job_id}/artifacts/${result.bundle}`; link.download=result.bundle; link.click();
  }
  return <div className="turntable-workbench">
    <h3>固定点转台多视角建图</h3>
    <p className="warn-text"><strong>当前角度为估计值，结果仅供开发验证</strong><br/>DEVELOPMENT / DIAGNOSTIC / ESTIMATED_ANGLE · 不是完整 SLAM，也不是正式转台精度验收。</p>
    <div className="turntable-captures">{ready.map(c => <label key={c.id}><input type="checkbox" aria-label={`转台选择 ${c.filename}`} checked={selected.includes(c.id)} onChange={e => setSelected(s => e.target.checked ? [...s,c.id] : s.filter(id=>id!==c.id))}/>{c.filename}</label>)}</div>
    <label>角度日志 CSV<input aria-label="转台角度日志" type="file" accept=".csv" onChange={e => readText(e.target.files?.[0],setAngleLog)}/></label>
    <label>转台标定 JSON<input aria-label="转台标定文件" type="file" accept=".json" onChange={e => readText(e.target.files?.[0], value => { try { setCalibration(JSON.parse(value)); } catch { setError('转台标定 JSON 无效'); } })}/></label>
    <button disabled={!selected.length || !angleLog || !calibration || !!job && ['queued','running'].includes(job.status)} onClick={() => void start()}>开始转台建图</button>
    {job && <div className="turntable-result"><p role="status">转台任务：{job.status} · {job.progress.toFixed(1)}%</p>{job.error && <p className="warn-text">{job.error}</p>}
      {job.submaps?.map(s => <div className="segment-row" key={s.segment_id}><strong>{s.segment_id} · {s.yaw_deg.toFixed(1)}°</strong><span>{s.angle_source}{s.estimated?' · 估计值':''} · {s.valid_start_s.toFixed(1)}–{s.valid_end_s.toFixed(1)} s · {s.point_count.toLocaleString()} 点 · 覆盖 {s.angular_coverage_deg.toFixed(1)}° · {s.quality_status}{s.rejection_reason?` (${s.rejection_reason})`:''}</span></div>)}
      {job.validation && <><h4>基线与角度补偿对照</h4>{Object.entries(job.validation.comparisons).map(([name,m])=><p key={name}>{name}: median {m.median_m?.toFixed(3)??'—'} m / p95 {m.p95_m?.toFixed(3)??'—'} m</p>)}<p>排除时间：{Object.entries(job.validation.excluded_rotation_time).map(([n,v])=>`${n} ${v.excluded_seconds.toFixed(1)} s`).join(' · ')}</p></>}
      {job.status==='ready' && <><div className="turntable-previews"><figure><img className="turntable-preview" alt="按 PCAP 来源着色的转台拼接预览" src={`/api/mapping/turntable/jobs/${job.job_id}/artifacts/source_colored_preview.png`}/><figcaption>估计角度/最终采用结果</figcaption></figure><figure><img className="turntable-preview" alt="未补偿角度的原始基线预览" src={`/api/mapping/turntable/jobs/${job.job_id}/artifacts/baseline_preview.png`}/><figcaption>A 原始直接累计基线</figcaption></figure></div><button onClick={() => void exportEvidence()}>导出转台地图及证据包</button></>}
    </div>}</div>;
}

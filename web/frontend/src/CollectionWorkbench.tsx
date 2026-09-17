import { useEffect, useState } from 'react';
import { api } from './api';
const initial = { mode: 'fixed', scene_static: false, near_geometry: false, translation: false, start_return: false, no_imu: false, movement_start: null as number | null, return_time: null as number | null, pauses: '', route: '', scene: '', device: '', initialization_frames: 0 };
type RecordData = typeof initial;
type Preflight = { status: string; reasons: string[]; metadata: RecordData; diagnostics: Record<string, unknown> };
export function CollectionWorkbench({ captureId, ready, initializationFrames, onPending }: { captureId: string; ready: boolean; initializationFrames: number; onPending: (pending: boolean) => void }) {
  const [record, setRecord] = useState(initial);
  const [result, setResult] = useState<Preflight | null>(null);
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState('');
  useEffect(() => {
    setResult(null); setLoaded('');
    if (!captureId || !ready) return;
    const controller = new AbortController();
    void api<{ capture_preflight?: Preflight }>(`/captures/${captureId}`, { signal: controller.signal }).then(c => {
      setRecord(previous => c.capture_preflight?.metadata || previous); setLoaded(captureId);
    }).catch(e => { if (!controller.signal.aborted) setError(String(e)); });
    return () => controller.abort();
  }, [captureId, ready]);
  useEffect(() => {
    if (!ready || loaded !== captureId) return;
    onPending(true);
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void api<Preflight>(`/captures/${captureId}/preflight`, { method: 'PUT', signal: controller.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...record, initialization_frames: initializationFrames }) }).then(value => { setResult(value); setError(''); onPending(false); }).catch(e => { if (!controller.signal.aborted) setError(String(e)); });
    }, 300);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [captureId, loaded, ready, record, initializationFrames, onPending]);
  const update = (key: string, value: unknown) => { onPending(true); setRecord(r => ({ ...r, [key]: value })); };
  return <div className="collection-workbench">
    <h3>采集准备与 PCAP 预检</h3>
    <label>采集模式 <select aria-label="采集模式" value={record.mode} onChange={e => update('mode', e.target.value)}><option value="fixed">固定视点静态点云</option><option value="manual">手动移动雷达/无 IMU</option></select></label>
    {([['scene_static','场景静态'],['near_geometry','近处固定几何'],['translation','真实平移路线'],['start_return','已记录起点/返回起点安排'],['no_imu','无 IMU']] as const).map(([key,label]) => <label key={key}><input type="checkbox" checked={record[key]} onChange={e => update(key,e.target.checked)}/>{label}</label>)}
    {([['movement_start','开始移动时间（秒）'],['return_time','返回起点时间（秒）']] as const).map(([key,label]) => <label key={key}>{label}<input aria-label={label} type="number" min="0" step="0.1" value={record[key] ?? ''} onChange={e => update(key,e.target.value === '' ? null : Number(e.target.value))}/></label>)}
    {([['pauses','停顿时间与旋转区间'],['route','路线说明'],['scene','场景说明（含人员/车辆干扰）'],['device','设备备注']] as const).map(([key,label]) => <label key={key}>{label}<textarea aria-label={label} value={record[key]} onChange={e => update(key,e.target.value)}/></label>)}
    <p role="status">{result?.status || '等待上传与预检'}</p>
    {result?.reasons.map(reason => <p key={reason}>{reason}</p>)}
    <p>预检不是定位通过。未取得真实移动轨迹和地图证据前，不能进行真实移动验收；任务仅作诊断。</p>
    {result && <details><summary>预检指标与证据元数据</summary><pre>{JSON.stringify(result,null,2)}</pre></details>}
    {error && <p role="alert">{error}</p>}
  </div>;
}

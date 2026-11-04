import { FormEvent, useEffect, useMemo, useState } from 'react';
import { api } from '../api';

type DetectRunSummary = {
  run_id: string; capture_id: string; mode: 'w3_manual_roi' | 'w4_full_fov';
  end_frame: number; duration_s: number; status: 'queued' | 'running' | 'ready' | 'error';
  error: string | null; created_at: number; updated_at: number;
};

type StageRow = { stage: string; status: string; reject_reason?: string; [k: string]: unknown };

type CandidateLine = {
  direction?: number[]; point_on_line?: number[]; segment_endpoints_sensor_m?: number[][];
  nearest_distance_m?: number; span_m?: number; coverage_fraction?: number;
  rms_orthogonal_m?: number; unique_packet_count?: number;
  source_ids_sha256?: string | null; [k: string]: unknown;
};

type DetectRunDetail = DetectRunSummary & {
  request: Record<string, unknown>;
  candidate?: { status?: string; first_blocking_stage?: string; reject_reason?: string;
    candidate_line?: CandidateLine | null; geometry_class?: string | null } | null;
  evidence?: Record<string, unknown>;
};

const EMPTY_FORM = { capture_id: '', mode: 'w4_full_fov' as DetectRunSummary['mode'],
  end_frame: '600', duration_s: '1.0', roi_m: '' };

export function WireDetectionPanel() {
  const [runs, setRuns] = useState<DetectRunSummary[]>([]);
  const [form, setForm] = useState(EMPTY_FORM);
  const [captures, setCaptures] = useState<string[]>([]);
  const [detail, setDetail] = useState<DetectRunDetail | null>(null);
  const [trace, setTrace] = useState<StageRow[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh(captureId?: string) {
    const target = captureId ?? form.capture_id ?? undefined;
    const query = target ? `?capture_id=${encodeURIComponent(target)}` : '';
    const list = await api<DetectRunSummary[]>(`/api/wire/detect/runs${query}`);
    setRuns(list);
  }

  useEffect(() => {
    api<Array<{ id?: string; capture_id?: string }>>('/api/captures')
      .then(items => setCaptures(items.map(item => item.id ?? item.capture_id).filter(Boolean) as string[]))
      .catch(() => setCaptures([]));
    refresh().catch(() => setRuns([]));
    const timer = setInterval(() => refresh().catch(() => undefined), 4000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!detail?.run_id || detail.status !== 'ready') return;
    api<{ stage_trace: StageRow[] }>(`/api/wire/detect/runs/${detail.run_id}/stage-trace`)
      .then(row => setTrace(row.stage_trace))
      .catch(() => setTrace([]));
  }, [detail?.run_id, detail?.status]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setNotice(null);
    try {
      const body: Record<string, unknown> = {
        capture_id: form.capture_id, mode: form.mode,
        end_frame: Number(form.end_frame), duration_s: Number(form.duration_s),
      };
      if (form.mode === 'w3_manual_roi') {
        body.roi_m = form.roi_m.split(',').map(v => Number(v.trim()));
      }
      await api<DetectRunSummary>('/api/wire/detect/runs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      setNotice('已提交检测任务；结果出现时自动刷新。检测结果是 UNKNOWN_LINE 证据,不构成高压线身份。');
      await refresh(form.capture_id);
    } catch (error) {
      setNotice(`提交失败: ${(error as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  async function openDetail(runId: string) {
    try {
      const row = await api<DetectRunDetail>(`/api/wire/detect/runs/${runId}`);
      setDetail(row);
      setTrace([]);
      setNotice(null);
    } catch (error) {
      setNotice(`读取运行详情失败: ${(error as Error).message}`);
      setDetail(null);
      setTrace([]);
    }
  }

  const statusChip = (status: string) => (
    <span className={`wire-detect-status wire-detect-status-${status}`}>{status}</span>
  );

  const candidateSummary = useMemo(() => {
    const candidate = detail?.candidate;
    if (!candidate) return null;
    const line = candidate.candidate_line ?? null;
    return { status: candidate.status, block: candidate.first_blocking_stage,
             reason: candidate.reject_reason, line };
  }, [detail]);

  return (
    <section className="wire-detect-panel">
      <header className="wire-detect-header">
        <h3>导线检测运行 (W3 手动 ROI / W4 全场)</h3>
        <p>
          运行由服务器持续化,落盘证据为 UNKNOWN_LINE。高压线身份需要 reviewed truth,
          当前所有真实数据保持 NOT_EVALUATED。
        </p>
      </header>
      <form className="wire-detect-form" onSubmit={submit}>
        <label>
          录制
          <select required value={form.capture_id}
                  onChange={e => setForm((f) => ({ ...f, capture_id: e.target.value }))}>
            <option value="">选择 capture…</option>
            {captures.map(id => <option key={id} value={id}>{id}</option>)}
          </select>
        </label>
        <label>
          模式
          <select value={form.mode}
                  onChange={e => setForm((f) => ({ ...f, mode: e.target.value as DetectRunSummary['mode'] }))}>
            <option value="w4_full_fov">W4 全场</option>
            <option value="w3_manual_roi">W3 手动 ROI</option>
          </select>
        </label>
        <label>
          end_frame
          <input type="number" min={0} value={form.end_frame}
                 onChange={e => setForm((f) => ({ ...f, end_frame: e.target.value }))} required />
        </label>
        <label>
          时长 s
          <input type="number" step="0.1" min={0.1} max={5.0} value={form.duration_s}
                 onChange={e => setForm((f) => ({ ...f, duration_s: e.target.value }))} required />
        </label>
        {form.mode === 'w3_manual_roi' && (
          <label className="wire-detect-roi">
            ROI (xmin,xmax,ymin,ymax,zmin,zmax)
            <input type="text" placeholder="170,320,-60,60,5,30" value={form.roi_m}
                   onChange={e => setForm((f) => ({ ...f, roi_m: e.target.value }))} required />
          </label>
        )}
        <button type="submit" disabled={busy}>{busy ? '提交中…' : '新建检测运行'}</button>
      </form>
      {notice && <p className="wire-detect-notice">{notice}</p>}

      <table className="wire-detect-table">
        <thead>
          <tr><th>run</th><th>模式</th><th>end</th><th>时长</th><th>状态</th><th>时间</th></tr>
        </thead>
        <tbody>
          {runs.length === 0 && <tr><td colSpan={6}>还没有检测运行</td></tr>}
          {runs.map(run => (
            <tr key={run.run_id} onClick={() => openDetail(run.run_id)}
                className="wire-detect-row" data-active={detail?.run_id === run.run_id}>
              <td>{run.run_id.slice(0, 8)}</td>
              <td>{run.mode}</td>
              <td>{run.end_frame}</td>
              <td>{run.duration_s}s</td>
              <td>{statusChip(run.status)}{run.error ? <small>{run.error}</small> : null}</td>
              <td>{new Date(run.updated_at * 1000).toLocaleTimeString()}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {detail && candidateSummary && (
        <div className="wire-detect-detail">
          <h4>运行 {detail.run_id.slice(0, 8)} 结果</h4>
          <dl>
            <dt>状态</dt><dd>{candidateSummary.status ?? '-'}</dd>
            <dt>首阻断阶段</dt><dd>{candidateSummary.block ?? '-'}</dd>
            <dt>拒绝原因</dt><dd>{candidateSummary.reason ?? '-'}</dd>
            {candidateSummary.line && (
              <>
                <dt>direction</dt><dd>{candidateSummary.line.direction?.join(', ')}</dd>
                <dt>span_m</dt><dd>{candidateSummary.line.span_m}</dd>
                <dt>nearest_distance_m</dt><dd>{candidateSummary.line.nearest_distance_m}</dd>
                <dt>coverage_fraction</dt><dd>{candidateSummary.line.coverage_fraction}</dd>
                <dt>rms_orthogonal_m</dt><dd>{candidateSummary.line.rms_orthogonal_m}</dd>
                <dt>unique_packets</dt><dd>{candidateSummary.line.unique_packet_count}</dd>
                <dt>source_ids_sha256</dt><dd>{candidateSummary.line.source_ids_sha256 ?? '-'}</dd>
              </>
            )}
          </dl>
          <h4>Stage trace</h4>
          <ol className="wire-detect-trace">
            {trace.map((row, i) => (
              <li key={`${row.stage}-${i}`}>
                <code>{row.stage}</code> {row.status}
                {row.reject_reason ? ` · ${row.reject_reason}` : ''}
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}

import { useEffect, useMemo, useRef, useState } from 'react';
import { api, type Capture } from '../api';
import { PageNav } from '../PageNav';
import { WireDetectionPanel } from './WireDetectionPanel';

type WireCapabilities = {
  schema_version: string;
  modes: Array<{ id: string; label: string; available: boolean; evaluation_status: string }>;
  future_modes: Array<{ id: string; available: boolean }>;
  max_active_runs: number;
  default_window_seconds: number[];
};

type RunStatus = 'queued' | 'running' | 'ready' | 'error';
type RunSummary = {
  run_id: string; capture_id: string; background_capture_id: string | null;
  end_frame: number | null; background_end_frame: number | null;
  status: RunStatus; wire_return_status: 'NOT_EVALUATED';
  window_count: number; error: string | null; created_at: number; updated_at: number;
};

type PointStats = {
  point_count: number; source_frame_count: number; source_packet_count: number;
  first_packet_device_time: number | null; last_packet_device_time: number | null;
  latest_source_age_s: number | null;
  raw_intensity: { min: number; p05: number; p50: number; p95: number; max: number } | null;
  sensor_distance_m: { min: number; p05: number; p50: number; p95: number; max: number } | null;
};

type WindowSummary = {
  window_id: string; input_role: string; requested_duration_s: number | null;
  actual_span_s: number | null; time_quality: string; ray_count: number;
  valid_return_count: number; zero_distance_count: number;
  target_roi: PointStats | null; wire_return_status: 'NOT_EVALUATED';
};

type RunDetail = RunSummary & {
  request: { mode: string; capture_sha256: string | null; background_sha256: string | null;
    config_sha256: string; render: boolean };
  windows: WindowSummary[];
  evidence_gaps: string[];
};

type WindowMetrics = WindowSummary & {
  direction_known_count?: number; direction_unknown_count?: number;
  packet_count: number; frame_count: number; first_frame: number | null;
  end_frame: number; first_packet_index: number | null; last_packet_index: number | null;
  actual_start_device_time: number | null; actual_end_device_time: number | null;
  partial_window: boolean; nonfinite_count: number; valid_outside_rois_count: number;
  all_valid: PointStats; background_rois: PointStats[];
  reference_corridor: PointStats | null; confirmed_target_return_count: null;
  time_resolution: string; segment_start_reason: string | null;
  equal_adjacent_device_times: number;
};

type PacketMetric = {
  window_id: string; index: number; frame_index: number; local_index: number;
  device_time: number | null; capture_time: number; segment: number | null;
  boundary_reason: string | null; ray_count: number; valid_mask: number;
  direction_known_mask?: number;
  target_roi_mask: number; background_roi_mask: number; reference_corridor_mask: number;
};

type WindowResult = { run_id: string; window: WindowMetrics; packets: PacketMetric[] };
type CloudPoint = {
  x: number; y: number; z: number; intensity: number;
  frame_index: number; packet_index: number; point_index: number;
  device_time: number | null;
};
type CloudResult = {
  run_id: string; window_id: string; capture_id: string; input_role: string;
  total_valid_points: number;
  sampled: boolean; points: CloudPoint[];
};

const RUN_ACTIVE = new Set<RunStatus>(['queued', 'running']);
const RUN_LABEL: Record<RunStatus, string> = {
  queued: '排队中', running: '诊断中', ready: '结果就绪', error: '失败',
};
const GAP_LABEL: Record<string, string> = {
  target_roi_missing: '未提供人工目标 ROI',
  independent_target_position_not_reviewed: '独立目标位置尚未复核',
  fixed_sensor_and_target_not_confirmed: '雷达与目标固定状态尚未确认',
  target_presence_not_confirmed: '目标存在性尚未确认',
  wire_diameter_missing: '线径尚未记录',
  same_site_background_pair_not_verified: '同机位背景配对尚未复核',
  independent_repeat_recordings_require_review: '重复录制仍需独立复核',
  target_attribution_requires_review: '目标回波归属仍需人工复核',
};
const FUTURE_LABEL: Record<string, string> = {
  w3_roi: 'W3 人工 ROI 线段', w4_full_fov: 'W4 全视场检索', w5_motion: 'W5 运动补偿',
};

function number(value: number | null | undefined, digits = 0): string {
  return value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}

function datetime(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '—' : new Date(value * 1000).toLocaleString('zh-CN');
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function pointId(point: CloudPoint): string {
  return `帧 ${point.frame_index} / 包 ${point.packet_index} / 点 ${point.point_index}`;
}

function projection(points: CloudPoint[], width: number, height: number, horizontal: 'x' | 'y', vertical: 'y' | 'z') {
  const pad = 22;
  const xs = points.map(point => point[horizontal]);
  const ys = points.map(point => point[vertical]);
  const xmin = Math.min(...xs), xmax = Math.max(...xs);
  const ymin = Math.min(...ys), ymax = Math.max(...ys);
  const spanX = Math.max(xmax - xmin, 0.1);
  const spanY = Math.max(ymax - ymin, 0.1);
  const scale = Math.min((width - 2 * pad) / spanX, (height - 2 * pad) / spanY);
  const usedX = spanX * scale, usedY = spanY * scale;
  const left = (width - usedX) / 2, top = (height - usedY) / 2;
  return points.map(point => ({
    px: left + (point[horizontal] - xmin) * scale,
    py: height - top - (point[vertical] - ymin) * scale,
  }));
}

function ProjectionCanvas({ points, selected, onPick, horizontal, vertical, title }: {
  points: CloudPoint[]; selected: number | null; onPick: (index: number | null) => void;
  horizontal: 'x' | 'y'; vertical: 'y' | 'z'; title: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const paint = () => {
      const width = Math.max(1, canvas.clientWidth), height = Math.max(1, canvas.clientHeight);
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      const context = canvas.getContext('2d');
      if (!context) return;
      context.scale(ratio, ratio);
      context.fillStyle = '#0c1723';
      context.fillRect(0, 0, width, height);
      context.strokeStyle = '#263b4d';
      context.lineWidth = 1;
      for (let fraction = 0.25; fraction <= 0.75; fraction += 0.25) {
        context.beginPath();
        context.moveTo(width * fraction, 0);
        context.lineTo(width * fraction, height);
        context.moveTo(0, height * fraction);
        context.lineTo(width, height * fraction);
        context.stroke();
      }
      if (!points.length) return;
      const projected = projection(points, width, height, horizontal, vertical);
      context.fillStyle = '#61d8c3';
      for (const location of projected) {
        context.fillRect(location.px - 1, location.py - 1, 2, 2);
      }
      if (selected != null && projected[selected]) {
        const location = projected[selected];
        context.beginPath();
        context.arc(location.px, location.py, 6, 0, Math.PI * 2);
        context.lineWidth = 2;
        context.strokeStyle = '#ffb852';
        context.stroke();
      }
    };
    const observer = new ResizeObserver(paint);
    observer.observe(canvas);
    paint();
    return () => observer.disconnect();
  }, [points, selected, horizontal, vertical]);

  const pick = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = ref.current;
    if (!canvas || !points.length) return;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left, y = event.clientY - rect.top;
    const positions = projection(points, canvas.clientWidth, canvas.clientHeight, horizontal, vertical);
    let nearest = -1, distance = 12 * 12;
    positions.forEach((location, index) => {
      const candidate = (location.px - x) ** 2 + (location.py - y) ** 2;
      if (candidate < distance) { nearest = index; distance = candidate; }
    });
    onPick(nearest < 0 ? null : nearest);
  };

  return <div className="wire-projection">
    <div className="wire-projection-title"><strong>{title}</strong><span>{horizontal.toUpperCase()} / {vertical.toUpperCase()} · m</span></div>
    <canvas ref={ref} onClick={pick} aria-label={`${title}点云投影`} role="img" />
  </div>;
}

function PacketTimeline({ packets, selected, onSelect }: {
  packets: PacketMetric[]; selected: number; onSelect: (index: number) => void;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const paint = () => {
      const width = Math.max(1, canvas.clientWidth), height = Math.max(1, canvas.clientHeight);
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      const context = canvas.getContext('2d');
      if (!context) return;
      context.scale(ratio, ratio);
      context.fillStyle = '#0c1723';
      context.fillRect(0, 0, width, height);
      if (!packets.length) return;
      const bins = Math.min(Math.floor(width), packets.length);
      const binWidth = width / bins;
      for (let bin = 0; bin < bins; bin += 1) {
        const from = Math.floor(bin * packets.length / bins);
        const to = Math.max(from + 1, Math.floor((bin + 1) * packets.length / bins));
        const group = packets.slice(from, to);
        const mean = group.reduce((sum, packet) => sum + packet.valid_mask, 0) / group.length;
        const barHeight = Math.max(1, mean / 200 * (height - 12));
        context.fillStyle = group.some(packet => packet.boundary_reason) ? '#ffb852' : '#58d6bd';
        context.fillRect(bin * binWidth, height - barHeight, Math.max(1, binWidth), barHeight);
      }
      context.fillStyle = '#ff684f';
      context.fillRect((selected + 0.5) / packets.length * width - 1, 0, 2, height);
    };
    const observer = new ResizeObserver(paint);
    observer.observe(canvas);
    paint();
    return () => observer.disconnect();
  }, [packets, selected]);
  const select = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = ref.current;
    if (!canvas || !packets.length) return;
    const x = event.clientX - canvas.getBoundingClientRect().left;
    onSelect(Math.max(0, Math.min(packets.length - 1, Math.floor(x / canvas.clientWidth * packets.length))));
  };
  return <canvas ref={ref} className="wire-timeline-canvas" role="img" aria-label="逐包有效回波时间线" onClick={select} />;
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return <div className="wire-stat"><span>{label}</span><strong>{value}</strong>{hint && <small>{hint}</small>}</div>;
}

export default function WireWorkbench() {
  const [captures, setCaptures] = useState<Capture[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [capabilities, setCapabilities] = useState<WireCapabilities | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogVersion, setCatalogVersion] = useState(0);
  const [captureId, setCaptureId] = useState('');
  const [backgroundId, setBackgroundId] = useState('');
  const [endFrame, setEndFrame] = useState('');
  const [backgroundEndFrame, setBackgroundEndFrame] = useState('');
  const [render, setRender] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [runDetail, setRunDetail] = useState<RunDetail | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [runRefresh, setRunRefresh] = useState(0);
  const [selectedWindowId, setSelectedWindowId] = useState<string | null>(null);
  const [windowResult, setWindowResult] = useState<WindowResult | null>(null);
  const [cloud, setCloud] = useState<CloudResult | null>(null);
  const [windowLoading, setWindowLoading] = useState(false);
  const [windowError, setWindowError] = useState<string | null>(null);
  const [cloudError, setCloudError] = useState<string | null>(null);
  const [selectedPoint, setSelectedPoint] = useState<number | null>(null);
  const [selectedPacket, setSelectedPacket] = useState(0);

  useEffect(() => {
    let live = true;
    setCatalogLoading(true);
    Promise.allSettled([
      api<Capture[]>('/captures'),
      api<WireCapabilities>('/wire/capabilities'),
      api<RunSummary[]>('/wire/runs?limit=50'),
    ]).then(([captureResult, capabilityResult, runResult]) => {
      if (!live) return;
      const errors: string[] = [];
      if (captureResult.status === 'fulfilled') {
        setCaptures(captureResult.value);
        const ready = captureResult.value.filter(item => item.status === 'ready');
        setCaptureId(current => ready.some(item => item.id === current) ? current : ready[0]?.id ?? '');
      } else errors.push(`录制列表：${errorText(captureResult.reason)}`);
      if (capabilityResult.status === 'fulfilled') setCapabilities(capabilityResult.value);
      else errors.push(`导线接口：${errorText(capabilityResult.reason)}`);
      if (runResult.status === 'fulfilled') setRuns(runResult.value);
      else errors.push(`运行列表：${errorText(runResult.reason)}`);
      setCatalogError(errors.length ? errors.join('；') : null);
      setCatalogLoading(false);
    });
    return () => { live = false; };
  }, [catalogVersion]);

  useEffect(() => {
    setRunDetail(null);
    setRunError(null);
    setSelectedWindowId(null);
    if (!selectedRunId) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const result = await api<RunDetail>(`/wire/runs/${selectedRunId}`, { signal: controller.signal });
        if (!live || result.run_id !== selectedRunId) return;
        setRunDetail(result);
        setRunError(null);
        setSelectedWindowId(previous => result.windows.some(item => item.window_id === previous)
          ? previous : result.windows[0]?.window_id ?? null);
        if (RUN_ACTIVE.has(result.status)) timer = setTimeout(poll, 1500);
        else setCatalogVersion(value => value + 1);
      } catch (error) {
        if (live) setRunError(errorText(error));
      }
    };
    void poll();
    return () => { live = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [selectedRunId, runRefresh]);

  useEffect(() => {
    setWindowResult(null);
    setCloud(null);
    setSelectedPoint(null);
    setSelectedPacket(0);
    setWindowError(null);
    setCloudError(null);
    if (!selectedRunId || !selectedWindowId || runDetail?.run_id !== selectedRunId || runDetail.status !== 'ready') return;
    let live = true;
    const controller = new AbortController();
    setWindowLoading(true);
    const base = `/wire/runs/${selectedRunId}/windows/${encodeURIComponent(selectedWindowId)}`;
    Promise.allSettled([
      api<WindowResult>(base, { signal: controller.signal }),
      api<CloudResult>(`${base}/cloud?limit=4000`, { signal: controller.signal }),
    ]).then(([windowResponse, cloudResponse]) => {
      if (!live) return;
      if (windowResponse.status === 'fulfilled' && windowResponse.value.run_id === selectedRunId
          && windowResponse.value.window.window_id === selectedWindowId) {
        setWindowResult(windowResponse.value);
      } else {
        setWindowError(windowResponse.status === 'rejected' ? errorText(windowResponse.reason) : '窗口身份不匹配');
      }
      const expectedRole = runDetail.windows.find(item => item.window_id === selectedWindowId)?.input_role;
      const expectedCapture = expectedRole === 'background'
        ? runDetail.background_capture_id : runDetail.capture_id;
      if (cloudResponse.status === 'fulfilled' && cloudResponse.value.run_id === selectedRunId
          && cloudResponse.value.window_id === selectedWindowId
          && cloudResponse.value.input_role === expectedRole
          && cloudResponse.value.capture_id === expectedCapture) {
        setCloud(cloudResponse.value);
      } else {
        setCloudError(cloudResponse.status === 'rejected' ? errorText(cloudResponse.reason) : '点云身份不匹配');
      }
      setWindowLoading(false);
    });
    return () => { live = false; controller.abort(); };
  }, [selectedRunId, selectedWindowId, runDetail?.run_id, runDetail?.status]);

  const capture = captures.find(item => item.id === captureId);
  const background = captures.find(item => item.id === backgroundId);
  const readyCaptures = captures.filter(item => item.status === 'ready');
  const w1Available = !!capabilities?.modes.some(mode => mode.id === 'w1_raw' && mode.available);
  const frameValid = (value: string, count: number | undefined) => value === '' ||
    (count != null && /^\d+$/.test(value) && Number(value) < count);
  const formValid = !!capture && capture.status === 'ready' && (!backgroundId || (!!background && background.status === 'ready'
    && background.id !== capture.id && !(background.sha256 && capture.sha256 && background.sha256 === capture.sha256)))
    && frameValid(endFrame, capture?.frame_count)
    && frameValid(backgroundEndFrame, background?.frame_count)
    && (!!backgroundId || backgroundEndFrame === '')
    && w1Available;

  const startRun = async () => {
    if (!formValid || !capture) return;
    setActionError(null);
    setSubmitting(true);
    try {
      const payload = {
        capture_id: capture.id,
        ...(backgroundId ? { background_capture_id: backgroundId } : {}),
        ...(endFrame !== '' ? { end_frame: Number(endFrame) } : {}),
        ...(backgroundEndFrame !== '' ? { background_end_frame: Number(backgroundEndFrame) } : {}),
        render,
      };
      const result = await api<RunSummary>('/wire/runs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      setRuns(previous => [result, ...previous.filter(item => item.run_id !== result.run_id)]);
      setSelectedRunId(result.run_id);
      setRunRefresh(value => value + 1);
    } catch (error) {
      setActionError(errorText(error));
    } finally {
      setSubmitting(false);
    }
  };

  // Render only data proven to belong to the currently selected run and window.
  const currentRun = runDetail?.run_id === selectedRunId ? runDetail : null;
  const currentWindow = windowResult?.run_id === selectedRunId && windowResult.window.window_id === selectedWindowId
    ? windowResult : null;
  const currentCloud = cloud?.run_id === selectedRunId && cloud.window_id === selectedWindowId ? cloud : null;
  const selectedRole = currentRun?.windows.find(item => item.window_id === selectedWindowId)?.input_role;
  const selectedCaptureSha = selectedRole === 'background'
    ? currentRun?.request.background_sha256 : currentRun?.request.capture_sha256;
  const metrics = currentWindow?.window;
  const packets = currentWindow?.packets ?? [];
  const selectedPacketData = packets[selectedPacket] ?? null;
  const points = useMemo(() => (currentCloud?.points ?? []).filter(point => [point.x, point.y, point.z].every(Number.isFinite)), [currentCloud]);
  const selectedPointData = selectedPoint != null ? points[selectedPoint] : null;
  const aroundPacket = packets.slice(Math.max(0, selectedPacket - 4), Math.min(packets.length, selectedPacket + 5));
  const activeRun = runs.find(item => item.run_id === selectedRunId);

  return <div className="wire-root">
    <header className="wire-header">
      <div className="wire-brand"><span className="wire-brand-mark">NA70</span><div><h1>架空导线感知工作台</h1><p>原始观测 · 逐包复核 · 证据定位</p></div></div>
      <PageNav />
    </header>

    <div className="wire-banner" role="status">
      <strong>W1 原始回波诊断</strong><span className="wire-status-badge">NOT_EVALUATED</span>
      <span>当前结果只说明原始射线和回波分布。ROI 点数、几何投影和背景差异均不等于已确认导线回波。</span>
    </div>

    <div className="wire-layout">
      <aside className="wire-sidebar">
        <section className="wire-card">
          <div className="wire-section-head"><h2>新建 W1 运行</h2><span>固定原始 PCAP</span></div>
          <label>主录制
            <select aria-label="主录制" value={captureId} onChange={event => {
              const next = event.target.value;
              setCaptureId(next);
              setEndFrame('');
              if (backgroundId === next) { setBackgroundId(''); setBackgroundEndFrame(''); }
            }}>
              {!readyCaptures.length && <option value="">没有就绪录制</option>}
              {readyCaptures.map(item => <option key={item.id} value={item.id}>{item.filename} · {item.frame_count} 帧</option>)}
            </select>
          </label>
          <p className="wire-muted">尚未导入 PCAP？<a href="/">在主工作台导入</a>。</p>
          <label>主录制终点帧 <small>留空使用 W1 默认终点</small>
            <input aria-label="主录制终点帧" type="number" min="0" max={Math.max(0, (capture?.frame_count ?? 1) - 1)} placeholder="默认" value={endFrame} onChange={event => setEndFrame(event.target.value)} />
          </label>
          <label>同机位背景录制 <small>可选，必须是独立 PCAP</small>
            <select aria-label="同机位背景录制" value={backgroundId} onChange={event => { setBackgroundId(event.target.value); setBackgroundEndFrame(''); }}>
              <option value="">暂不指定</option>
              {readyCaptures.filter(item => item.id !== captureId).map(item => <option key={item.id} value={item.id}>{item.filename} · {item.frame_count} 帧</option>)}
            </select>
          </label>
          {backgroundId && <label>背景终点帧 <small>留空使用 W1 默认终点</small>
            <input aria-label="背景终点帧" type="number" min="0" max={Math.max(0, (background?.frame_count ?? 1) - 1)} placeholder="默认" value={backgroundEndFrame} onChange={event => setBackgroundEndFrame(event.target.value)} />
          </label>}
          <label className="wire-check"><input type="checkbox" checked={render} onChange={event => setRender(event.target.checked)} />生成 W1 图片（耗时更长）</label>
          <button className="wire-primary" disabled={!formValid || submitting} onClick={startRun}>{submitting ? '提交中…' : '运行 W1 原始诊断'}</button>
          {actionError && <p className="wire-error" role="alert">{actionError}</p>}
          <p className="wire-muted">窗口时长：{capabilities?.default_window_seconds?.map(value => `${value} s`).join(' / ') || '读取中'}；并发上限 {capabilities?.max_active_runs ?? '—'}。</p>
        </section>

        <section className="wire-card wire-runs">
          <div className="wire-section-head"><h2>持久化运行</h2><button onClick={() => { setCatalogVersion(value => value + 1); setRunRefresh(value => value + 1); }}>刷新</button></div>
          {catalogLoading && <p className="wire-muted">读取录制与运行…</p>}
          {catalogError && <p className="wire-error" role="alert">{catalogError}</p>}
          {!runs.length && !catalogLoading && <p className="wire-muted">暂无导线诊断运行。</p>}
          <div className="wire-run-list">
            {runs.map(run => <button key={run.run_id} className={`wire-run ${run.run_id === selectedRunId ? 'selected' : ''}`}
              onClick={() => setSelectedRunId(run.run_id)} aria-pressed={run.run_id === selectedRunId}>
              <span><strong>{captures.find(item => item.id === run.capture_id)?.filename ?? run.capture_id.slice(0, 8)}</strong><em className={`wire-run-${run.status}`}>{RUN_LABEL[run.status]}</em></span>
              <small>{datetime(run.created_at)} · {run.window_count} 窗</small>
              <code>{run.run_id}</code>
            </button>)}
          </div>
        </section>
      </aside>

      <main className="wire-content">
        <section className="wire-card wire-mode-card">
          <div className="wire-section-head"><h2>处理阶段</h2><span>接口预留状态</span></div>
          <div className="wire-modes">
            <div className={`wire-mode ${w1Available ? 'available' : ''}`}><strong>W1 原始回波</strong><span>{w1Available ? '当前可运行 · 仅诊断' : '接口不可用'}</span></div>
            <div className="wire-mode available"><strong>W3 手动 ROI</strong><span>在线持久化可用 · 仍 UNKNOWN_LINE</span></div>
            <div className="wire-mode available"><strong>W4 全场</strong><span>在线持久化可用 · 仍 UNKNOWN_LINE</span></div>
            <div className="wire-mode"><strong>W5 运动</strong><span>接口未启用</span></div>
          </div>
        </section>

        <section className="wire-card">
          <WireDetectionPanel />
        </section>

        {!selectedRunId && <section className="wire-card wire-empty"><h2>选择一个运行</h2><p>新建 W1 诊断或从左侧打开历史运行，查看固定 run ID 的窗口、逐包统计和原始有效点。</p></section>}
        {selectedRunId && <section className="wire-card">
          <div className="wire-section-head"><h2>运行证据</h2><span className={`wire-run-${currentRun?.status ?? activeRun?.status ?? 'queued'}`}>{currentRun ? RUN_LABEL[currentRun.status] : '读取中'}</span></div>
          <div className="wire-identity"><span>RUN ID</span><code>{selectedRunId}</code><span>主录制 SHA-256</span><code>{currentRun?.request.capture_sha256 ?? '读取中'}</code></div>
          {runError && <p className="wire-error" role="alert">{runError}</p>}
          {currentRun && <>
            <div className="wire-run-meta"><span>模式 {currentRun.request.mode}</span><span>创建于 {datetime(currentRun.created_at)}</span><span>图片 {currentRun.request.render ? '已生成' : '未生成'}</span></div>
            {currentRun.background_capture_id && <div className="wire-identity"><span>背景 SHA-256</span><code>{currentRun.request.background_sha256 ?? '—'}</code></div>}
            {currentRun.status === 'error' && <p className="wire-error" role="alert">{currentRun.error ?? '运行失败，检查后端日志和运行目录。'}</p>}
            {RUN_ACTIVE.has(currentRun.status) && <p className="wire-muted" role="status">诊断正在运行，页面每 1.5 秒查询一次。运行目录和已完成证据会保留。</p>}
            {currentRun.status === 'ready' && <div className="wire-callout">已生成 {currentRun.window_count} 个 W1 窗口。<strong>导线回波归属：NOT_EVALUATED</strong></div>}
            {currentRun.evidence_gaps.length > 0 && <details className="wire-gaps" open><summary>证据缺口 · {currentRun.evidence_gaps.length}</summary><ul>{currentRun.evidence_gaps.map(gap => <li key={gap}>{GAP_LABEL[gap] ?? gap}<code>{gap}</code></li>)}</ul></details>}
          </>}
        </section>}

        {currentRun?.status === 'ready' && <section className="wire-card">
          <div className="wire-section-head"><h2>观察窗口</h2><span>{currentRun.windows.length} 个</span></div>
          {currentRun.windows.length === 0 && <p className="wire-muted">运行已完成，但没有窗口指标。</p>}
          <div className="wire-window-list">
            {currentRun.windows.map(item => <button key={item.window_id} className={`wire-window ${selectedWindowId === item.window_id ? 'selected' : ''}`}
              onClick={() => setSelectedWindowId(item.window_id)} aria-pressed={selectedWindowId === item.window_id}>
              <span><strong>{item.input_role === 'background' ? '背景' : '主录制'} · {item.requested_duration_s == null ? '单帧' : `${item.requested_duration_s} s`}</strong><small>{item.time_quality}</small></span>
              <span>{number(item.valid_return_count)} / {number(item.ray_count)} 有效回波</span>
            </button>)}
          </div>
        </section>}

        {selectedWindowId && currentRun?.status === 'ready' && <>
          <section className="wire-card">
            <div className="wire-section-head"><h2>窗口原始指标</h2><code>{selectedWindowId}</code></div>
            {windowLoading && <p className="wire-muted">正在读取窗口和有界点云…</p>}
            {windowError && <p className="wire-error" role="alert">窗口指标：{windowError}</p>}
            {metrics && <>
              <div className="wire-stats">
                <Stat label="原始射线" value={number(metrics.ray_count)} />
                <Stat label="有效距离回波" value={number(metrics.valid_return_count)} hint={`${number(metrics.valid_return_count / Math.max(metrics.ray_count, 1) * 100, 1)}% 射线`} />
                <Stat label="零距离码" value={number(metrics.zero_distance_count)} />
                <Stat label="方向可恢复" value={number(metrics.direction_known_count)} hint="含距离为零但角码可恢复的射线" />
                <Stat label="方向未知" value={number(metrics.direction_unknown_count)} hint="不计入确认扫过目标的分母" />
                <Stat label="非有限点" value={number(metrics.nonfinite_count)} />
                <Stat label="目标 ROI 内有效点" value={metrics.target_roi ? number(metrics.target_roi.point_count) : '未配置'} hint="不是已确认目标回波" />
                <Stat label="独立来源包" value={number(metrics.all_valid?.source_packet_count)} />
              </div>
              <div className="wire-metric-table">
                <div><span>时间质量</span><strong>{metrics.time_quality}{metrics.partial_window ? ' · 短窗' : ''}</strong></div>
                <div><span>请求 / 实际跨度</span><strong>{metrics.requested_duration_s == null ? '单帧' : `${number(metrics.requested_duration_s, 2)} s`} / {number(metrics.actual_span_s, 3)} s</strong></div>
                <div><span>来源帧 / 包</span><strong>{number(metrics.frame_count)} / {number(metrics.packet_count)}</strong></div>
                <div><span>设备时间</span><strong>{number(metrics.actual_start_device_time, 4)} → {number(metrics.actual_end_device_time, 4)}</strong></div>
                <div><span>原始距离 p50 / p95</span><strong>{number(metrics.all_valid?.sensor_distance_m?.p50, 2)} / {number(metrics.all_valid?.sensor_distance_m?.p95, 2)} m</strong></div>
                <div><span>参考走廊有效点</span><strong>{metrics.reference_corridor ? number(metrics.reference_corridor.point_count) : '未配置'}</strong></div>
                <div><span>背景 ROI 数</span><strong>{number(metrics.background_rois?.length)}</strong></div>
                <div><span>包时间粒度</span><strong>{metrics.time_resolution}</strong></div>
              </div>
              <p className="wire-muted">原始数据保留零距离射线；下面点云仅显示有效回波抽样。包时间在该包的 200 条射线中重复，不是逐点实测时间。</p>
            </>}
          </section>

          <section className="wire-card">
            <div className="wire-section-head"><h2>逐包时间线</h2><span>{number(packets.length)} 包 · 每柱是对应包组的平均有效回波数</span></div>
            {metrics && !packets.length && <p className="wire-muted">当前窗口无可显示包。</p>}
            {!!packets.length && <>
              <PacketTimeline packets={packets} selected={selectedPacket} onSelect={setSelectedPacket} />
              <input className="wire-packet-slider" aria-label="选择包" type="range" min="0" max={packets.length - 1} value={selectedPacket} onChange={event => setSelectedPacket(Number(event.target.value))} />
              {selectedPacketData && <div className="wire-packet-selected">
                <strong>包 {selectedPacketData.index}</strong><span>帧 {selectedPacketData.frame_index} · 帧内包 {selectedPacketData.local_index}</span>
                <span>有效 {number(selectedPacketData.valid_mask)} / {number(selectedPacketData.ray_count)}</span>
                <span>方向可恢复 {number(selectedPacketData.direction_known_mask)} / {number(selectedPacketData.ray_count)}</span>
                <span>目标 ROI {number(selectedPacketData.target_roi_mask)}</span>
                <span>设备时间 {number(selectedPacketData.device_time, 6)}</span>
                {selectedPacketData.boundary_reason && <em>时间段边界：{selectedPacketData.boundary_reason}</em>}
              </div>}
              <div className="wire-packet-table-wrap"><table className="wire-packet-table"><thead><tr><th>包 ID</th><th>帧</th><th>设备时间</th><th>有效 / 200</th><th>ROI</th><th>段边界</th></tr></thead><tbody>
                {aroundPacket.map(packet => <tr key={packet.index} className={packet.index === selectedPacketData?.index ? 'selected' : ''} onClick={() => setSelectedPacket(packets.indexOf(packet))}>
                  <td>{packet.index}</td><td>{packet.frame_index}</td><td>{number(packet.device_time, 6)}</td><td>{packet.valid_mask}</td><td>{packet.target_roi_mask}</td><td>{packet.boundary_reason ?? '—'}</td>
                </tr>)}
              </tbody></table></div>
            </>}
          </section>

          <section className="wire-card">
            <div className="wire-section-head"><h2>原始有效点三视图</h2><span>点击点查看原始身份</span></div>
            {cloudError && <p className="wire-error" role="alert">点云：{cloudError}</p>}
            {currentCloud && <>
              <p className="wire-muted">展示 {number(points.length)} / {number(currentCloud.total_valid_points)} 个有效点{currentCloud.sampled ? '，按原始顺序等间隔抽样；不可据此判定沿线无点' : '，未抽样'}。点云显示不代表拟合或识别。</p>
              {!points.length && <p className="wire-empty-cloud">此窗口没有有效距离点。</p>}
              {!!points.length && <div className="wire-projections">
                <ProjectionCanvas title="正视" horizontal="y" vertical="z" points={points} selected={selectedPoint} onPick={setSelectedPoint} />
                <ProjectionCanvas title="侧视" horizontal="x" vertical="z" points={points} selected={selectedPoint} onPick={setSelectedPoint} />
                <ProjectionCanvas title="俯视" horizontal="x" vertical="y" points={points} selected={selectedPoint} onPick={setSelectedPoint} />
              </div>}
              {selectedPointData && <div className="wire-point-info"><strong>抽样点原始身份</strong><code>{selectedCaptureSha ?? '—'} / {pointId(selectedPointData)}</code><span>XYZ ({number(selectedPointData.x, 3)}, {number(selectedPointData.y, 3)}, {number(selectedPointData.z, 3)}) m · 强度 {number(selectedPointData.intensity, 1)} · 包时间 {number(selectedPointData.device_time, 6)}</span></div>}
            </>}
          </section>
        </>}
      </main>
    </div>
  </div>;
}

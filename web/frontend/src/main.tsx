import { CollectionWorkbench } from './CollectionWorkbench';
import { TurntableWorkbench } from './TurntableWorkbench';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, ArrowUpRight, Box, Check, ChevronLeft, ChevronRight, CircleHelp, Download, FileBox, FolderOpen, Hand, Layers, LoaderCircle, Maximize, Pause, Play, Plus, Radio, RotateCcw, Ruler, Settings2, Trash2, Upload, X } from 'lucide-react';
import { PageNav } from './PageNav';
import {
  api, cancelMappingJob, clock, createMappingJob, exportMappingJob, JOB_ACTIVE, MapChunk, MapMetadata,
  MappingFrame, MappingJob, MappingTrajectory, readFrame, readFrameQuality, readMapChunks, readMapMetadata,
  readTrajectory, STATE_COLOR, STATE_LABEL,
} from './api';
import type { Capture, Frame, FrameInfo } from './api';
import { CloudView, MapCloud, Marker, RangeMeasurement, Settings, ViewerTool, ViewHandle } from './CloudView';
import './style.css';
import './toolbox.css';

type Mode = 'raw' | 'odometry' | 'map';
const MODES: Array<{ id: Mode; label: string; hint: string }> = [
  { id: 'raw', label: 'RAW', hint: '原始坐标系叠加：用于暴露拖影，不做任何配准' },
  { id: 'odometry', label: 'ODOMETRY', hint: '纯点云里程计累计位姿与轨迹（无 IMU、无回环）' },
  { id: 'map', label: 'MAP', hint: '后端体素融合地图 + 当前帧，块按视野流式读取' },
];
const defaults: Settings = { color: 'height', size: 2, opacity: 1, min: 0, max: 1200, accumulation: 200, grid: true, gridSize: 10, axes: true, budget: 0, navigation: 'rotate', showMotion: false };
const statuses: Record<string, string> = { ready: '可回放', uploading: '上传中', indexing: '建立帧索引', error: '导入失败' };
const jobStatusLabel: Record<string, string> = { queued: '排队中', running: '配准中', cancelling: '正在取消', cancelled: '已取消', ready: '已完成', error: '失败' };
const bytes = (n: number) => `${(n / 1024 / 1024).toFixed(1)} MB`;
const MARKER_RGB: Record<string, [number, number, number]> = {
  quality_failed: [0.95, 0.72, 0.18], insufficient_points: [0.95, 0.72, 0.18],
  degenerate_geometry: [0.98, 0.55, 0.12], timestamp_invalid: [0.85, 0.45, 0.95],
  tracking_lost: [0.92, 0.26, 0.20],
};
const STRIP_RANK: Record<string, number> = { accepted: 0, reference: 0, fixed_observation: 0, reinitialized: 0, quality_failed: 1, insufficient_points: 1, timestamp_invalid: 2, degenerate_geometry: 3, tracking_lost: 4, pending: -1 };
const STRIP_COLOR = STATE_COLOR;
const FAILED_STATES = new Set(['quality_failed', 'insufficient_points', 'degenerate_geometry', 'timestamp_invalid', 'tracking_lost']);
const box3 = (bounds: { minimum: number[] | null; maximum: number[] | null } | null) =>
  bounds && bounds.minimum && bounds.maximum
    ? `${bounds.minimum.map((v, i) => v.toFixed(1)).join(' / ')} → ${bounds.maximum.map((v, i) => v.toFixed(1)).join(' / ')} m`
    : '—';

/** Failure timeline: one thin column per frame, worst state wins a column. */
function FailureStrip({ states, onSeek }: { states: string[]; onSeek: (index: number) => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const element = canvas.current; if (!element) return;
    const draw = () => {
      const width = Math.max(1, element.clientWidth), height = 12;
      element.width = width; element.height = height;
      const context = element.getContext('2d'); if (!context) return;
      context.clearRect(0, 0, width, height);
      if (!states.length) return;
      for (let x = 0; x < width; x++) {
        const from = Math.floor(x * states.length / width);
        const to = Math.max(from + 1, Math.floor((x + 1) * states.length / width));
        let worst = 'pending';
        for (let i = from; i < to && i < states.length; i++) if ((STRIP_RANK[states[i]] ?? 0) > (STRIP_RANK[worst] ?? 0)) worst = states[i];
        context.fillStyle = STRIP_COLOR[worst] || '#334350';
        context.fillRect(x, 0, 1, height);
      }
    };
    draw();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(draw);
    observer?.observe(element);
    return () => observer?.disconnect();
  }, [states]);
  const seek = (event: React.MouseEvent<HTMLCanvasElement>) => {
    if (!states.length) return;
    const box = event.currentTarget.getBoundingClientRect();
    onSeek(Math.min(states.length - 1, Math.max(0, Math.round((event.clientX - box.left) / box.width * (states.length - 1)))));
  };
  return <canvas ref={canvas} className="failure-strip" onClick={seek} title="失败帧时间轴：点击跳转到对应帧"/>;
}

function App() {
  const [captures, setCaptures] = useState<Capture[]>([]);
  const [selected, setSelected] = useState('');
  const selectedRef = useRef(''); selectedRef.current = selected;
  const capture = captures.find(c => c.id === selected);
  const [indices, setIndices] = useState<FrameInfo[]>([]);
  const [indicesCapture, setIndicesCapture] = useState('');
  const [framesData, setFrames] = useState<Frame[]>([]);
  const [framesContext, setFramesContext] = useState('');
  const [index, setIndex] = useState(0);
  const [loaded, setLoaded] = useState(-1);
  const [cloudProgress, setCloudProgress] = useState({ done: 0, total: 0 });
  const playback = useRef({ index, loaded }); playback.current = { index, loaded };
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [settings, setSettings] = useState(defaults);
  const requestIndex = settings.accumulation === 0 ? 0 : index;
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [upload, setUpload] = useState<{ id: string; percent: number } | null>(null);
  const uploadRequest = useRef<XMLHttpRequest | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const view = useRef<ViewHandle>(null);
  const cache = useRef(new Map<string, Frame>());
  const [stats, setStats] = useState({ count: 0, fps: 0, azimuth: 0, polar: 0 });
  const [help, setHelp] = useState(false);
  const [activeTool, setActiveTool] = useState<ViewerTool>('navigate');
  const [measurement, setMeasurement] = useState<RangeMeasurement | null>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);
  // ---- mapping workbench
  const [jobs, setJobs] = useState<MappingJob[]>([]);
  const [jobId, setJobId] = useState('');
  const [trajectoryData, setTrajectory] = useState<MappingTrajectory | null>(null);
  const [mapMetaData, setMapMeta] = useState<MapMetadata | null>(null);
  const [mapDataContext, setMapDataContext] = useState('');
  const [chunks, setChunks] = useState<Map<string, MapChunk>>(new Map());
  const chunksRef = useRef(chunks); chunksRef.current = chunks;
  const pendingChunks = useRef(new Set<string>());
  const [mode, setMode] = useState<Mode>('raw');
  const [frameQualityData, setFrameQuality] = useState<MappingFrame | null>(null);
  const [exporting, setExporting] = useState(false);
  const [preflightPending, setPreflightPending] = useState(true);
  const [bootstrapFrames, setBootstrapFrames] = useState(0);
  const [fixedSeconds, setFixedSeconds] = useState(10);
  const [fixedConfirmed, setFixedConfirmed] = useState(false);
  const job = jobs.find(entry => entry.job_id === jobId) || jobs[0] || null;
  const activeJobId = job?.job_id || '';
  const activeMapContext = `${selected}:${activeJobId}`;
  const activeJobIdRef = useRef(activeJobId); activeJobIdRef.current = activeJobId;
  const frameContextKey = `${selected}:${mode}:${mode === 'raw' ? 'raw' : activeJobId}:${settings.accumulation}:${requestIndex}`;
  const frames = framesContext === frameContextKey ? framesData : [];
  const trajectory = mapDataContext === activeMapContext ? trajectoryData : null;
  const mapMeta = mapDataContext === activeMapContext ? mapMetaData : null;
  const activeTrajectory = trajectory;
  const activeMapMeta = mapMeta;
  const frameQuality = activeTrajectory && frameQualityData?.index === index ? frameQualityData : null;
  const jobReady = job?.status === 'ready';
  const fixedJob = job?.pose_source === 'fixed_sensor';
  const busy = capture?.status === 'uploading' || capture?.status === 'indexing';
  const ready = capture?.status === 'ready' && indicesCapture === selected && indices.length > 0;

  useEffect(() => { setMeasurement(null); }, [selected, mode]);
  useEffect(() => { setFixedConfirmed(false); }, [selected]);

  const refresh = useCallback(async () => { const list = await api<Capture[]>('/captures'); setCaptures(list.sort((a, b) => b.created_at - a.created_at)); return list; }, []);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try { const list = await api<Capture[]>('/captures'); if (alive) setCaptures(list.sort((a, b) => b.created_at - a.created_at)); }
      catch (e) { if (alive) setError(`服务连接失败：${(e as Error).message}`); }
    };
    void poll(); const timer = setInterval(poll, 800);
    return () => { alive = false; clearInterval(timer); uploadRequest.current?.abort(); };
  }, []);

  // Job status poll: fast while something is running, lazy otherwise.
  useEffect(() => {
    setJobs([]); setJobId(''); setTrajectory(null); setMapMeta(null); setMapDataContext(''); setChunks(new Map()); pendingChunks.current.clear(); setFrameQuality(null); setMode('raw');
    if (!selected) return;
    const controller = new AbortController();
    let timer = 0;
    const poll = async () => {
      try {
        const result = await api<{ jobs: MappingJob[] }>(`/captures/${selected}/mapping/jobs`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setJobs(result.jobs);
        const active = result.jobs.some(entry => JOB_ACTIVE.has(entry.status));
        clearInterval(timer); timer = window.setInterval(poll, active ? 700 : 2500);
      } catch {
        // Selection precedes upload/index registration; retry an initial 404 too.
        if (!controller.signal.aborted) {
          clearInterval(timer); timer = window.setInterval(poll, 700);
        }
      }
    };
    void poll();
    return () => { controller.abort(); clearInterval(timer); };
  }, [selected]);

  // Completed jobs are re-read from disk after a restart, so a reload alone is
  // enough to get the trajectory and map back.
  useEffect(() => {
    setMapDataContext(''); setTrajectory(null); setMapMeta(null); setChunks(new Map()); pendingChunks.current.clear();
    if (!job || job.status !== 'ready') return;
    const controller = new AbortController();
    Promise.all([readTrajectory(job.job_id, controller.signal), readMapMetadata(job.job_id, controller.signal)])
      .then(([lines, meta]) => { if (!controller.signal.aborted && selectedRef.current === selected) { setTrajectory(lines); setMapMeta(meta); setMapDataContext(`${selected}:${job.job_id}`); } })
      .catch(e => { if (!controller.signal.aborted) setError((e as Error).message); });
    return () => controller.abort();
  }, [job?.job_id, job?.status]);

  useEffect(() => {
    const current = activeTrajectory?.frames.find(entry => entry.frame_index === index);
    if (!job || job.status !== 'ready' || !current) { setFrameQuality(null); return; }
    const controller = new AbortController();
    readFrameQuality(job.job_id, index, controller.signal)
      .then(result => { if (!controller.signal.aborted) setFrameQuality(result); })
      .catch(() => { if (!controller.signal.aborted) setFrameQuality(null); });
    return () => controller.abort();
  }, [job?.job_id, job?.status, activeTrajectory, index]);

  // Map chunks are requested by view, never wholesale.
  const requestChunks = useCallback((box: { min: number[]; max: number[] }) => {
    if (!job || job.status !== 'ready' || !activeMapMeta?.chunks) return;
    const requestJobId = job.job_id;
    const slack = activeMapMeta.voxel_size * activeMapMeta.chunk_voxels;
    const wanted = activeMapMeta.chunks.filter(chunk =>
      chunk.maximum[0] >= box.min[0] - slack && chunk.minimum[0] <= box.max[0] + slack &&
      chunk.maximum[1] >= box.min[1] - slack && chunk.minimum[1] <= box.max[1] + slack &&
      chunk.maximum[2] >= box.min[2] - slack && chunk.minimum[2] <= box.max[2] + slack);
    const keep = new Set(wanted.map(chunk => chunk.id));
    setChunks(previous => {
      let changed = false; const next = new Map(previous);
      for (const id of next.keys()) if (!keep.has(id)) { next.delete(id); changed = true; }
      return changed ? next : previous;
    });
    const missing = wanted.map(chunk => chunk.id)
      .filter(id => !chunksRef.current.has(id) && !pendingChunks.current.has(id)).slice(0, 24);
    if (!missing.length) return;
    for (const id of missing) pendingChunks.current.add(id);
    readMapChunks(requestJobId, `ids=${missing.join(',')}&limit=200000`)
      .then(response => setChunks(previous => {
        if (activeJobIdRef.current !== requestJobId) return previous;
        const next = new Map(previous);
        for (const chunk of response.chunks) next.set(chunk.id, chunk);
        return next;
      }))
      .catch(() => undefined)
      .finally(() => { if (activeJobIdRef.current === requestJobId) for (const id of missing) pendingChunks.current.delete(id); });
  }, [job?.job_id, job?.status, activeMapMeta]);

  useEffect(() => { setPlaying(false); setIndex(0); setLoaded(-1); setFrames([]); setFramesContext(''); setIndices([]); setIndicesCapture(''); cache.current.clear(); setError(''); setCloudProgress({ done: 0, total: 0 }); }, [selected]);
  useEffect(() => {
    if (!selected || capture?.status !== 'ready') return;
    const controller = new AbortController();
    (async () => {
      const result: FrameInfo[] = [];
      let total = 1;
      while (result.length < total) {
        const page = await api<{ total: number; frames: FrameInfo[] }>(`/captures/${selected}/frames?offset=${result.length}&limit=2000`, { signal: controller.signal });
        result.push(...page.frames); total = page.total;
      }
      if (!controller.signal.aborted) { setIndices(result); setIndicesCapture(selected); }
    })().catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [selected, capture?.status]);

  useEffect(() => {
    if (!selected || indicesCapture !== selected || !indices.length) return;
    const controller = new AbortController();
    setLoaded(-1);
    // Dense inspection includes a complete window even at frame zero.
    // Full-file view is progressive and independent of the timeline cursor.
    const size = mode === 'map' && fixedJob ? 1 : settings.accumulation === 0 ? indices.length : Math.min(settings.accumulation, indices.length);
    const start = mode === 'map' && fixedJob ? requestIndex : settings.accumulation === 0 ? 0 : Math.min(Math.max(0, requestIndex - size + 1), indices.length - size);
    const end = start + size;
    const coordinateKey = mode === 'raw' ? 'raw' : (job?.job_id || 'raw');
    const cachedWindow = Array.from({ length: size }, (_, i) => cache.current.get(`${selected}:${coordinateKey}:${start + i}`));
    const cachedCount = cachedWindow.filter(Boolean).length;
    for (const key of cache.current.keys()) {
      const i = Number(key.split(':').at(-1));
      if (!key.startsWith(`${selected}:${coordinateKey}:`) || i < start || i > end + 1) cache.current.delete(key);
    }
    if (cachedCount === size) {
      const complete = cachedWindow as Frame[];
      setFrames(previous => previous.length === size && previous[0] === complete[0] && previous.at(-1) === complete.at(-1) ? previous : complete);
      setFramesContext(frameContextKey);
      setCloudProgress({ done: size, total: size }); setLoaded(requestIndex);
      return () => controller.abort();
    }
    setCloudProgress({ done: 0, total: size });
    const load = async (i: number) => {
      const key = `${selected}:${coordinateKey}:${i}`;
      if (cache.current.has(key)) return cache.current.get(key)!;
      const frame = await readFrame(selected, i, controller.signal, mode === 'raw' ? undefined : job?.job_id);
      if (!controller.signal.aborted) cache.current.set(key, frame);
      return frame;
    };
    (async () => {
      const window: Frame[] = [];
      for (let batch = start; batch < end; batch += 25) {
        const next: Frame[] = [];
        for (let i = batch; i < Math.min(end, batch + 25); i += 4) {
          next.push(...await Promise.all(Array.from({ length: Math.min(4, Math.min(end, batch + 25) - i) }, (_, j) => load(i + j))));
          if (controller.signal.aborted) return;
        }
        window.push(...next);
        if (cachedCount < size / 2 || window.length === size) { setFrames([...window]); setFramesContext(frameContextKey); }
        setCloudProgress({ done: window.length, total: size });
        // Yield between chunks so zoom/pan/cancel remain responsive.
        await new Promise(resolve => setTimeout(resolve, 0));
        if (controller.signal.aborted) return;
      }
      setLoaded(requestIndex);
      for (let i = end; i < Math.min(end + 2, indices.length); i++) await load(i);
    })().catch(e => {
      if (controller.signal.aborted) return;
      setError((e as Error).message); setPlaying(false);
    });
    return () => controller.abort();
  }, [selected, indicesCapture, requestIndex, indices, settings.accumulation, mode, frameContextKey, fixedJob]);

  useEffect(() => {
    if (!playing || !indices.length) return;
    // Full-capture mode keeps the whole scene resident and never reloads per
    // frame, so playback must not gate on a window load; the timeline advances
    // directly against the full point set.
    const full = settings.accumulation === 0;
    let requested = playback.current.index;
    let due = performance.now() + Math.max(1, ((indices[requested + 1]?.time ?? indices[requested].time + 0.1) - indices[requested].time) * 1000 / speed);
    const timer = setInterval(() => {
      if (performance.now() < due) return;
      if (!full && playback.current.loaded !== requested) return;
      if (requested >= indices.length - 1) { setPlaying(false); return; }
      requested++; setIndex(requested);
      due += Math.max(1, ((indices[requested + 1]?.time ?? indices[requested].time + 0.1) - indices[requested].time) * 1000 / speed);
    }, 8);
    return () => clearInterval(timer);
  }, [playing, speed, indices, settings.accumulation]);

  // In MAP mode the raw accumulation window would bury the fused map, so only
  // the current frame is drawn next to it - a direct "current scan vs map" read.
  const visibleFrames = frames;
  const displayFrames = useMemo(() => mode === 'map' ? (fixedJob && chunks.size ? [] : visibleFrames.filter(frame => frame.meta.index === index)) : visibleFrames, [visibleFrames, mode, index, fixedJob, chunks.size]);
  const mapCloud = useMemo<MapCloud | null>(() => {
    if (mode !== 'map' || mapDataContext !== activeMapContext || !chunks.size) return null;
    let total = 0;
    for (const chunk of chunks.values()) total += chunk.voxels.length;
    if (!total) return null;
    const positions = new Float32Array(total * 3), intensity = new Float32Array(total), support = new Float32Array(total);
    let n = 0;
    for (const chunk of chunks.values()) for (const voxel of chunk.voxels) {
      positions[n * 3] = voxel[0]; positions[n * 3 + 1] = voxel[1]; positions[n * 3 + 2] = voxel[2];
      intensity[n] = Math.min(255, Math.max(0, voxel[3])); support[n] = voxel[4]; n++;
    }
    return { key: `${Array.from(chunks.keys()).sort().join('|')}#${total}`, positions, intensity, support };
  }, [chunks, mode, mapDataContext, activeMapContext]);
  const trajectoryLines = useMemo(() => {
    if (mode === 'raw' || fixedJob || !activeTrajectory) return undefined;
    return activeTrajectory.segments.map(segment => activeTrajectory.frames
      .filter(entry => entry.segment_id === segment.segment_id && entry.pose)
      .map(entry => [entry.pose![0][3], entry.pose![1][3], entry.pose![2][3]] as number[]));
  }, [activeTrajectory, mode, fixedJob]);
  const markers = useMemo<Marker[] | undefined>(() => {
    if (mode === 'raw' || fixedJob || !activeTrajectory) return undefined;
    return activeTrajectory.frames
      .filter(entry => FAILED_STATES.has(entry.state) && (entry.candidate_pose || entry.pose))
      .map(entry => {
        const pose = (entry.pose || entry.candidate_pose)!;
        return { position: [pose[0][3], pose[1][3], pose[2][3]], color: MARKER_RGB[entry.state] || [0.7, 0.7, 0.7] };
      });
  }, [activeTrajectory, mode, fixedJob]);
  const states = useMemo(() => {
    if (!activeTrajectory || !indices.length) return [] as string[];
    const byIndex = new Map(activeTrajectory.frames.map(entry => [entry.frame_index, entry.state]));
    return indices.map((_, i) => byIndex.get(i) || 'pending');
  }, [activeTrajectory, indices]);
  const failedFrames = useMemo(() => activeTrajectory ? activeTrajectory.frames.filter(entry => FAILED_STATES.has(entry.state)) : [], [activeTrajectory]);

  function select(id: string) { setSelected(id); setPlaying(false); setNotice(''); }
  function seek(value: number) { setPlaying(false); setIndex(value); }
  function play() {
    if (index === indices.length - 1) { setIndex(0); playback.current = { index: 0, loaded: -1 }; }
    if (settings.accumulation === 0 && cloudProgress.done < cloudProgress.total) {
      setNotice('整包点云正在后台加载，回放会按时间轴继续进行。');
    }
    setPlaying(v => !v);
  }
  async function importFile(file?: File) {
    if (!file || upload) return;
    setError(''); setNotice(''); setPlaying(false);
    const id = crypto.randomUUID();
    select(id); setUpload({ id, percent: 0 });
    const xhr = new XMLHttpRequest(); uploadRequest.current = xhr;
    xhr.open('POST', `/api/captures?id=${id}&filename=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = e => setUpload({ id, percent: e.lengthComputable ? Math.round(e.loaded / e.total * 100) : 0 });
    xhr.onload = () => {
      setUpload(null); uploadRequest.current = null;
      if (xhr.status >= 400) { try { setError(JSON.parse(xhr.responseText).detail); } catch { setError('导入失败'); } }
      void refresh().catch(e => setError(e.message));
    };
    xhr.onerror = () => { setUpload(null); uploadRequest.current = null; setError('文件上传失败，请检查本机服务'); };
    xhr.onabort = () => { setUpload(null); uploadRequest.current = null; };
    xhr.send(file);
  }
  async function startMapping() {
    if (!ready || (job && JOB_ACTIVE.has(job.status))) return;
    setPlaying(false); setError('');
    try {
      const requested = Math.max(0, Math.round(bootstrapFrames));
      const stationary = requested > 0 ? Math.min(requested, Math.max(1, capture?.frame_count || requested)) : 0;
      const config = stationary > 0 ? {
        initialization_mode: 'stationary_window', stationary_start_frame: 0,
        stationary_end_frame: stationary - 1, bootstrap_max_frames: stationary,
        bootstrap_max_seconds: Math.max(35, stationary * 0.1 + 1),
        stationary_declaration: `用户确认录制开头静止；使用前 ${stationary} 帧建立初始地图。`,
      } : undefined;
      const created = await createMappingJob(selected, config);
      setJobs(previous => [created, ...previous]); setJobId(created.job_id);
      setNotice('开始无 IMU 点云里程计；请在静态环境中缓慢移动雷达，完成后对照 RAW 与 MAP 视图检查重影。');
    } catch (e) { setError((e as Error).message); }
  }
  async function startFixedScene() {
    if (!ready || !fixedConfirmed || (job && JOB_ACTIVE.has(job.status))) return;
    setPlaying(false); setError('');
    try {
      const cutoff = indices[0].time + fixedSeconds;
      const firstAfter = fixedSeconds === 0 ? -1 : indices.findIndex(frame => frame.time > cutoff);
      const end = firstAfter < 0 ? indices.length - 1 : Math.max(0, firstAfter - 1);
      const created = await createMappingJob(selected, {
        pose_source: 'fixed_sensor', stationary_start_frame: 0, stationary_end_frame: end,
        stationary_declaration: `操作者确认 ${capture?.filename || selected} 第 0–${end} 帧雷达固定；仅融合已观测回波，不估计运动位姿。`,
      });
      setJobs(previous => [created, ...previous]); setJobId(created.job_id);
      setNotice('固定视点地图正在生成；完成后切换 MAP 查看。未观测区域不会补成实体表面。');
    } catch (e) { setError((e as Error).message); }
  }
  async function cancelMapping() {
    if (!job || !JOB_ACTIVE.has(job.status)) return;
    try { const updated = await cancelMappingJob(job.job_id); setJobs(previous => previous.map(entry => entry.job_id === updated.job_id ? updated : entry)); }
    catch (e) { setError((e as Error).message); }
  }
  async function exportEvidence() {
    if (!job || job.status !== 'ready' || exporting) return;
    setExporting(true);
    try {
      const manifest = await exportMappingJob(job.job_id);
      const link = document.createElement('a');
      link.href = `/api/mapping/jobs/${job.job_id}/export/${manifest.bundle}`;
      link.download = manifest.bundle; document.body.appendChild(link); link.click(); link.remove();
      setNotice(`已导出证据包 ${manifest.bundle}（${bytes(manifest.bundle_bytes)}），含轨迹、逐帧质量、地图与 validation.json。`);
    } catch (e) { setError((e as Error).message); }
    finally { setExporting(false); }
  }
  async function remove(id: string) {
    setPlaying(false);
    if (upload?.id === id) uploadRequest.current?.abort();
    try { await api(`/captures/${id}`, { method: 'DELETE' }); }
    catch (e) { if (!(e as Error).message.includes('不存在')) { setError((e as Error).message); return; } }
    if (selectedRef.current === id) setSelected('');
    setNotice('已移除导入副本，原始 PCAP 文件保留。');
    await refresh();
  }
  function setting<K extends keyof Settings>(key: K, value: Settings[K]) { setSettings(s => ({ ...s, [key]: value })); }
  const currentFrame = visibleFrames.find(f => f.meta.index === index);
  const current = currentFrame?.meta;
  const activeJob = job;
  return <div className="app" onDragOver={e => { e.preventDefault(); if (!upload) setDragging(true); }} onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false); }} onDrop={e => { e.preventDefault(); setDragging(false); void importFile(e.dataTransfer.files[0]); }}>
    <header className="header"><div className="brand"><span className="brand-icon"><Radio size={23}/></span><div><strong>NA70 <span>点云工作台</span></strong><small>POINT CLOUD STUDIO</small></div><span className="version">BETA 0.3</span></div>
      <PageNav/>
      <div className="header-right"><span className="local"><i/> 本机离线模式</span><button className="icon" title="操作帮助" onClick={() => setHelp(!help)}><CircleHelp size={18}/></button><button className="primary" disabled={!!upload} onClick={() => fileInput.current?.click()}><Plus size={17}/> 导入 PCAP</button></div>
      <input ref={fileInput} data-testid="file-input" type="file" accept=".pcap,.cap" hidden onChange={e => { void importFile(e.target.files?.[0]); e.target.value = ''; }}/>
    </header>
    <nav className="navigation"><span className="active"><Box size={16}/> 静态环境建图</span><span><Activity size={15}/> 纯点云里程计 <small>无 IMU · 无回环</small></span><span className="nav-tail">移动雷达 · 离线累计验证</span></nav>
    {(error || notice) && <div role="alert" className={`banner ${error ? 'error' : ''}`}>{error || notice}<button className="icon" onClick={() => { setError(''); setNotice(''); }}><X size={14}/></button></div>}
    {help && <div className="help"><strong>观察与测量点云</strong><p>默认左键拖动旋转视角：左右拖动水平环绕，上下拖动控制俯仰，画面跟随鼠标方向且不会翻转；右键拖动平移，滚轮向鼠标位置缩放。也可在画布左下切换“平移／旋转”。双击点云重新定位旋转中心。F 聚焦主体、R 查看全部、1/2/3 切换视角。</p><p>工具箱的“原点测距”会拾取点击位置附近的真实点，显示它到雷达／当前坐标原点的三维距离、XY 水平距离、ΔZ 与坐标。虚线是分量辅助线；结果随 RAW、ODOMETRY、MAP 所在坐标系解释。网格间距可在右侧设置，网格只作尺度参考，不代表重建地面。</p><p>建图流程：导入 PCAP → 检查帧索引 → 确认静态环境模式 → 开始点云配准 → 查看轨迹与失败帧 → 查看累计地图 → 对照 RAW 与 MAP → 导出地图与验收报告。</p><p>三种视图：RAW 是原始坐标叠加（会拖影，用于暴露问题）；ODOMETRY 是纯点云里程计位姿与轨迹；MAP 是后端体素融合地图。当前没有 IMU、去畸变、回环检测和位姿图优化，因此只能称为点云里程计／配准累计，不是完整 SLAM。真实无重影验收需要真实静态采集数据。</p><button onClick={() => setHelp(false)}>知道了</button></div>}
    <main className="workspace">
      <aside className="left-panel"><div className="panel-heading"><span>数据资源</span><span className="pill">{captures.length}</span></div><button className="file-drop" disabled={!!upload} onClick={() => fileInput.current?.click()}><Upload size={21}/><strong>选择或拖入文件</strong><small>经典 PCAP · NA70 点云</small></button>
        <div className="file-list">{captures.map(c => <div key={c.id} className={`file-row ${selected === c.id ? 'selected' : ''}`}><button className="file-select" onClick={() => select(c.id)}><FileBox size={20}/><span><strong title={c.filename}>{c.filename}</strong><small>{bytes(c.size)} <b>·</b> {statuses[c.status]}</small></span>{selected === c.id && <Check size={13}/>}</button><button className="delete-file" title={`移除 ${c.filename}`} onClick={() => void remove(c.id)}><Trash2 size={13}/></button></div>)}</div>
        <div className="file-details"><div className="section-label">文件信息</div>{capture ? <><h3>{capture.filename}</h3><dl><dt>文件大小</dt><dd>{bytes(capture.size)}</dd><dt>录制时长</dt><dd>{clock(capture.duration)}</dd><dt>帧总数</dt><dd>{capture.frame_count.toLocaleString()}</dd><dt>有效数据包</dt><dd>{capture.valid_packets.toLocaleString()}</dd><dt>异常数据包</dt><dd className={capture.invalid_packets ? 'warn-text' : ''}>{capture.invalid_packets}</dd><dt>忽略的其他包</dt><dd>{capture.ignored_packets}</dd><dt>PCAP SHA-256</dt><dd className="mono" title={capture.sha256}>{capture.sha256 ? `${capture.sha256.slice(0, 16)}…` : '—'}</dd></dl><div className="protocol"><span>解码配置</span><strong>NA70 / ROS2</strong><small>16 mm · 100 包/帧 · 正常安装</small></div></> : <p className="muted">导入文件后，在此查看录制信息。</p>}</div>
        <div className="analysis-card mapping-card"><span><Activity size={16}/> 建图任务</span>
          <div className="fixed-scene-controls">
            <strong>固定视点地图</strong>
            <p className="hint">雷达保持不动时，将同一坐标系中的真实回波融合为体素地图；不运行点云配准。</p>
            <select aria-label="固定视点时长" value={fixedSeconds} onChange={e => setFixedSeconds(Number(e.target.value))}>
              <option value={10}>录制开头 10 秒</option><option value={20}>录制开头 20 秒</option><option value={0}>整段录制</option>
            </select>
            <label><input type="checkbox" checked={fixedConfirmed} onChange={e => setFixedConfirmed(e.target.checked)}/> 我确认所选时段雷达固定不动</label>
            <button disabled={!ready || !fixedConfirmed || (!!job && JOB_ACTIVE.has(job.status))} onClick={() => void startFixedScene()}>生成固定视点地图</button>
          </div>
          <TurntableWorkbench captures={captures}/>
          <p>静态环境 → 缓慢移动雷达 → 后端配准与体素融合</p>
          <CollectionWorkbench captureId={selected} ready={ready} initializationFrames={bootstrapFrames} onPending={setPreflightPending}/>
          <button disabled={!ready || preflightPending || (!!job && JOB_ACTIVE.has(job.status))} onClick={() => void startMapping()}>开始点云配准</button>
          <label className="mapping-bootstrap">静止初始化帧数 <input aria-label="静止初始化帧数" type="number" min={0} max={300} step={1} value={bootstrapFrames} onChange={e => setBootstrapFrames(Math.max(0, Math.min(300, Number(e.target.value) || 0)))} /> <small>0=单帧；确认开头静止时可填 300</small></label>
          {jobs.length > 1 && <select className="mapping-job-select" aria-label="选择建图结果" value={activeJobId} onChange={e => { setJobId(e.target.value); setMode('raw'); if (settings.color === 'support') setting('color', 'height'); }}>{jobs.map(entry => <option key={entry.job_id} value={entry.job_id}>{entry.pose_source === 'fixed_sensor' ? '固定视点' : '里程计'} · {jobStatusLabel[entry.status] || entry.status} · {entry.job_id.slice(0, 8)}</option>)}</select>}
          {activeJob && fixedJob && <p className="hint">固定观测 {activeJob.metrics?.fixed_observation_frames ?? 0} / {activeJob.metrics?.selected_frame_count ?? '—'} 帧；坐标原点为本次录制的雷达位置。没有运行里程计，几何重影尚未验收。</p>}
          {activeJob && fixedJob && <details className="job-details" open><summary>固定地图：{activeJob.status === 'running' ? '融合中' : jobStatusLabel[activeJob.status] || activeJob.status} · {activeJob.progress.toFixed(1)}%</summary><div className="job-details-body"><p className="hint">{activeJob.fixed_sensor_declaration}</p><dl><dt>选定帧</dt><dd>{activeJob.first_frame_index}–{activeJob.last_frame_index}</dd><dt>固定观测 / 拒绝</dt><dd>{activeJob.metrics?.fixed_observation_frames ?? 0} / {activeJob.rejected_frames}</dd><dt>融合回波 / 体素</dt><dd>{activeJob.map_point_count.toLocaleString()} / {(activeJob.metrics?.map_voxel_count ?? 0).toLocaleString()}</dd><dt>地图边界</dt><dd className="mono">{box3(activeJob.map_bounds)}</dd><dt>输入哈希</dt><dd className="mono" title={activeJob.input_sha256 || ''}>{activeJob.input_sha256?.slice(0, 16) || '—'}…</dd><dt>配置版本</dt><dd className="mono">{activeJob.config_version}</dd></dl><p className="hint">仅固定雷达本地坐标；未观测区域保持空白，真实结构重影与几何误差仍待独立复核。</p>{activeJob.error && <p className="warn-text">{activeJob.error}</p>}<div className="job-actions">{JOB_ACTIVE.has(activeJob.status) && <button onClick={() => void cancelMapping()}>取消任务</button>}<button disabled={activeJob.status !== 'ready' || exporting} onClick={() => void exportEvidence()}><Download size={14}/> 导出地图及证据包</button></div></div></details>}
          {activeJob && !fixedJob && <details className="job-details" open><summary>任务状态：{jobStatusLabel[activeJob.status] || activeJob.status} · {activeJob.progress.toFixed(1)}%</summary><div className="job-details-body"><details><summary>任务采集证据：{activeJob.capture_preflight?.status || "未预检"}</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(activeJob.capture_preflight || { status: "未预检", metadata: null }, null, 2)}</pre></details><dl><dt>状态</dt><dd>{jobStatusLabel[activeJob.status] || activeJob.status}</dd><dt>进度</dt><dd>{activeJob.progress.toFixed(1)}%</dd><dt>初始化 / 配准 / 恢复</dt><dd>{activeJob.metrics?.initialization_frames ?? '—'} / {activeJob.metrics?.registered_frames ?? '—'} / {activeJob.metrics?.recovered_frames ?? '—'}</dd><dt>接受 / 失败</dt><dd>{activeJob.accepted_frames} / {activeJob.rejected_frames}</dd><dt>失锁帧</dt><dd className={activeJob.tracking_lost_frames ? 'warn-text' : ''}>{activeJob.tracking_lost_frames}</dd><dt>关键帧</dt><dd>{activeJob.keyframe_count}</dd><dt>地图点数</dt><dd>{activeJob.map_point_count.toLocaleString()}</dd><dt>体素数</dt><dd>{(activeJob.metrics?.map_voxel_count ?? 0).toLocaleString()}</dd><dt>地图边界</dt><dd className="mono">{box3(activeJob.map_bounds)}</dd><dt>首尾平移距离</dt><dd>{activeJob.metrics?.closure_translation_m != null ? `${activeJob.metrics.closure_translation_m.toFixed(3)} m` : '—'}</dd><dt>首尾旋转差</dt><dd>{activeJob.metrics?.closure_rotation_deg != null ? `${activeJob.metrics.closure_rotation_deg.toFixed(2)}°` : '—'}</dd><dt>轨迹长度</dt><dd>{activeJob.metrics?.trajectory_length_m != null ? `${activeJob.metrics.trajectory_length_m.toFixed(2)} m` : '—'}</dd><dt>配置版本</dt><dd className="mono">{activeJob.config_version}</dd><dt>代码版本</dt><dd className="mono">{activeJob.code_version}</dd><dt>阶段</dt><dd className="mono">{activeJob.stage}</dd><dt>IMU / 回环</dt><dd>未使用 / 未使用</dd><dt>耗时</dt><dd>{activeJob.elapsed_seconds != null ? `${activeJob.elapsed_seconds.toFixed(2)} s` : '—'}</dd><dt>峰值内存</dt><dd>{activeJob.peak_memory_mb != null ? `${activeJob.peak_memory_mb.toFixed(0)} MB` : '—'}</dd></dl>
            {activeJob.status === 'ready' && !fixedJob && activeJob.metrics?.registered_frames === 0 && <p className="warn-text">没有正式配准成功帧，不能评价移动累计地图。</p>}
            {activeJob.error && <p className="warn-text">{activeJob.error}</p>}
            <div className="job-actions">{JOB_ACTIVE.has(activeJob.status) && <button onClick={() => void cancelMapping()}>取消任务</button>}<button disabled={activeJob.status !== 'ready' || exporting} onClick={() => void exportEvidence()}><Download size={14}/> 导出证据包</button></div></div></details>}
          {!activeJob && ready && <p className="hint">尚未运行建图。点击上面的按钮开始；完成后轨迹、失败帧与地图会写入导入记录目录，重启后仍可查看。</p>}
          <p className="hint">固定视点地图仅融合用户确认静止的录制；移动雷达任务仍是纯点云里程计。两者都不代表完整 SLAM 或真实无重影验收。</p>
        </div><div className="privacy"><i/> 数据与分析结果保留在本机</div>
      </aside>
      <section className="viewer-column"><div className="viewer-toolbar"><div><span className="live-dot"/> 三维视图 <small>{capture?.filename || '等待导入数据'}</small></div>
          <div className="view-modes">{MODES.map(entry => <button key={entry.id} className={mode === entry.id ? 'active' : ''} title={entry.hint} disabled={(entry.id !== 'raw' && !jobReady) || (entry.id === 'odometry' && fixedJob)} onClick={() => { setPlaying(false); if (entry.id !== 'map' && settings.color === 'support') setting('color', 'height'); setMode(entry.id); }}>{entry.label}</button>)}</div>
          <div className="view-actions"><button className="focus-button" title="聚焦主体（F）" onClick={() => view.current?.view('reset')}>聚焦主体</button><button title="查看全部点的范围（R）" onClick={() => view.current?.view('all')}>全景</button>{[['top', '俯视'], ['front', '正视'], ['side', '侧视']].map(([name, label]) => <button key={name} onClick={() => view.current?.view(name)}>{label}</button>)}<span className="separator"/><button title="复位视角" onClick={() => view.current?.view('reset')}><RotateCcw size={15}/></button><button title="全屏" onClick={() => { void (document.fullscreenElement ? document.exitFullscreen() : viewport.current?.requestFullscreen()); }}><Maximize size={15}/></button></div></div>
        <div className="viewport" ref={viewport}>
          <CloudView ref={view} frames={displayFrames} settings={settings} capture={`${selected}:${mode}:${job?.job_id || ''}`} focusIndex={index} playbackActive={playing && settings.accumulation !== 0} map={mapCloud} trajectory={trajectoryLines} markers={markers} activeTool={activeTool} measurement={measurement} onMeasure={setMeasurement} onChunkNeeded={requestChunks} onStats={(count, fps, camera) => setStats({ count, fps, ...camera })}/>
          {selected && <div className="viewer-tools" aria-label="视图工具箱"><div className="toolbox-title">工具箱</div><div className="toolbox-buttons">
            <button className={activeTool === 'navigate' ? 'active' : ''} aria-pressed={activeTool === 'navigate'} title="浏览点云" onClick={() => setActiveTool('navigate')}><Hand size={15}/> 浏览</button>
            <button className={activeTool === 'range' ? 'active' : ''} aria-pressed={activeTool === 'range'} title="原点测距" onClick={() => setActiveTool('range')}><Ruler size={15}/> 原点测距</button>
            <button disabled={!measurement} title="清除测量" onClick={() => setMeasurement(null)}><Trash2 size={14}/><span className="sr-only">清除测量</span></button>
          </div>{activeTool === 'range' && !measurement && <p>点击点云，测量到当前坐标原点的距离</p>}
          {measurement && <div className="measurement-card" role="status" aria-label="测距结果"><div><span>三维距离</span><strong>{measurement.distance.toFixed(2)} m</strong></div><dl><dt>XY 水平</dt><dd>{measurement.horizontal.toFixed(2)} m</dd><dt>高度 ΔZ</dt><dd>{measurement.vertical >= 0 ? '+' : ''}{measurement.vertical.toFixed(2)} m</dd><dt>坐标 XYZ</dt><dd>{measurement.point.map(value => value.toFixed(2)).join(' / ')} m</dd></dl><small>基准：{mode === 'raw' ? '雷达原点' : mode === 'odometry' ? '首帧原点' : fixedJob ? '固定雷达原点' : '全局地图原点'}</small></div>}
          </div>}
          {!selected && <div className="empty-state"><div className="empty-glyph"><Box size={45} strokeWidth={1}/></div><span className="eyebrow">MAP A STATIC SCENE</span><h1>先静止采集，再缓慢移动雷达</h1><p>导入 PCAP，运行无 IMU 点云配准，检查墙面是否变厚、墙角是否出现双层、回到起点后是否错位。</p><button className="primary" onClick={() => fileInput.current?.click()}><FolderOpen size={17}/> 打开 PCAP 文件 <ArrowUpRight size={16}/></button><small>RAW / ODOMETRY / MAP 三种视图 · 失败帧不隐藏</small></div>}
          {(busy || upload?.id === selected) && <div className="processing"><LoaderCircle className="spin" size={28}/><h3>{upload?.id === selected ? '正在上传文件' : '正在建立帧索引'}</h3><progress max={100} value={upload?.id === selected ? upload.percent : capture?.progress}/><span>{upload?.id === selected ? upload.percent : capture?.progress}%</span><button onClick={() => void remove(selected)}>取消导入</button></div>}
          {capture?.status === 'error' && <div className="processing"><FileBox size={30}/><h3>无法读取此文件</h3><p>{capture.error}</p><button onClick={() => void remove(selected)}>移除记录</button></div>}
          {ready && cloudProgress.done < cloudProgress.total && <span className="loading-frame"><LoaderCircle className="spin" size={14}/> 正在汇集点云 {cloudProgress.done} / {cloudProgress.total} 帧</span>}
           {activeJob && JOB_ACTIVE.has(activeJob.status) && <span className="loading-frame mapping-progress"><LoaderCircle className="spin" size={14}/> {fixedJob ? '固定点云融合' : '后端点云配准'} {activeJob.progress.toFixed(1)}%{fixedJob ? '' : ` · 接受 ${activeJob.accepted_frames} / ${activeJob.frame_count} 帧`}</span>}
          {ready && <div className="canvas-meta"><span>{MODES.find(entry => entry.id === mode)?.label} · {settings.accumulation === 0 ? 'FULL CAPTURE' : `FRAME ${String(index + 1).padStart(5, '0')}`}</span><span>{mode === 'map' ? `体素地图 ${mapCloud ? (mapCloud.positions.length / 3).toLocaleString() : 0} 体素 · 当前视野分块 · ${fixedJob ? '固定视点' : '仅当前帧叠加'}` : mode === 'odometry' ? `${frames.length} 帧叠加 · 后端位姿与轨迹` : `${currentFrame ? (currentFrame.meta.complete ? '完整帧' : '不完整帧') : '等待当前帧'} · ${frames.length} 帧叠加 · 原始坐标 · 无运动补偿`}</span>{currentFrame?.motionMeta && <span className="motion-meta">{currentFrame.motionMeta.ready ? `前景 ${currentFrame.motionMeta.candidate_count.toLocaleString()} 点 · 移动 ${currentFrame.motionMeta.moving_count.toLocaleString()} 点` : '背景学习中'}</span>}{frameQuality && <span className="motion-meta" style={{ color: STRIP_COLOR[frameQuality.state] }}>{STATE_LABEL[frameQuality.state]} · 重叠 {frameQuality.overlap != null ? `${(frameQuality.overlap * 100).toFixed(1)}%` : '—'} · RMSE {frameQuality.rmse != null ? `${frameQuality.rmse.toFixed(3)} m` : '—'}</span>}</div>}
          <div className="navigation-pad"><div className="segmented"><button className={settings.navigation === 'pan' ? 'active' : ''} onClick={() => setting('navigation', 'pan')}>平移</button><button className={settings.navigation === 'rotate' ? 'active' : ''} onClick={() => setting('navigation', 'rotate')}>旋转</button></div><button title="放大" onClick={() => view.current?.view('in')}>＋</button><button title="缩小" onClick={() => view.current?.view('out')}>−</button><button title="聚焦主体" onClick={() => view.current?.view('reset')}>⌖</button></div><div className="canvas-bottom"><span className="axis-label"><b>X</b><b>Y</b><b>Z</b> <span>正交视图 · m · 网格 {settings.gridSize} m</span></span><span data-testid="camera-state" data-azimuth={stats.azimuth.toFixed(1)} data-polar={stats.polar.toFixed(1)}>{activeTool === 'range' ? '测距：点击点云 · 拖动仍可调整视角' : settings.navigation === 'pan' ? '左键平移 · 右键旋转' : '左键旋转（跟手） · 右键平移'} · 滚轮缩放 · 方位 {Math.round(stats.azimuth)}° 俯仰 {Math.round(stats.polar)}°</span></div>
          <div className="coordinate-frames">{[
            ['原始坐标系', '雷达当前帧 / 解码输出'],
            ['首帧坐标系', '配准位姿的参考系'],
            ['局部子地图坐标系', '最近关键帧组成，用于精配准'],
            ['全局地图坐标系', '体素融合地图，输出于此'],
          ].map(([name, note]) => <span key={name}><b>{name}</b>{note}</span>)}</div>
        </div>
        <div className="timeline"><div className="timeline-top"><span><Layers size={14}/> 全帧回放</span><span className="mono">{clock(indices[index]?.time || 0)} <b>/ {clock(capture?.duration || 0)}</b></span></div><div className="timeline-subline"><span>按原始时间逐帧浏览整段 PCAP</span><span>{mode === 'map' && fixedJob ? '固定视点地图' : settings.accumulation === 0 ? '整包点云' : `显示窗口 ${settings.accumulation} 帧`}</span></div><input aria-label="回放时间轴" type="range" min={0} max={Math.max(0, indices.length - 1)} value={index} disabled={!ready} onChange={e => seek(+e.target.value)}/><details className="frame-status-details"><summary>帧状态概览（点击跳转）</summary><FailureStrip states={states} onSeek={seek}/><div className="frame-status-legend"><span><i className="state-key accepted"/>已接受</span><span><i className="state-key warning"/>质量问题</span><span><i className="state-key lost"/>跟踪失锁</span></div></details><div className="playback-controls"><div className="transport"><button title="回到第 1 帧" aria-label="回到第 1 帧" disabled={!ready} onClick={() => seek(0)}>⏮</button><button title="上一帧" aria-label="上一帧" disabled={!ready || index === 0} onClick={() => seek(index - 1)}><ChevronLeft size={20}/></button><button className="play" title={playing ? '暂停' : '播放'} aria-label={playing ? '暂停全帧回放' : '播放全部帧'} disabled={!ready} onClick={play}>{playing ? <Pause size={19}/> : <Play size={19}/>}</button><button title="下一帧" aria-label="下一帧" disabled={!ready || index === indices.length - 1} onClick={() => seek(index + 1)}><ChevronRight size={20}/></button><select aria-label="播放速度" value={speed} onChange={e => setSpeed(+e.target.value)}>{[0.5, 1, 2].map(v => <option key={v} value={v}>{v}× 速度</option>)}</select></div><span className="frame-counter">第 <strong>{ready ? index + 1 : '—'}</strong> / {indices.length || '—'} 帧</span></div></div>
        <div className="statusbar"><span><i/> {playing ? '正在回放' : ready ? '就绪' : '等待数据'}{playing && settings.accumulation !== 0 && (settings.budget === 0 || settings.budget > 1_000_000) ? ' · 预览最多 100 万点，暂停恢复完整密度' : ''}</span><span data-testid="point-count">显示 {stats.count.toLocaleString()} 点 <b>·</b> 原始 {frames.reduce((s, f) => s + f.meta.point_count, 0).toLocaleString()} 点 <b>·</b> <span data-testid="fps">{stats.fps}</span> FPS{mode === 'map' && mapMeta ? ` · 地图分块 ${chunks.size} / ${mapMeta.chunk_count}` : ''}</span></div>
      </section>
      <aside className="right-panel"><div className="panel-heading"><span><Settings2 size={16}/> 显示设置</span><button className="icon" title="重置显示设置" onClick={() => setSettings(defaults)}><RotateCcw size={14}/></button></div>
        {activeJob && fixedJob && <details className="mapping-quality-group"><summary>固定视点帧状态</summary><div className="settings-section mapping-quality">{frameQuality ? <><p><strong>{STATE_LABEL[frameQuality.state] || frameQuality.state}</strong> · 第 {frameQuality.index + 1} 帧</p><p className="hint">有效回波 {frameQuality.point_count.toLocaleString()} · {frameQuality.reason}</p></> : <p className="hint">选择时间轴上的帧查看是否参与固定地图。</p>}<p className="hint">固定观测不计算配准重叠率或 RMSE；记录中的位姿来自雷达固定声明。</p></div></details>}
        {activeJob && !fixedJob && <details className="mapping-quality-group"><summary>当前帧配准质量</summary><div className="settings-section mapping-quality"><div className="section-label">当前帧配准质量</div>
          {frameQuality ? <><div className="quality-head"><span className="state-dot" style={{ background: STRIP_COLOR[frameQuality.state] }}/><strong>{STATE_LABEL[frameQuality.state] || frameQuality.state}</strong><small>{frameQuality.reason}</small></div><dl><dt>帧号</dt><dd>{frameQuality.index} · {clock(frameQuality.timestamp)}</dd><dt>点数</dt><dd>{frameQuality.point_count.toLocaleString()}</dd><dt>重叠率</dt><dd>{frameQuality.overlap != null ? `${(frameQuality.overlap * 100).toFixed(1)}%` : '—'}</dd><dt>RMSE</dt><dd>{frameQuality.rmse != null ? `${frameQuality.rmse.toFixed(4)} m` : '—'}</dd><dt>平移增量</dt><dd>{frameQuality.translation_step_m != null ? `${frameQuality.translation_step_m.toFixed(4)} m` : '—'}</dd><dt>旋转增量</dt><dd>{frameQuality.rotation_step_deg != null ? `${frameQuality.rotation_step_deg.toFixed(3)}°` : '—'}</dd><dt>帧内点间距中位数</dt><dd>{frameQuality.spacing?.within_frame?.p50 != null ? `${frameQuality.spacing.within_frame.p50.toFixed(3)} m` : '—'}</dd><dt>子地图帧数</dt><dd>{frameQuality.submap_frame_count ?? '—'}</dd><dt>迭代次数</dt><dd>{frameQuality.iterations ?? '—'}</dd><dt>关键帧</dt><dd>{frameQuality.keyframe ? '是' : '否'} · 段 {frameQuality.segment_id}</dd></dl></> : <p className="hint">尚未运行建图，或该帧没有后端记录。</p>}
          <p className="hint">失败帧不会被隐藏：状态颜色为绿=接受，黄=质量不达标，橙=几何退化，红=跟踪失锁，灰=未处理。失锁后的帧不参与地图融合。</p>
        </div></details>}
        {jobReady && !fixedJob && trajectory && <details className="mapping-quality-group"><summary>轨迹与失败帧 · {trajectory.segments.length} 段 · {failedFrames.length} 个</summary><div className="settings-section mapping-quality"><div className="section-label">轨迹与失败帧 · {trajectory.segments.length} 段 · {failedFrames.length} 个失败帧</div>
          <div className="segment-list">{trajectory.segments.map(segment => <div key={segment.segment_id} className="segment-row"><strong>段 {segment.segment_id}</strong><span>帧 {segment.start_frame_index}–{segment.end_frame_index} · 长度 {segment.length_m.toFixed(2)} m{segment.closure ? ` · 首尾 ${segment.closure.translation_m.toFixed(3)} m / ${segment.closure.rotation_deg.toFixed(2)}°` : ''}</span></div>)}</div>
          {failedFrames.length > 0 && <div className="failed-list">{failedFrames.slice(0, 40).map(entry => <button key={entry.frame_index} className="failed-row" onClick={() => seek(entry.frame_index)}><span className="state-dot" style={{ background: STRIP_COLOR[entry.state] }}/><strong>#{entry.frame_index}</strong><span>{STATE_LABEL[entry.state]}</span><small>{entry.reason}</small></button>)}{failedFrames.length > 40 && <p className="hint">仅列出前 40 个失败帧，完整记录见 frame_quality.jsonl。</p>}</div>}
          <p className="hint">段与段之间没有回环约束，坐标不能直接拼接；重新初始化会开启新的一段。</p>
        </div></details>}
        {mapMeta && <details className="mapping-quality-group"><summary>地图结果 · {mapMeta.voxel_count.toLocaleString()} 体素</summary><div className="settings-section mapping-quality"><div className="section-label">地图结果</div><dl><dt>体素分辨率</dt><dd>{mapMeta.voxel_size} m</dd><dt>融合点数</dt><dd>{mapMeta.fused_points.toLocaleString()}</dd><dt>体素数</dt><dd>{mapMeta.voxel_count.toLocaleString()}</dd><dt>分块数</dt><dd>{mapMeta.chunk_count}（已载入 {chunks.size}）</dd><dt>地图边界</dt><dd className="mono">{box3(mapMeta)}</dd><dt>输入哈希</dt><dd className="mono" title={mapMeta.input_sha256 || ''}>{mapMeta.input_sha256 ? `${mapMeta.input_sha256.slice(0, 16)}…` : '—'}</dd><dt>配置版本</dt><dd className="mono">{mapMeta.config_version}</dd><dt>体素最近来源段</dt><dd>{mapMeta.segments.map(segment => `段 ${segment.segment_id}: ${segment.voxels} 体素`).join(' · ') || '—'}</dd></dl><p className="hint">地图按视野请求 <code>map/chunks</code>：只读取当前视野附近的分块，不把整幅地图发给浏览器。</p></div></details>}
        <details className="settings-group" open><summary>显示参数与回放</summary><div className="settings-group-body"><div className="settings-section"><div className="section-label">点云着色</div><div className="segmented">{[['height', '高度'], ['intensity', '反射率'], ['distance', '距离'], ...(fixedJob && mode === 'map' ? [['support', '回波数']] : [])].map(([value, label]) => <button key={value} className={settings.color === value ? 'active' : ''} onClick={() => setting('color', value)}>{label}</button>)}</div><div className="setting-heading"><label htmlFor="point-size">点大小</label><output>{settings.size} px</output></div><input id="point-size" type="range" min={1} max={6} step={0.5} value={settings.size} onChange={e => setting('size', +e.target.value)}/><div className="setting-heading"><label htmlFor="opacity">透明度</label><output>{Math.round(settings.opacity * 100)}%</output></div><input id="opacity" type="range" min={0.1} max={1} step={0.05} value={settings.opacity} onChange={e => setting('opacity', +e.target.value)}/></div>
        <div className="settings-section"><div className="section-label">空间过滤</div><label>距雷达距离 <small>m</small></label><div className="range-inputs"><input aria-label="最小距离" type="number" min={0} max={settings.max} value={settings.min} onChange={e => setting('min', Math.max(0, Math.min(settings.max, +e.target.value)))}/><span>—</span><input aria-label="最大距离" type="number" min={settings.min} max={1200} value={settings.max} onChange={e => setting('max', Math.max(settings.min, Math.min(1200, +e.target.value)))}/></div><p className="hint">仅过滤显示，不改变原始点数据。零距离点不绘制。</p></div>
        <div className="settings-section"><div className="section-label">场景叠加</div><select aria-label="叠加帧数" value={settings.accumulation} onChange={e => { setPlaying(false); setting('accumulation', +e.target.value); }}>{[1, 5, 10, 20, 100, 200, 500, 0].map(v => <option key={v} value={v}>{v === 0 ? '整包 · 全部真实点' : v === 1 ? '单帧 · 原始回放' : `${v} 帧密集叠加${v >= 100 ? ` · 约 ${v * 2} 万点` : ''}`}</option>)}</select><button className="all-points-button" disabled={!ready} onClick={() => { setPlaying(false); setSettings(s => ({ ...s, accumulation: 0, budget: 0 })); }}>加载整包全部点</button><p className="hint">RAW 视图的叠加不含运动补偿，会拖影；这正是要暴露的问题。MAP 视图只叠加当前帧与后端融合地图。</p><label className="toggle-row">坐标网格<input type="checkbox" checked={settings.grid} onChange={e => setting('grid', e.target.checked)}/></label><div className="grid-scale-row"><label htmlFor="grid-size">每格距离</label><select id="grid-size" aria-label="网格每格距离" value={settings.gridSize} disabled={!settings.grid} onChange={e => setting('gridSize', +e.target.value)}>{[1, 2, 5, 10, 20, 50].map(value => <option key={value} value={value}>{value} m</option>)}</select></div><p className="hint">网格以当前坐标系 XY 平面为参考，只表达尺度，不代表已识别或重建的地面。</p><label className="toggle-row">坐标轴<input type="checkbox" checked={settings.axes} onChange={e => setting('axes', e.target.checked)}/></label></div>
        <div className="settings-section"><div className="section-label">渲染预算</div><select aria-label="渲染点数上限" value={settings.budget} onChange={e => setting('budget', +e.target.value)}>{[0, 1000000, 4000000, 8000000].map(v => <option key={v} value={v}>{v === 0 ? '全部点 · 不抽样（默认）' : `最多 ${v / 10000} 万点`}</option>)}</select><p className="hint">默认保留全部非零真实点。仅在显存不足时手动降低点数预算。</p></div></div></details>
        {capture && capture.warnings.length > 0 && <div className="warnings"><strong>数据提示</strong>{capture.warnings.map((w, i) => <p key={i}>{w}</p>)}</div>}
        <div className="coordinate-note"><Box size={16}/><span>{mode === 'map' ? (fixedJob ? '固定雷达本地地图' : '全局地图坐标系') : mode === 'odometry' ? '首帧坐标系' : 'ROS2 原始坐标系'}<br/><small>XYZI / Float32</small></span></div>
      </aside>
    </main>
    {dragging && <div className="drop-overlay"><Upload size={40}/><h2>释放文件，开始静态环境建图</h2><p>文件仅上传至本机服务</p></div>}
  </div>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);

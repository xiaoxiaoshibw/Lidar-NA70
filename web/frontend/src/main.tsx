import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, ArrowUpRight, Box, Check, ChevronLeft, ChevronRight, CircleHelp, FileBox, FolderOpen, Layers, LoaderCircle, Maximize, Pause, Play, Plus, Radio, RotateCcw, Settings2, Trash2, Upload, X } from 'lucide-react';
import { api, Capture, clock, Frame, FrameInfo, readFrame } from './api';
import { CloudView, Settings, ViewHandle } from './CloudView';
import './style.css';

const defaults: Settings = { color: 'height', size: 2, opacity: 1, min: 0, max: 1200, accumulation: 200, grid: true, axes: true, budget: 0, navigation: 'pan' };
const statuses: Record<string, string> = { ready: '可回放', uploading: '上传中', indexing: '建立帧索引', error: '导入失败' };
const bytes = (n: number) => `${(n / 1024 / 1024).toFixed(1)} MB`;

function App() {
  const [captures, setCaptures] = useState<Capture[]>([]);
  const [selected, setSelected] = useState('');
  const selectedRef = useRef(''); selectedRef.current = selected;
  const capture = captures.find(c => c.id === selected);
  const [indices, setIndices] = useState<FrameInfo[]>([]);
  const [frames, setFrames] = useState<Frame[]>([]);
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
  const [stats, setStats] = useState({ count: 0, fps: 0 });
  const [help, setHelp] = useState(false);
  const viewport = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);
  const busy = capture?.status === 'uploading' || capture?.status === 'indexing';
  const ready = capture?.status === 'ready' && indices.length > 0;

  async function refresh() { const list = await api<Capture[]>('/captures'); setCaptures(list.sort((a, b) => b.created_at - a.created_at)); return list; }
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try { const list = await api<Capture[]>('/captures'); if (alive) setCaptures(list.sort((a, b) => b.created_at - a.created_at)); }
      catch (e) { if (alive) setError(`服务连接失败：${(e as Error).message}`); }
    };
    void poll(); const timer = setInterval(poll, 800);
    return () => { alive = false; clearInterval(timer); uploadRequest.current?.abort(); };
  }, []);

  useEffect(() => {
    setPlaying(false); setIndex(0); setLoaded(-1); setFrames([]); setIndices([]); cache.current.clear(); setError(''); setCloudProgress({ done: 0, total: 0 });
    if (!selected || capture?.status !== 'ready') return;
    const controller = new AbortController();
    (async () => {
      const result: FrameInfo[] = [];
      let total = 1;
      while (result.length < total) {
        const page = await api<{ total: number; frames: FrameInfo[] }>(`/captures/${selected}/frames?offset=${result.length}&limit=2000`, { signal: controller.signal });
        result.push(...page.frames); total = page.total;
      }
      if (!controller.signal.aborted) setIndices(result);
    })().catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [selected, capture?.status]);

  useEffect(() => {
    if (!selected || !indices.length) return;
    const controller = new AbortController();
    // Dense inspection includes a complete window even at frame zero.
    // Full-file view is progressive and independent of the timeline cursor.
    const size = settings.accumulation === 0 ? indices.length : Math.min(settings.accumulation, indices.length);
    const start = settings.accumulation === 0 ? 0 : Math.min(Math.max(0, requestIndex - size + 1), indices.length - size);
    const end = start + size;
    const cachedWindow = Array.from({ length: size }, (_, i) => cache.current.get(`${selected}:${start + i}`));
    const cachedCount = cachedWindow.filter(Boolean).length;
    for (const key of cache.current.keys()) {
      const i = Number(key.split(':')[1]);
      if (!key.startsWith(selected + ':') || i < start || i > end + 1) cache.current.delete(key);
    }
    if (cachedCount === size) {
      const complete = cachedWindow as Frame[];
      setFrames(previous => previous.length === size && previous[0] === complete[0] && previous.at(-1) === complete.at(-1) ? previous : complete);
      setCloudProgress({ done: size, total: size }); setLoaded(requestIndex);
      return () => controller.abort();
    }
    setCloudProgress({ done: 0, total: size });
    const load = async (i: number) => {
      const key = `${selected}:${i}`;
      if (cache.current.has(key)) return cache.current.get(key)!;
      const frame = await readFrame(selected, i, controller.signal);
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
        if (cachedCount < size / 2 || window.length === size) setFrames([...window]);
        setCloudProgress({ done: window.length, total: size });
        // Yield between chunks so zoom/pan/cancel remain responsive.
        await new Promise(resolve => setTimeout(resolve, 0));
        if (controller.signal.aborted) return;
      }
      setLoaded(requestIndex);
      for (let i = end; i < Math.min(end + 2, indices.length); i++) await load(i);
    })().catch(e => { if (!controller.signal.aborted) { setError(e.message); setPlaying(false); } });
    return () => controller.abort();
  }, [selected, requestIndex, indices, settings.accumulation]);

  useEffect(() => {
    if (!playing || !indices.length) return;
    let requested = playback.current.index;
    let due = performance.now() + Math.max(1, ((indices[requested + 1]?.time ?? indices[requested].time + 0.1) - indices[requested].time) * 1000 / speed);
    const timer = setInterval(() => {
      if (playback.current.loaded !== requested || performance.now() < due) return;
      if (requested >= indices.length - 1) { setPlaying(false); return; }
      requested++; setIndex(requested);
      due += Math.max(1, ((indices[requested + 1]?.time ?? indices[requested].time + 0.1) - indices[requested].time) * 1000 / speed);
    }, 8);
    return () => clearInterval(timer);
  }, [playing, speed, indices]);

  function select(id: string) { setSelected(id); setPlaying(false); setNotice(''); }
  function seek(value: number) { setPlaying(false); setIndex(value); }
  function play() {
    if (settings.accumulation === 0) {
      setting('accumulation', 200); setNotice('已切换为 200 帧密集回放；如需观察单帧运动，可在叠加设置中选择单帧。');
    }
    if (index === indices.length - 1) { setIndex(0); playback.current = { index: 0, loaded: -1 }; }
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
  const current = frames.find(f => f.meta.index === index)?.meta;
  return <div className="app" onDragOver={e => { e.preventDefault(); if (!upload) setDragging(true); }} onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false); }} onDrop={e => { e.preventDefault(); setDragging(false); void importFile(e.dataTransfer.files[0]); }}>
    <header className="header"><div className="brand"><span className="brand-icon"><Radio size={23}/></span><div><strong>NA70 <span>点云工作台</span></strong><small>POINT CLOUD STUDIO</small></div><span className="version">BETA 0.2</span></div>
      <div className="header-right"><span className="local"><i/> 本机离线模式</span><button className="icon" title="操作帮助" onClick={() => setHelp(!help)}><CircleHelp size={18}/></button><button className="primary" disabled={!!upload} onClick={() => fileInput.current?.click()}><Plus size={17}/> 导入 PCAP</button></div>
      <input ref={fileInput} data-testid="file-input" type="file" accept=".pcap,.cap" hidden onChange={e => { void importFile(e.target.files?.[0]); e.target.value = ''; }}/>
    </header>
    <nav className="navigation"><span className="active"><Box size={16}/> 点云回放</span><span className="future"><Activity size={15}/> 运动分析 <small>规划中</small></span><span className="future"><Radio size={15}/> 实时雷达 <small>规划中</small></span><span className="nav-tail">双楔镜激光雷达 <span>/</span> 数据可视化</span></nav>
    {(error || notice) && <div role="alert" className={`banner ${error ? 'error' : ''}`}>{error || notice}<button className="icon" onClick={() => { setError(''); setNotice(''); }}><X size={14}/></button></div>}
    {help && <div className="help"><strong>观察点云</strong><p>默认左键平移、右键旋转，滚轮向鼠标位置缩放；可切换左键操作。双击点云重新定位旋转中心。F 聚焦主体、R 查看全部、1/2/3 切换视角。导入后默认叠加 200 帧，不抽样；整包模式可加载全部真实点。叠加用于观察场景，运动可能产生拖影。</p><p>帧按 100 个有效 UDP 包组成。颜色不代表模型判断；运动检测尚未接入。录制文件和索引仅保存在本机。</p><button onClick={() => setHelp(false)}>知道了</button></div>}
    <main className="workspace">
      <aside className="left-panel"><div className="panel-heading"><span>数据资源</span><span className="pill">{captures.length}</span></div><button className="file-drop" disabled={!!upload} onClick={() => fileInput.current?.click()}><Upload size={21}/><strong>选择或拖入文件</strong><small>经典 PCAP · NA70 点云</small></button>
        <div className="file-list">{captures.map(c => <div key={c.id} className={`file-row ${selected === c.id ? 'selected' : ''}`}><button className="file-select" onClick={() => select(c.id)}><FileBox size={20}/><span><strong title={c.filename}>{c.filename}</strong><small>{bytes(c.size)} <b>·</b> {statuses[c.status]}</small></span>{selected === c.id && <Check size={13}/>}</button><button className="delete-file" title={`移除 ${c.filename}`} onClick={() => void remove(c.id)}><Trash2 size={13}/></button></div>)}</div>
        <div className="file-details"><div className="section-label">文件信息</div>{capture ? <><h3>{capture.filename}</h3><dl><dt>文件大小</dt><dd>{bytes(capture.size)}</dd><dt>录制时长</dt><dd>{clock(capture.duration)}</dd><dt>帧总数</dt><dd>{capture.frame_count.toLocaleString()}</dd><dt>有效数据包</dt><dd>{capture.valid_packets.toLocaleString()}</dd><dt>异常数据包</dt><dd className={capture.invalid_packets ? 'warn-text' : ''}>{capture.invalid_packets}</dd><dt>忽略的其他包</dt><dd>{capture.ignored_packets}</dd></dl><div className="protocol"><span>解码配置</span><strong>NA70 / ROS2</strong><small>16 mm · 100 包/帧 · 正常安装</small></div></> : <p className="muted">导入文件后，在此查看录制信息。</p>}</div>
        <div className="analysis-card"><span><Activity size={16}/> 运动状态</span><strong>未分析</strong><p>首版提供原始点云回放<br/>运动分析将在后续接入</p></div><div className="privacy"><i/> 数据保留在本机</div>
      </aside>
      <section className="viewer-column"><div className="viewer-toolbar"><div><span className="live-dot"/> 三维视图 <small>{capture?.filename || '等待导入数据'}</small></div><div className="view-actions"><button className="focus-button" title="聚焦主体（F）" onClick={() => view.current?.view('reset')}>聚焦主体</button><button title="查看全部点的范围（R）" onClick={() => view.current?.view('all')}>全景</button>{[['top', '俯视'], ['front', '正视'], ['side', '侧视']].map(([name, label]) => <button key={name} onClick={() => view.current?.view(name)}>{label}</button>)}<span className="separator"/><button title="复位视角" onClick={() => view.current?.view('reset')}><RotateCcw size={15}/></button><button title="全屏" onClick={() => { void (document.fullscreenElement ? document.exitFullscreen() : viewport.current?.requestFullscreen()); }}><Maximize size={15}/></button></div></div>
        <div className="viewport" ref={viewport}>
          <CloudView ref={view} frames={frames} settings={settings} capture={selected} onStats={(count, fps) => setStats({ count, fps })}/>
          {!selected && <div className="empty-state"><div className="empty-glyph"><Box size={45} strokeWidth={1}/></div><span className="eyebrow">EXPLORE YOUR POINT CLOUD</span><h1>从一次扫描，看见三维空间</h1><p>导入雷达录制文件，旋转、回放并探索真实点云。</p><button className="primary" onClick={() => fileInput.current?.click()}><FolderOpen size={17}/> 打开 PCAP 文件 <ArrowUpRight size={16}/></button><small>默认 200 帧密集点云 · 可加载整包全部点</small></div>}
          {(busy || upload?.id === selected) && <div className="processing"><LoaderCircle className="spin" size={28}/><h3>{upload?.id === selected ? '正在上传文件' : '正在建立帧索引'}</h3><progress max={100} value={upload?.id === selected ? upload.percent : capture?.progress}/><span>{upload?.id === selected ? upload.percent : capture?.progress}%</span><button onClick={() => void remove(selected)}>取消导入</button></div>}
          {capture?.status === 'error' && <div className="processing"><FileBox size={30}/><h3>无法读取此文件</h3><p>{capture.error}</p><button onClick={() => void remove(selected)}>移除记录</button></div>}
          {ready && cloudProgress.done < cloudProgress.total && <span className="loading-frame"><LoaderCircle className="spin" size={14}/> 正在汇集点云 {cloudProgress.done} / {cloudProgress.total} 帧</span>}
          {ready && <div className="canvas-meta"><span>{settings.accumulation === 0 ? 'FULL CAPTURE' : `FRAME ${String(index + 1).padStart(5, '0')}`}</span><span>{settings.accumulation === 0 ? '整包场景' : current?.complete ? '完整帧' : '不完整帧'} · {settings.accumulation === 1 ? '单帧显示' : `${frames.length} 帧叠加`} · {settings.budget === 0 ? '全部真实点 · 无抽样' : '按预算显示'}</span></div>}
          <div className="navigation-pad"><div className="segmented"><button className={settings.navigation === 'pan' ? 'active' : ''} onClick={() => setting('navigation', 'pan')}>平移</button><button className={settings.navigation === 'rotate' ? 'active' : ''} onClick={() => setting('navigation', 'rotate')}>旋转</button></div><button title="放大" onClick={() => view.current?.view('in')}>＋</button><button title="缩小" onClick={() => view.current?.view('out')}>−</button><button title="聚焦主体" onClick={() => view.current?.view('reset')}>⌖</button></div><div className="canvas-bottom"><span className="axis-label"><b>X</b><b>Y</b><b>Z</b> <span>正交视图 · m · 参考网格 10 m</span></span><span>{settings.navigation === 'pan' ? '左键平移 · 右键旋转' : '左键旋转 · 右键平移'} · 滚轮缩放 · 双击定位</span></div>
        </div>
        <div className="timeline"><div className="timeline-top"><span><Layers size={14}/> 回放时间轴</span><span className="mono">{clock(indices[index]?.time || 0)} <b>/ {clock(capture?.duration || 0)}</b></span></div><input aria-label="回放时间轴" type="range" min={0} max={Math.max(0, indices.length - 1)} value={index} disabled={!ready} onChange={e => seek(+e.target.value)}/><div className="playback-controls"><div className="transport"><button title="上一帧" disabled={!ready || index === 0} onClick={() => seek(index - 1)}><ChevronLeft size={20}/></button><button className="play" title={playing ? '暂停' : '播放'} disabled={!ready} onClick={play}>{playing ? <Pause size={19}/> : <Play size={19}/>}</button><button title="下一帧" disabled={!ready || index === indices.length - 1} onClick={() => seek(index + 1)}><ChevronRight size={20}/></button><select aria-label="播放速度" value={speed} onChange={e => setSpeed(+e.target.value)}>{[0.5, 1, 2].map(v => <option key={v} value={v}>{v}× 速度</option>)}</select></div><span className="frame-counter">第 <strong>{ready ? index + 1 : '—'}</strong> / {indices.length || '—'} 帧</span></div></div>
        <div className="statusbar"><span><i/> {playing ? '正在回放' : ready ? '就绪' : '等待数据'}</span><span data-testid="point-count">显示 {stats.count.toLocaleString()} 点 <b>·</b> 原始 {frames.reduce((s, f) => s + f.meta.point_count, 0).toLocaleString()} 点 <b>·</b> <span data-testid="fps">{stats.fps}</span> FPS</span></div>
      </section>
      <aside className="right-panel"><div className="panel-heading"><span><Settings2 size={16}/> 显示设置</span><button className="icon" title="重置显示设置" onClick={() => setSettings(defaults)}><RotateCcw size={14}/></button></div>
        <div className="settings-section"><div className="section-label">点云着色</div><div className="segmented">{[['height', '高度'], ['intensity', '反射率'], ['distance', '距离']].map(([value, label]) => <button key={value} className={settings.color === value ? 'active' : ''} onClick={() => setting('color', value)}>{label}</button>)}</div><div className="setting-heading"><label htmlFor="point-size">点大小</label><output>{settings.size} px</output></div><input id="point-size" type="range" min={1} max={6} step={0.5} value={settings.size} onChange={e => setting('size', +e.target.value)}/><div className="setting-heading"><label htmlFor="opacity">透明度</label><output>{Math.round(settings.opacity * 100)}%</output></div><input id="opacity" type="range" min={0.1} max={1} step={0.05} value={settings.opacity} onChange={e => setting('opacity', +e.target.value)}/></div>
        <div className="settings-section"><div className="section-label">空间过滤</div><label>距雷达距离 <small>m</small></label><div className="range-inputs"><input aria-label="最小距离" type="number" min={0} max={settings.max} value={settings.min} onChange={e => setting('min', Math.max(0, Math.min(settings.max, +e.target.value)))}/><span>—</span><input aria-label="最大距离" type="number" min={settings.min} max={1200} value={settings.max} onChange={e => setting('max', Math.max(settings.min, Math.min(1200, +e.target.value)))}/></div><p className="hint">仅过滤显示，不改变原始点数据。零距离点不绘制。</p></div>
        <div className="settings-section"><div className="section-label">场景叠加</div><select aria-label="叠加帧数" value={settings.accumulation} onChange={e => { setPlaying(false); setting('accumulation', +e.target.value); }}>{[1, 5, 10, 20, 100, 200, 500, 0].map(v => <option key={v} value={v}>{v === 0 ? '整包 · 全部真实点' : v === 1 ? '单帧 · 原始回放' : `${v} 帧密集叠加${v >= 100 ? ` · 约 ${v * 2} 万点` : ''}`}</option>)}</select><button className="all-points-button" disabled={!ready} onClick={() => { setPlaying(false); setSettings(s => ({ ...s, accumulation: 0, budget: 0 })); }}>加载整包全部点</button><p className="hint">默认 200 帧，文件开头也显示完整窗口。整包渐进加载，全部点均来自真实采集，移动轨迹会叠加。</p><label className="toggle-row">坐标网格<input type="checkbox" checked={settings.grid} onChange={e => setting('grid', e.target.checked)}/></label><label className="toggle-row">坐标轴<input type="checkbox" checked={settings.axes} onChange={e => setting('axes', e.target.checked)}/></label></div>
        <div className="settings-section"><div className="section-label">渲染预算</div><select aria-label="渲染点数上限" value={settings.budget} onChange={e => setting('budget', +e.target.value)}>{[0, 1000000, 4000000, 8000000].map(v => <option key={v} value={v}>{v === 0 ? '全部点 · 不抽样（默认）' : `最多 ${v / 10000} 万点`}</option>)}</select><p className="hint">默认保留全部非零真实点。仅在显存不足时手动降低点数预算。</p></div>
        {capture && capture.warnings.length > 0 && <div className="warnings"><strong>数据提示</strong>{capture.warnings.map((w, i) => <p key={i}>{w}</p>)}</div>}
        <div className="coordinate-note"><Box size={16}/><span>ROS2 原始坐标系<br/><small>lidar_link · XYZI / Float32</small></span></div>
      </aside>
    </main>
    {dragging && <div className="drop-overlay"><Upload size={40}/><h2>释放文件，开始探索点云</h2><p>文件仅上传至本机服务</p></div>}
  </div>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);

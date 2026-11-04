import { useCallback, useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { api, clock } from './api';
import { parseCloudPoints } from './lib/cloudBinary';
import { PageNav } from './PageNav';

type BoxLabel = { center: number[]; size: number[]; yaw: number };
type Stroke = { points: [number, number][]; brush: number };
// [index, device_start_time, device_time, packet_count, point_count, complete]
type FrameRow = [number, number | null, number | null, number, number, boolean];
type WindowContext = {
  capture_sha256: string;
  observation_unit: 'accumulated_window';
  frame_range: number[];
  coordinate_frame: string;
  cloud_id: string;
  device_time_range: [number | null, number | null];
  duration_seconds: number | null;
};
type TimelineData = {
  capture_id: string; frame_count: number; duration: number;
  fields: string[]; frames: FrameRow[];
};
type PostStats = { points: number; xy: number[]; base_z: number; top_z: number; axis_support: number };
type AnalysisResult = {
  status: 'ok' | 'insufficient_points' | 'degenerate_span' | 'ok_multiple';
  region_points?: number; min_region_points?: number; axis_span_m?: number;
  left_post?: PostStats; right_post?: PostStats;
  thickness_m?: number; base_z?: number; top_z?: number;
  box?: BoxLabel; checks?: Record<string, boolean>; warnings?: string[];
  proposals?: AnalysisResult[]; cluster_count?: number;
};
type PendingGroup = { id: string; strokes: Stroke[]; analysis: AnalysisResult | null };
type AnnotationLabel = {
  type: 'goal' | 'non_goal_structure';
  frame_range: number[];
  note?: string;
  box?: BoxLabel;
  analysis?: AnalysisResult;
  label_id?: string;
};
type CaptureEntry = { id: string; filename: string; frame_count: number };
type CloudInfo = {
  capture: string; start: number; end: number; point_count: number;
  device_start_time: number | null; device_time: number | null;
  window_context: WindowContext | null;
};
type AnnotateCloudMeta = {
  start: number; end: number; filtering: string; point_count: number;
  device_start_time: number | null; device_time: number | null;
  window_context?: WindowContext | null;
};

const WINDOW_CHOICES = [20, 30, 50, 100, 200];
const BRUSH_CHOICES = [0.5, 1.0, 1.5, 2.0];
const SPEED_CHOICES = [0.5, 1, 2, 4];
const COMPONENT_NAMES = ['left_edge', 'right_edge', 'upper_edge', 'local_ground', 'net_or_brace'];

const clamp = (value: number, lo: number, hi: number) => Math.min(Math.max(value, lo), hi);

/** Capsule ribbon (discs swept along the polyline) drawn on the XY plane. */
function strokeRibbonGeometry(points: [number, number][], radius: number): THREE.BufferGeometry {
  const verts: number[] = [];
  const push = (x: number, y: number, z = 0.05) => verts.push(x, y, z);
  const disc = (cx: number, cy: number, segments: number, z: number) => {
    for (let i = 0; i < segments; i++) {
      const a0 = (i / segments) * Math.PI * 2;
      const a1 = ((i + 1) / segments) * Math.PI * 2;
      push(cx, cy, z);
      push(cx + Math.cos(a0) * radius, cy + Math.sin(a0) * radius, z);
      push(cx + Math.cos(a1) * radius, cy + Math.sin(a1) * radius, z);
    }
  };
  if (points.length === 1) {
    disc(points[0][0], points[0][1], 16, 0.05);
  } else {
    const normals = points.map((_, i) => {
      const prev = points[Math.max(i - 1, 0)];
      const next = points[Math.min(i + 1, points.length - 1)];
      const dx = next[0] - prev[0];
      const dy = next[1] - prev[1];
      const len = Math.hypot(dx, dy) || 1;
      return [-dy / len, dx / len];
    });
    for (let i = 0; i < points.length - 1; i++) {
      const [x0, y0] = points[i];
      const [x1, y1] = points[i + 1];
      const [n0x, n0y] = normals[i];
      const [n1x, n1y] = normals[i + 1];
      push(x0 + n0x * radius, y0 + n0y * radius);
      push(x0 - n0x * radius, y0 - n0y * radius);
      push(x1 + n1x * radius, y1 + n1y * radius);
      push(x0 - n0x * radius, y0 - n0y * radius);
      push(x1 - n1x * radius, y1 - n1y * radius);
      push(x1 + n1x * radius, y1 + n1y * radius);
    }
    // 端头圆帽：z 略低避免与条带共面闪烁。
    disc(points[0][0], points[0][1], 12, 0.048);
    const last = points[points.length - 1];
    disc(last[0], last[1], 12, 0.048);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
  return geometry;
}

export default function AnnotateApp() {
  const [captures, setCaptures] = useState<CaptureEntry[]>([]);
  const [selected, setSelected] = useState('');
  const [timeline, setTimeline] = useState<TimelineData | null>(null);
  const [frame, setFrame] = useState(0);
  const [windowSize, setWindowSize] = useState(20);
  const [speed, setSpeed] = useState(1);
  const [playing, setPlaying] = useState(false);
  const [tool, setTool] = useState<'brush' | 'pick' | 'pan'>('brush');
  const [brush, setBrush] = useState(1.0);
  const [strokes, setStrokes] = useState<Stroke[]>([]);
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [pending, setPending] = useState<PendingGroup[]>([]);
  const [negativeCenter, setNegativeCenter] = useState<number[] | null>(null);
  const [labeler, setLabeler] = useState('');
  const [note, setNote] = useState('');
  const [labels, setLabels] = useState<AnnotationLabel[]>([]);
  const [cloudInfo, setCloudInfo] = useState<CloudInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const canvasRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<{
    scene: THREE.Scene; camera: THREE.OrthographicCamera; renderer: THREE.WebGLRenderer;
    controls: OrbitControls; points: THREE.Points | null; fitted: boolean;
    strokeGroup: THREE.Group; boxGroup: THREE.Group | null;
  } | null>(null);
  const requestSeqRef = useRef(0);
  const cloudRef = useRef<{ capture: string; start: number; end: number; window: number } | null>(null);
  const stateRef = useRef({ frame: 0, windowSize: 20, selected: '', tool: 'brush' as 'brush' | 'pick' | 'pan', brush: 1.0 });
  const strokesRef = useRef<Stroke[]>([]);
  const activeStrokeRef = useRef<[number, number][] | null>(null);
  const rebuildOverlayRef = useRef<() => void>(() => {});
  const commitStrokeRef = useRef<(stroke: Stroke) => void>(() => {});
  const onPickRef = useRef<(point: number[]) => void>(() => {});
  const frameTotal = timeline?.frame_count ?? 0;

  useEffect(() => { stateRef.current = { frame, windowSize, selected, tool, brush }; }, [frame, windowSize, selected, tool, brush]);
  useEffect(() => { strokesRef.current = strokes; rebuildOverlayRef.current(); }, [strokes]);

  const windowStart = Math.max(0, frame - windowSize + 1);
  const frameRow = timeline?.frames[clamp(frame, 0, timeline.frames.length - 1)] ?? null;
  const cloudSynced = !!cloudInfo && cloudInfo.capture === selected
    && cloudInfo.start === windowStart && cloudInfo.end === frame;

  // ------------------------------------------------ 2D 俯视场景 + 画笔交互
  useEffect(() => {
    const container = canvasRef.current;
    if (!container) return;
    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-50, 50, 50, -50, -500, 2000);
    // 标注视图锁定 XY 俯视：只允许平移/缩放，不允许旋转视角。
    camera.up.set(0, 1, 0);
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setSize(container.clientWidth || 800, container.clientHeight || 500);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.enableRotate = false;
    controls.screenSpacePanning = true;
    // 左键留给画笔/拾取，右键平移，滚轮缩放。
    (controls.mouseButtons as Record<string, unknown>).LEFT = null;
    (controls.touches as Record<string, unknown>).ONE = null;
    const grid = new THREE.GridHelper(240, 24, 0x334155, 0x1e293b);
    grid.rotation.x = Math.PI / 2;
    grid.position.z = -2;
    scene.add(grid);
    const strokeGroup = new THREE.Group();
    scene.add(strokeGroup);
    sceneRef.current = { scene, camera, renderer, controls, points: null, fitted: false, strokeGroup, boxGroup: null };

    const ribbonMaterial = new THREE.MeshBasicMaterial({
      color: 0x22d3ee, transparent: true, opacity: 0.3, depthWrite: false, side: THREE.DoubleSide });
    const lineMaterial = new THREE.LineBasicMaterial({ color: 0x67e8f9 });

    const raycaster = new THREE.Raycaster();
    const ndcOf = (event: PointerEvent | MouseEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      return new THREE.Vector2(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1);
    };
    const planePoint = (event: PointerEvent | MouseEvent, z: number) => {
      raycaster.setFromCamera(ndcOf(event), camera);
      return raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 0, 1), -z), new THREE.Vector3());
    };

    const rebuildOverlay = () => {
      for (const child of [...strokeGroup.children]) {
        strokeGroup.remove(child);
        if (child instanceof THREE.Mesh || child instanceof THREE.Line) child.geometry.dispose();
      }
      const all: Stroke[] = [...strokesRef.current];
      if (activeStrokeRef.current) all.push({ points: activeStrokeRef.current, brush: stateRef.current.brush });
      for (const stroke of all) {
        const mesh = new THREE.Mesh(strokeRibbonGeometry(stroke.points, stroke.brush), ribbonMaterial);
        strokeGroup.add(mesh);
        if (stroke.points.length >= 2) {
          const line = new THREE.BufferGeometry().setFromPoints(
            stroke.points.map(([x, y]) => new THREE.Vector3(x, y, 0.06)));
          strokeGroup.add(new THREE.Line(line, lineMaterial));
        }
      }
    };
    rebuildOverlayRef.current = rebuildOverlay;

    const onPointerDown = (event: PointerEvent) => {
      if (stateRef.current.tool === 'brush') {
        const hit = planePoint(event, 0);
        if (!hit) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        activeStrokeRef.current = [[hit.x, hit.y]];
        rebuildOverlay();
        return;
      }
      // 移动模式：左键平移完全交给 OrbitControls，这里不拦截。
      if (stateRef.current.tool !== 'pick') return;
      // 负样本点选：拾取射线附近最近点。
      if (!sceneRef.current?.points) return;
      raycaster.params.Points = { threshold: (camera.top - camera.bottom) * 0.015 };
      raycaster.setFromCamera(ndcOf(event), camera);
      const hits = raycaster.intersectObject(sceneRef.current.points, false);
      if (!hits.length || hits[0].index === undefined) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const position = (sceneRef.current.points.geometry as THREE.BufferGeometry)
        .getAttribute('position') as THREE.BufferAttribute;
      const index = hits[0].index;
      onPickRef.current([position.getX(index), position.getY(index), position.getZ(index)]
        .map((v) => Math.round(v * 100) / 100));
    };
    const onPointerMove = (event: PointerEvent) => {
      const active = activeStrokeRef.current;
      if (!active) return;
      const hit = planePoint(event, 0);
      if (!hit) return;
      const last = active[active.length - 1];
      if (Math.hypot(hit.x - last[0], hit.y - last[1]) < 0.12) return;
      active.push([hit.x, hit.y]);
      rebuildOverlay();
    };
    const onPointerUp = () => {
      const active = activeStrokeRef.current;
      if (!active) return;
      activeStrokeRef.current = null;
      commitStrokeRef.current({ points: active, brush: stateRef.current.brush });
    };

    renderer.domElement.addEventListener('pointerdown', onPointerDown, true);
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
    const onResize = () => {
      if (!container.clientWidth || !container.clientHeight) return;
      const aspect = container.clientWidth / Math.max(container.clientHeight, 1);
      renderer.setSize(container.clientWidth, container.clientHeight);
      camera.right = (camera.top - camera.bottom) * 0.5 * aspect;
      camera.left = -camera.right;
      camera.updateProjectionMatrix();
    };
    window.addEventListener('resize', onResize);
    let raf = 0;
    const animate = () => {
      raf = requestAnimationFrame(animate);
      try {
        if (camera.right === camera.left) onResize();
        controls.update();
        renderer.render(scene, camera);
      } catch (err) {
        console.error('[annotate-loop]', err);
      }
    };
    raf = requestAnimationFrame(animate);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
      renderer.domElement.removeEventListener('pointerdown', onPointerDown, true);
      controls.dispose();
      renderer.dispose();
      container.removeChild(renderer.domElement);
      sceneRef.current = null;
    };
  }, []);

  useEffect(() => {
    commitStrokeRef.current = (stroke) => setStrokes((prev) => [...prev, stroke]);
    onPickRef.current = (point) => setNegativeCenter(point);
  }, []);

  // 模式切换：移动模式下左键 = 平移；画笔/负样本下左键留给标注，平移走右键。
  useEffect(() => {
    const world = sceneRef.current;
    if (!world) return;
    (world.controls.mouseButtons as Record<string, unknown>).LEFT =
      tool === 'pan' ? THREE.MOUSE.PAN : null;
    world.renderer.domElement.style.cursor = tool === 'pan' ? 'grab' : 'crosshair';
  }, [tool]);

  // 测试/自动化钩子：绕过合成指针事件（OrbitControls setPointerCapture 会抛异常）。
  useEffect(() => {
    (window as unknown as Record<string, unknown>).__annotate = {
      addStroke: (pts: [number, number][]) =>
        commitStrokeRef.current({ points: pts, brush: stateRef.current.brush }),
      clear: () => { setStrokes([]); setAnalysis(null); },
      analyze: () => analyzeRef.current(),
      closeGroup: () => closeGroupRef.current(),
      save: () => saveRef.current(),
      state: () => ({
        frame: stateRef.current.frame, windowSize: stateRef.current.windowSize,
        strokes: strokesRef.current, cloud: cloudInfoRef.current,
        analysis: analysisRef.current, pending: pendingRef.current,
        tool: stateRef.current.tool,
      }),
    };
  });

  // 分析提案框 → 场景（绿色线框）
  useEffect(() => {
    const world = sceneRef.current;
    if (!world) return;
    if (world.boxGroup) {
      world.scene.remove(world.boxGroup);
      world.boxGroup.traverse((child) => {
        if (child instanceof THREE.Mesh || child instanceof THREE.LineSegments) {
          child.geometry.dispose();
          (child.material as THREE.Material).dispose();
        }
      });
      world.boxGroup = null;
    }
    if (analysis?.status !== 'ok' || !analysis.box) return;
    const box = analysis.box;
    const group = new THREE.Group();
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1),
      new THREE.MeshBasicMaterial({ color: 0x4ade80, transparent: true, opacity: 0.1,
        side: THREE.DoubleSide, depthWrite: false }));
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)),
      new THREE.LineBasicMaterial({ color: 0x4ade80 }));
    group.add(mesh, edges);
    group.scale.set(box.size[0], box.size[1], box.size[2]);
    group.position.set(box.center[0], box.center[1], box.center[2]);
    group.rotation.z = box.yaw - Math.PI / 2;
    world.scene.add(group);
    world.boxGroup = group;
  }, [analysis]);

  // ------------------------------------------------ 点云获取（原始全量叠加）
  const renderCloud = useCallback((points: Float32Array, info: CloudInfo) => {
    const world = sceneRef.current;
    if (!world) return;
    const previous = cloudRef.current;
    if (!previous || previous.capture !== info.capture
        || previous.start !== info.start || previous.end !== info.end) {
      // 换了窗口就清空画笔与待存组：笔画绑定它绘制时的那幅叠加云，禁止跨窗口复用。
      strokesRef.current = [];
      activeStrokeRef.current = null;
      setStrokes([]);
      setAnalysis(null);
      setNegativeCenter(null);
      setPending([]);
    }
    if (world.points) {
      const old = (world.points.geometry as THREE.BufferGeometry).getAttribute('position') as THREE.BufferAttribute;
      if (old.count !== info.point_count) {
        world.scene.remove(world.points);
        (world.points.geometry as THREE.BufferGeometry).dispose();
        (world.points.material as THREE.Material).dispose();
        world.points = null;
      }
    }
    if (!world.points) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(info.point_count * 3), 3));
      geometry.setAttribute('color', new THREE.BufferAttribute(new Float32Array(info.point_count * 3), 3));
      world.points = new THREE.Points(geometry, new THREE.PointsMaterial({
        size: 2.5, vertexColors: true, sizeAttenuation: false }));
      world.points.frustumCulled = false;
      world.scene.add(world.points);
    }
    const geometry = world.points.geometry as THREE.BufferGeometry;
    const position = geometry.getAttribute('position') as THREE.BufferAttribute;
    const color = geometry.getAttribute('color') as THREE.BufferAttribute;
    for (let i = 0; i < info.point_count; i++) {
      position.setXYZ(i, points[i * 4], points[i * 4 + 1], points[i * 4 + 2]);
      const t = Math.min(Math.max((points[i * 4 + 2] + 2) / 10, 0), 1);
      color.setXYZ(i, 0.2 + 0.8 * t, 0.55 - 0.25 * t, 0.9 - 0.6 * t);
    }
    position.needsUpdate = true;
    color.needsUpdate = true;
    geometry.setDrawRange(0, info.point_count);
    geometry.computeBoundingSphere();
    cloudRef.current = { capture: info.capture, start: info.start, end: info.end, window: stateRef.current.windowSize };
    setCloudInfo(info);
    if (!world.fitted && info.point_count > 0 && world.renderer.domElement.clientWidth) {
      world.fitted = true;
      geometry.computeBoundingBox();
      const bbox = geometry.boundingBox!;
      const center = bbox.getCenter(new THREE.Vector3());
      const bboxSize = bbox.getSize(new THREE.Vector3());
      const radius = Math.max(Math.max(bboxSize.x, bboxSize.y) / 2, 10);
      const aspect = world.renderer.domElement.clientWidth / Math.max(world.renderer.domElement.clientHeight, 1);
      world.camera.position.set(center.x, center.y, center.z + radius * 2.0);
      world.camera.up.set(0, 1, 0);
      world.controls.target.copy(center);
      const half = radius * 1.2;
      world.camera.top = half; world.camera.bottom = -half;
      world.camera.right = half * aspect; world.camera.left = -half * aspect;
      world.camera.near = -radius * 8; world.camera.far = radius * 12;
      world.camera.updateProjectionMatrix();
      world.controls.update();
    }
  }, []);

  const fetchCloud = useCallback(async (capture: string, end: number, windowN: number) => {
    const seq = ++requestSeqRef.current;
    const start = Math.max(0, end - windowN + 1);
    setLoading(true);
    try {
      const response = await fetch('/api/demo/annotate/cloud', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ capture_id: capture, start, end, voxel: 0 }) });
      if (!response.ok) throw new Error((await response.json()).detail || '叠加云读取失败');
      const buffer = await response.arrayBuffer();
      const { meta, points } = parseCloudPoints<AnnotateCloudMeta>(buffer, '叠加云', [1], 4);
      if (seq !== requestSeqRef.current) return; // 已有更新的请求，丢弃过期结果
      // 对齐门禁：服务端窗口与请求必须逐帧一致，且必须是原始未过滤点集。
      if (meta.start !== start || meta.end !== end) return;
      if (meta.filtering !== 'none') throw new Error('后端返回了过滤点集，已拒绝渲染');
      renderCloud(points, {
        capture, start, end, point_count: meta.point_count,
        device_start_time: meta.device_start_time, device_time: meta.device_time,
        window_context: meta.window_context ?? null });
      setError('');
    } catch (e) {
      if (seq === requestSeqRef.current) setError(String(e instanceof Error ? e.message : e));
    } finally {
      if (seq === requestSeqRef.current) setLoading(false);
    }
  }, [renderCloud]);

  // 轮询同步：时间轴位置/窗口变化 → 拉取对应叠加云（一次一个请求）。
  useEffect(() => {
    if (!selected) return;
    let inflight = false;
    const timer = setInterval(async () => {
      const state = stateRef.current;
      const current = cloudRef.current;
      if (inflight) return;
      if (current && current.capture === state.selected
          && current.end === state.frame
          && current.window === state.windowSize) return;
      inflight = true;
      try { await fetchCloud(state.selected, state.frame, state.windowSize); }
      finally { inflight = false; }
    }, 120);
    return () => clearInterval(timer);
  }, [selected, fetchCloud]);

  // 选采集：加载时间轴（帧号/设备时间/包数对齐数据）。
  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setTimeline(null);
    cloudRef.current = null;
    api<TimelineData>(`/demo/annotate/timeline?capture_id=${selected}`)
      .then((data) => {
        if (cancelled) return;
        setTimeline(data);
        setFrame((f) => clamp(f, 0, Math.max(data.frame_count - 1, 0)));
      })
      .catch((e) => setError(`时间轴读取失败: ${e}`));
    return () => { cancelled = true; };
  }, [selected]);

  // 播放：按设备帧率（10 Hz）× 速度推进帧号，到片尾自动停。
  useEffect(() => {
    if (!playing || frameTotal === 0) return;
    const timer = setInterval(() => {
      setFrame((f) => (f >= frameTotal - 1 ? f : f + 1));
    }, Math.max(40, Math.round(100 / speed)));
    return () => clearInterval(timer);
  }, [playing, speed, frameTotal]);
  useEffect(() => {
    if (playing && frameTotal > 0 && frame >= frameTotal - 1) setPlaying(false);
  }, [playing, frame, frameTotal]);

  // ------------------------------------------------ 脚本分析与保存
  const cloudInfoRef = useRef(cloudInfo);
  useEffect(() => { cloudInfoRef.current = cloudInfo; }, [cloudInfo]);
  const timelineRef = useRef<TimelineData | null>(null);
  useEffect(() => { timelineRef.current = timeline; }, [timeline]);
  const negativeCenterRef = useRef(negativeCenter);
  useEffect(() => { negativeCenterRef.current = negativeCenter; }, [negativeCenter]);
  const analysisRef = useRef(analysis);
  useEffect(() => { analysisRef.current = analysis; }, [analysis]);
  const pendingRef = useRef<PendingGroup[]>([]);
  useEffect(() => { pendingRef.current = pending; }, [pending]);

  const analyze = useCallback(async () => {
    const state = stateRef.current;
    const current = strokesRef.current;
    if (!state.selected || !current.length) { setError('先用画笔画出球门区域'); return; }
    const info = cloudInfoRef.current;
    if (!info || info.capture !== state.selected
        || info.end !== state.frame
        || info.start !== Math.max(0, state.frame - state.windowSize + 1)) {
      setError('叠加云与时间轴未同步，请等待加载完成'); return;
    }
    setError('');
    try {
      const result = await api<AnalysisResult>('/demo/annotate/analyze', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          capture_id: state.selected,
          start: Math.max(0, state.frame - state.windowSize + 1),
          end: state.frame,
          strokes: current.map((s) => s.points),
          brush_radius: state.brush,
        }) });
      if (result.status !== 'ok' && result.status !== 'ok_multiple') {
        setAnalysis(null);
        setError(result.status === 'insufficient_points'
          ? `画笔区域内仅 ${result.region_points ?? 0} 个点（至少 ${result.min_region_points ?? '?'}）——沿门柱和横梁多画几笔`
          : `${result.warnings?.[0] ?? '区域内未找到双立柱结构'}`);
        return;
      }
      // 分析成功：当前笔画入列。多区域时每个分离 blob 各自成组（各带提案框）。
      const stamp = Math.random().toString(36).slice(2, 10);
      const groups: PendingGroup[] = result.status === 'ok_multiple' && result.proposals?.length
        ? result.proposals.map((proposal, i) => ({
            id: `${stamp}-${i}`, strokes: strokesRef.current, analysis: proposal }))
        : [{ id: stamp, strokes: strokesRef.current, analysis: result }];
      setPending((prev) => [...prev, ...groups]);
      setStrokes([]);
      setAnalysis(result.status === 'ok_multiple' ? result.proposals![0] : result);
      setNotice(result.status === 'ok_multiple'
        ? (result.warnings?.[0] ?? '已拆分为多个提案，已分别入列') : '');
      setError('');
    } catch (e) { setError(String(e instanceof Error ? e.message : e)); }
  }, []);
  const analyzeRef = useRef(analyze);
  useEffect(() => { analyzeRef.current = analyze; }, [analyze]);

  // 不跑脚本分析，直接把当前笔画作为一组入列（保存纯画笔区域真值）。
  const closeGroup = useCallback(() => {
    if (!strokesRef.current.length) { setError('当前没有画好的笔画'); return; }
    setPending((prev) => [...prev, {
      id: Math.random().toString(36).slice(2, 10),
      strokes: strokesRef.current, analysis: null }]);
    setStrokes([]);
    setAnalysis(null);
    setError('');
  }, []);
  const closeGroupRef = useRef(closeGroup);
  useEffect(() => { closeGroupRef.current = closeGroup; }, [closeGroup]);

  // 对已入列的组补跑脚本分析。
  const analyzeGroup = useCallback(async (groupId: string) => {
    const state = stateRef.current;
    const info = cloudInfoRef.current;
    if (!info || info.capture !== state.selected
        || info.end !== state.frame
        || info.start !== Math.max(0, state.frame - state.windowSize + 1)) {
      setError('叠加云与时间轴未同步，请等待加载完成'); return;
    }
    const group = pendingRef.current.find((g) => g.id === groupId);
    if (!group) return;
    setError('');
    try {
      const result = await api<AnalysisResult>('/demo/annotate/analyze', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          capture_id: state.selected,
          start: Math.max(0, state.frame - state.windowSize + 1),
          end: state.frame,
          strokes: group.strokes.map((s) => s.points),
          brush_radius: state.brush,
        }) });
      setPending((prev) => prev.map((g) => (g.id === groupId ? { ...g, analysis: result } : g)));
      if (result.status !== 'ok') {
        setError(result.status === 'insufficient_points'
          ? `该组画笔区域内仅 ${result.region_points ?? 0} 个点——沿门柱和横梁多画几笔`
          : `${result.warnings?.[0] ?? '该组区域内未找到双立柱结构'}`);
      }
    } catch (e) { setError(String(e instanceof Error ? e.message : e)); }
  }, []);

  const loadLabels = useCallback(async (capture = selected) => {
    if (!capture) return;
    try {
      const docs = await api<Array<{ labels: AnnotationLabel[] }>>(`/demo/annotations?capture_id=${capture}`);
      setLabels(docs.flatMap((d) => d.labels));
    } catch { setLabels([]); }
  }, [selected]);
  const loadLabelsRef = useRef(loadLabels);
  useEffect(() => { loadLabelsRef.current = loadLabels; }, [loadLabels]);
  useEffect(() => { loadLabels(); }, [loadLabels]);

  // 保存全部待存组（无框组按纯画笔区域保存）+ 负样本点选。
  const saveAll = useCallback(async () => {
    const state = stateRef.current;
    if (!state.selected) return;
    const info = cloudInfoRef.current;
    const context = info?.window_context;
    if (!info || !context || info.capture !== state.selected
        || info.end !== state.frame
        || info.start !== Math.max(0, state.frame - state.windowSize + 1)
        || context.frame_range[1] !== state.frame
        || context.duration_seconds === null) {
      setError('请等待当前窗口点云加载完成后再保存'); return;
    }
    const groups = pendingRef.current;
    const negative = negativeCenterRef.current;
    if (!groups.length && !(state.tool === 'pick' && negative)) {
      setError('没有待保存的标注：先画笔圈选，回车分析入列或按 G 直接入列'); return;
    }
    setError('');
    const annotationContext = {
      frame_index: state.frame,
      device_time: info.device_time,
      accumulated_frames: state.frame - info.start + 1,
      window_size: state.windowSize,
      voxel_size: 0,
      range: [0, 2000],
      source: 'demo_accumulated_cloud',
    };
    const windowFields = {
      ...context,
      motion_status: 'unknown', visibility: 'uncertain', review_status: 'PENDING_REVIEW',
      estimated_error_m: null,
      components: Object.fromEntries(COMPONENT_NAMES
        .map((name) => [name, { visibility: 'uncertain', raw_refs: [] }])),
    };
    const failures: string[] = [];
    const savedIds: string[] = [];
    for (const group of groups) {
      const hasBox = group.analysis?.status === 'ok' && group.analysis.box;
      try {
        await api('/demo/annotations', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            capture_id: state.selected, note, labeler,
            type: 'goal',
            strokes: group.strokes.map((s) => ({ points: s.points, brush_radius: s.brush })),
            ...(hasBox ? { box: group.analysis!.box, analysis: group.analysis } : {}),
            annotation_context: annotationContext,
            ...windowFields,
          }) });
        savedIds.push(group.id);
      } catch (e) {
        failures.push(`组(${group.strokes.length}笔): ${e instanceof Error ? e.message : e}`);
      }
    }
    if (state.tool === 'pick' && negative) {
      try {
        await api('/demo/annotations', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            capture_id: state.selected, note, labeler,
            type: 'non_goal_structure',
            points: { center: negative },
            annotation_context: annotationContext,
            ...windowFields,
          }) });
      } catch (e) {
        failures.push(`负样本: ${e instanceof Error ? e.message : e}`);
      }
    }
    if (savedIds.length) {
      setPending((prev) => prev.filter((g) => !savedIds.includes(g.id)));
      setNote('');
    }
    await loadLabelsRef.current();
    if (failures.length) setError(`部分标注保存失败：${failures.join('；')}`);
  }, [note, labeler]);
  const saveRef = useRef(saveAll);
  useEffect(() => { saveRef.current = saveAll; }, [saveAll]);

  const removeLabel = useCallback(async (labelId?: string) => {
    if (!labelId || !stateRef.current.selected) return;
    try {
      await api(`/demo/annotations?capture_id=${stateRef.current.selected}&label_id=${labelId}`,
        { method: 'DELETE' });
      await loadLabelsRef.current();
    } catch (e) { setError(String(e instanceof Error ? e.message : e)); }
  }, []);

  useEffect(() => {
    api<CaptureEntry[]>('/demo/captures').then((list) => {
      setCaptures(list);
      if (list.length) setSelected((prev) => prev || list[0].id);
    }).catch(() => setError('无法获取采集列表'));
  }, []);

  // ------------------------------------------------ 键盘快捷键
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA'
          || target.tagName === 'SELECT' || target.isContentEditable)) return;
      if (event.key === ' ') {
        event.preventDefault();
        setPlaying((p) => !p);
      } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault();
        const step = event.shiftKey ? 10 : 1;
        setFrame((f) => clamp(event.key === 'ArrowLeft' ? f - step : f + step, 0, Math.max(frameTotal - 1, 0)));
      } else if (event.key === 'b' || event.key === 'B') {
        setTool('brush');
      } else if (event.key === 'm' || event.key === 'M') {
        setTool('pan');
      } else if (event.key === 'n' || event.key === 'N') {
        setTool('pick');
      } else if (event.key === 'Delete' || event.key === 'Backspace'
                 || ((event.ctrlKey || event.metaKey) && (event.key === 'z' || event.key === 'Z'))) {
        event.preventDefault();
        setStrokes((prev) => {
          if (prev.length) return prev.slice(0, -1);
          setAnalysis(null);
          return prev;
        });
      } else if (event.key === 'Escape') {
        setStrokes([]);
        setAnalysis(null);
        setNotice('');
      } else if (event.key === 'g' || event.key === 'G') {
        closeGroupRef.current();
      } else if (event.key === 'Enter') {
        analyzeRef.current();
      } else if ((event.ctrlKey || event.metaKey) && (event.key === 's' || event.key === 'S')) {
        event.preventDefault();
        saveRef.current();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [frameTotal]);

  const seek = useCallback((target: number) => {
    setFrame(clamp(target, 0, Math.max(frameTotal - 1, 0)));
  }, [frameTotal]);

  const timeBase = timeline?.frames[0]?.[1] ?? 0;
  const timeText = (row: FrameRow) => {
    const startText = row[1] != null ? clock(Math.max(row[1] - timeBase, 0)) : '—';
    const endText = row[2] != null ? clock(Math.max(row[2] - timeBase, 0)) : '—';
    return `${startText} → ${endText}`;
  };

  return (
    <div className="demo-root">
      <header className="demo-header">
        <div>
          <h1>NA70 球门标注工作台</h1>
          <div className="demo-sub">画笔圈选 → 脚本分析 → 保存 · 原始全量点叠加（{windowSize} 帧）· XY 俯视锁定</div>
        </div>
        <PageNav/>
        <div className="demo-controls">
          <select value={selected} onChange={(e) => { setSelected(e.target.value); setFrame(0); setPlaying(false); }}>
            {captures.map((c) => <option key={c.id} value={c.id}>{c.filename}</option>)}
          </select>
          <label>叠加帧数
            <select value={windowSize} onChange={(e) => setWindowSize(Number(e.target.value))}>
              {WINDOW_CHOICES.map((n) => <option key={n} value={n}>{n} 帧</option>)}
            </select>
          </label>
          <label>画笔半径
            <select value={brush} onChange={(e) => setBrush(Number(e.target.value))}>
              {BRUSH_CHOICES.map((r) => <option key={r} value={r}>{r} m</option>)}
            </select>
          </label>
          <label>速度
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
              {SPEED_CHOICES.map((s) => <option key={s} value={s}>{s}×</option>)}
            </select>
          </label>
          <button className={playing ? 'danger' : 'active'}
                  onClick={() => setPlaying((p) => !p)} disabled={!selected || frameTotal === 0}>
            {playing ? '暂停 (空格)' : '播放 (空格)'}
          </button>
        </div>
      </header>
      <div className="demo-body" ref={canvasRef}>
        {!selected && <div className="demo-hint">选择一个采集后自动加载叠加点云</div>}
        <div className="demo-panel demo-annotate">
          <h2>画笔标注</h2>
          <div className="anno-row">
            <button className={tool === 'brush' ? 'active' : ''} onClick={() => setTool('brush')}>画笔圈选 (B)</button>
            <button className={tool === 'pan' ? 'active' : ''} onClick={() => setTool('pan')}>移动视图 (M)</button>
            <button className={tool === 'pick' ? 'active' : ''} onClick={() => setTool('pick')}>负样本点选 (N)</button>
          </div>
          {tool === 'brush' ? (
            <>
              <div className="anno-picked">
                当前笔画 {strokes.length} 条{strokes.length ? ` · 最近一笔 ${strokes[strokes.length - 1].points.length} 点` : ''}
              </div>
              <div className="anno-row">
                <button className="active" onClick={() => analyzeRef.current()} disabled={!strokes.length}>
                  分析并入列 (回车)
                </button>
                <button onClick={() => closeGroupRef.current()} disabled={!strokes.length}>
                  直接入列 (G)
                </button>
                <button onClick={() => setStrokes((prev) => prev.slice(0, -1))}>撤销一笔 (Ctrl+Z)</button>
                <button onClick={() => { setStrokes([]); setAnalysis(null); setNotice(''); }}>清空 (Esc)</button>
              </div>
              {notice && <div className="anno-notice">{notice}</div>}
              <div className="anno-list">
                <h3>待保存组（{pending.length}）</h3>
                {pending.map((group, i) => (
                  <div key={group.id} className="anno-pending-row">
                    <span className="anno-goal">
                      组{i + 1} · {group.strokes.length} 笔
                      {group.analysis?.status === 'ok' && group.analysis.box
                        ? ` · 宽 ${group.analysis.box.size[1].toFixed(2)} m · 高 ${group.analysis.box.size[2].toFixed(2)} m`
                        : ' · 未分析（存为画笔区域）'}
                    </span>
                    <button onClick={() => analyzeGroup(group.id)}>分析</button>
                    <button onClick={() => setPending((prev) => prev.filter((g) => g.id !== group.id))}>删除</button>
                  </div>
                ))}
                {pending.length > 0 && (
                  <div className="anno-row">
                    <button className="active" onClick={() => saveRef.current()}>保存全部 (Ctrl+S)</button>
                  </div>
                )}
              </div>
              {analysis?.status === 'ok' && analysis.box && (
                <div className="anno-diagnostics">
                  <div className="anno-derived">最近分析</div>
                  <div>
                    宽 {analysis.box.size[1].toFixed(2)} m · 高 {analysis.box.size[2].toFixed(2)} m ·
                    厚 {analysis.box.size[0].toFixed(2)} m · 朝向 {(analysis.box.yaw * 180 / Math.PI).toFixed(1)}° ·
                    区域 {analysis.region_points} 点
                  </div>
                  <div>中心 [{analysis.box.center.map((v) => v.toFixed(2)).join(', ')}]</div>
                  <div>左柱 [{analysis.left_post?.xy.map((v) => v.toFixed(2)).join(', ')}]
                    底 {analysis.left_post?.base_z} / 顶 {analysis.left_post?.top_z}</div>
                  <div>右柱 [{analysis.right_post?.xy.map((v) => v.toFixed(2)).join(', ')}]
                    底 {analysis.right_post?.base_z} / 顶 {analysis.right_post?.top_z}</div>
                  {Object.entries(analysis.checks ?? {}).map(([name, okValue]) => (
                    <div key={name} style={{ color: okValue ? '#4ade80' : '#fbbf24' }}>
                      {okValue ? '✓' : '⚠'} {name === 'width_in_expected_range' ? '宽度在常规区间' : '高度在常规区间'}
                    </div>
                  ))}
                  {(analysis.warnings ?? []).map((w, i) => <div key={i} className="warn">⚠ {w}</div>)}
                </div>
              )}
            </>
          ) : (
            <div className="anno-picked">
              {negativeCenter
                ? `负样本中心: [${negativeCenter.map((v) => v.toFixed(2)).join(', ')}]`
                : '点击负样本结构（围栏/看台等）的中心点'}
            </div>
          )}
          <div className="anno-row">
            <input placeholder="标注人" value={labeler} onChange={(e) => setLabeler(e.target.value)} />
            <input placeholder="备注" value={note} onChange={(e) => setNote(e.target.value)} />
            {tool === 'pick' && (
              <button className="active" onClick={() => saveRef.current()}>保存标注 (Ctrl+S)</button>
            )}
          </div>
          <div className="anno-list">
            <h3>已保存（{labels.length}）</h3>
            {labels.map((label, i) => (
              <div key={label.label_id ?? i}
                   className={label.type === 'goal' ? 'anno-goal' : 'anno-negative'}>
                <span>
                  {label.type === 'goal' ? '球门' : '负样本'} · 帧 {label.frame_range[0]}–{label.frame_range[1]}
                  {label.box ? ` · 宽 ${label.box.size[1].toFixed(2)} m` : ''}
                  {label.note ? ` · ${label.note}` : ''}
                </span>
                <button onClick={() => seek(label.frame_range[1])}>定位</button>
                <button disabled={!label.label_id} title={label.label_id ? '' : '旧格式标注无 id，不可在此删除'}
                        onClick={() => removeLabel(label.label_id)}>删除</button>
              </div>
            ))}
          </div>
          <div className="anno-shortcuts">
            快捷键：空格 播放/暂停 · ←/→ ±1 帧（Shift ±10）· B 画笔 · M 移动视图 · N 负样本 · G 当组入列 ·
            Ctrl+Z/删除 撤销一笔 · Esc 清空 · 回车 分析并入列 · Ctrl+S 保存全部 ·
            移动模式下左键拖动平移；画笔/负样本模式下右键拖动平移 · 滚轮缩放
          </div>
          <div className="anno-tip">
            同一画面可画多组：画完一组按回车（分析并入列）或 G（直接入列，不跑分析），再画下一组；
            全部就绪后 Ctrl+S 一次性保存。未分析的组按画笔区域真值保存（无框）。
            已保存标注可定位或删除。
          </div>
        </div>
        {error && <div className="demo-error">{error}</div>}
      </div>
      <div className="anno-trackbar">
        <div className="anno-align">
          {frameRow ? (
            <>
              <span>帧 <b>{frame}</b>/{Math.max(frameTotal - 1, 0)}</span>
              <span>设备时间 {timeText(frameRow)}</span>
              <span>{frameRow[3]} 包{frameRow[5] ? '' : ' · 不完整'}</span>
              <span>窗口 {windowStart}–{frame}（{frame - windowStart + 1}/{windowSize} 帧）</span>
              <span>{cloudSynced && cloudInfo ? `${cloudInfo.point_count.toLocaleString()} 点（原始全量）`
                : loading ? '叠加云加载中…' : '等待同步'}</span>
            </>
          ) : <span>时间轴加载中…</span>}
        </div>
        <TimelineTrack
          frames={timeline?.frames ?? null}
          frame={frame}
          windowStart={windowStart}
          windowEnd={frame}
          labels={labels}
          onSeek={seek} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 视频轨道

function TimelineTrack({ frames, frame, windowStart, windowEnd, labels, onSeek }: {
  frames: FrameRow[] | null;
  frame: number;
  windowStart: number;
  windowEnd: number;
  labels: AnnotationLabel[];
  onSeek: (frame: number) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const hoverRef = useRef<number | null>(null);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const parent = canvas.parentElement;
    const width = parent ? parent.clientWidth : 0;
    const height = 84;
    if (!width) return;
    const dpr = Math.min(window.devicePixelRatio, 2);
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = '#0f172a';
    ctx.fillRect(0, 0, width, height);
    if (!frames || frames.length === 0) {
      ctx.fillStyle = '#475569';
      ctx.font = '12px sans-serif';
      ctx.fillText('时间轴不可用', 12, 40);
      return;
    }
    const pad = 8;
    const count = frames.length;
    const xOf = (index: number) => pad + (index / Math.max(count - 1, 1)) * (width - 2 * pad);

    // 时间标尺：优先真实设备时间；缺失时退化为纯帧刻度。
    const firstT = frames[0][1];
    const lastT = frames[count - 1][2] ?? frames[count - 1][1];
    const rulerTop = 4;
    ctx.textBaseline = 'top';
    if (firstT != null && lastT != null && lastT > firstT) {
      const duration = lastT - firstT;
      const step = [0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600].find((s) => duration / s <= 10) ?? 600;
      ctx.font = '10px monospace';
      ctx.fillStyle = '#64748b';
      for (let t = Math.ceil(firstT / step) * step; t <= lastT; t += step) {
        // 设备时间 → 帧：按 device_start_time 单调查找。
        let lo = 0, hi = count - 1;
        while (lo < hi) {
          const mid = (lo + hi + 1) >> 1;
          const value = frames[mid][1];
          if (value != null && value <= t) lo = mid; else hi = mid - 1;
        }
        const x = xOf(lo);
        ctx.strokeStyle = '#334155';
        ctx.beginPath();
        ctx.moveTo(x, rulerTop);
        ctx.lineTo(x, height - 20);
        ctx.stroke();
        ctx.fillText(clock(Math.max(t - firstT, 0)), x + 2, rulerTop);
      }
    }
    // 帧刻度：密度自适应。
    const pxPerFrame = (width - 2 * pad) / Math.max(count - 1, 1);
    const frameStep = Math.max(1, Math.round(2 / Math.max(pxPerFrame, 0.02)));
    ctx.strokeStyle = 'rgba(71, 85, 105, 0.55)';
    ctx.beginPath();
    for (let i = 0; i < count; i += frameStep) {
      const x = xOf(i);
      ctx.moveTo(x, height - 30);
      ctx.lineTo(x, height - 22);
    }
    ctx.stroke();
    ctx.fillStyle = '#475569';
    ctx.font = '9px monospace';
    const numberStep = Math.max(frameStep, Math.round(46 / Math.max(pxPerFrame, 0.02)));
    for (let i = 0; i < count; i += numberStep) {
      ctx.fillText(String(i), xOf(i) + 2, height - 18);
    }
    // 已保存标注的帧区间带。
    const bandY = height - 34;
    for (const label of labels) {
      const [lo, hi] = label.frame_range;
      ctx.fillStyle = label.type === 'goal' ? 'rgba(74, 222, 128, 0.45)' : 'rgba(248, 113, 113, 0.4)';
      ctx.fillRect(xOf(lo), bandY, Math.max(xOf(hi) - xOf(lo), 2), 8);
    }
    // 当前叠加窗口带。
    const wx0 = xOf(windowStart);
    const wx1 = xOf(windowEnd);
    ctx.fillStyle = 'rgba(37, 99, 235, 0.28)';
    ctx.fillRect(wx0, 16, Math.max(wx1 - wx0, 2), height - 52);
    ctx.strokeStyle = '#3b82f6';
    ctx.strokeRect(wx0, 16, Math.max(wx1 - wx0, 2), height - 52);
    // 播放头。
    const px = xOf(frame);
    ctx.strokeStyle = '#f8fafc';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(px, rulerTop);
    ctx.lineTo(px, height - 12);
    ctx.stroke();
    ctx.fillStyle = '#f8fafc';
    ctx.beginPath();
    ctx.moveTo(px - 4, rulerTop);
    ctx.lineTo(px + 4, rulerTop);
    ctx.lineTo(px, rulerTop + 6);
    ctx.fill();
    ctx.lineWidth = 1;
    // 悬停提示。
    const hover = hoverRef.current;
    if (hover != null) {
      const row = frames[hover];
      const hx = xOf(hover);
      ctx.strokeStyle = 'rgba(248, 250, 252, 0.35)';
      ctx.beginPath();
      ctx.moveTo(hx, rulerTop);
      ctx.lineTo(hx, height - 12);
      ctx.stroke();
      const time = row[1] != null ? clock(Math.max(row[1] - (firstT ?? 0), 0)) : '时间缺失';
      const text = `帧 ${row[0]} · ${time} · ${row[3]} 包${row[5] ? '' : ' · 不完整'} · ${row[4].toLocaleString()} 点`;
      ctx.font = '11px monospace';
      const textWidth = ctx.measureText(text).width;
      const boxX = clamp(hx + 6, pad, Math.max(width - textWidth - 12, pad));
      ctx.fillStyle = 'rgba(11, 17, 32, 0.92)';
      ctx.fillRect(boxX - 3, height - 16, textWidth + 6, 14);
      ctx.fillStyle = '#e2e8f0';
      ctx.fillText(text, boxX, height - 14);
    }
  }, [frames, frame, windowStart, windowEnd, labels]);

  useEffect(() => { draw(); });
  useEffect(() => {
    const onResize = () => draw();
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [draw]);

  const indexFromEvent = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!frames?.length) return null;
    const rect = event.currentTarget.getBoundingClientRect();
    const pad = 8;
    const ratio = (event.clientX - rect.left - pad) / Math.max(rect.width - 2 * pad, 1);
    return clamp(Math.round(ratio * (frames.length - 1)), 0, frames.length - 1);
  };

  return (
    <canvas
      ref={canvasRef}
      style={{ width: '100%', height: 84, display: 'block', cursor: 'crosshair' }}
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        const index = indexFromEvent(event);
        if (index != null) onSeek(index);
      }}
      onPointerMove={(event) => {
        const index = indexFromEvent(event);
        hoverRef.current = index;
        if (index != null && (event.buttons & 1)) onSeek(index);
        draw();
      }}
      onPointerLeave={() => { hoverRef.current = null; draw(); }}
    />
  );
}

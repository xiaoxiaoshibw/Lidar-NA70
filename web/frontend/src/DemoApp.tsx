import { useCallback, useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { api, type Capture } from './api';
import { parseCloudPoints } from './lib/cloudBinary';
import { PageNav } from './PageNav';

type DemoGoal = {
  id: number;
  state: string;
  detected: boolean;
  distance: number;
  confidence: number;
  center: number[];
  size: number[];
  yaw: number;
  age: number;
};

type DemoSnapshot = {
  status: 'starting' | 'running' | 'done' | 'error';
  error: string | null;
  frame_index: number;
  device_time: number | null;
  accumulated_frames: number;
  window_size: number;
  accumulated_points: number;
  window_raw_points: number;
  fps: number;
  processing_ms: number;
  imu: boolean;
  lidar_online: boolean;
  goal: DemoGoal | null;
  strategy?: string | null;
  focus?: { active: boolean; center: number[] | null; half_size: number } | null;
  paused?: boolean;
};

type Cloud = { meta: CloudMeta; points: Float32Array };
type CloudMeta = {
  version: number;
  fields?: string[];
  index: number;
  point_count: number;
  mode: 'single' | 'accumulated';
  accumulated_frames: number;
  source?: 'single' | 'accumulated';
  stage?: string;
};

type DebugInfo = {
  frame_index: number;
  single_candidates: CandidateInfo[];
  accumulated_candidates: CandidateInfo[];
  single_rejected_candidates?: CandidateInfo[];
  accumulated_rejected_candidates?: CandidateInfo[];
  bev_regions?: { center: number[]; score: number }[];
  association?: AssociationInfo | null;
};

type StageInfo = {
  stage: string; label: string; input_points?: number; output_points?: number;
  rejected_points?: number; removed_points?: number; latency_ms: number | null;
  input_entities?: number; output_entities?: number; entity?: string;
  candidate_count?: number;
  reject_reason?: string | null; parameters?: Record<string, unknown>;
};
type PipelineInfo = { frame_index: number; source: string; stages: StageInfo[] };
type AssociationInfo = {
  accepted: boolean; candidate_id: string | null; association_score: number;
  detection_score?: number; center_distance_m?: number; gate_m?: number;
  bbox_similarity?: number; geometry_similarity?: number; reject_reason?: string | null;
};
type LogItem = { timestamp: number; frame_id: number; module: string; level: string;
  message: string; data?: Record<string, unknown> };

type FocusInfo = { active: boolean; center: number[] | null; half_size: number };

type BoxLabel = { center: number[]; size: number[]; yaw: number };
const GOAL_DEFAULT_SIZE: [number, number, number] = [0.6, 7.32, 2.44];

type CandidateInfo = {
  candidate_id: string;
  center: number[];
  size: number[];
  yaw: number;
  confidence: number;
  crossbar: number;
  detection_score?: number;
  score_breakdown?: Record<string, number>;
  supporting_point_indices?: number[];
  point_count?: number;
  distance?: number;
  bearing?: number;
  width?: number;
  height?: number;
  reject_reason?: string | null;
  accepted?: boolean;
};

async function readDemoCloud(sessionId: string, mode: 'single' | 'accumulated',
                             signal: AbortSignal, stage?: string,
                             candidateId?: string): Promise<Cloud | null> {
  const query = stage
    ? `/debug/cloud?source=${mode}&stage=${encodeURIComponent(stage)}${candidateId ? `&candidate_id=${encodeURIComponent(candidateId)}` : ''}`
    : `/cloud?mode=${mode}`;
  const response = await fetch(`/api/demo/sessions/${sessionId}${query}`, { signal });
  if (!response.ok) return null;
  const buffer = await response.arrayBuffer();
  try {
    return parseCloudPoints<CloudMeta>(buffer, '演示点云', [1, 2, 3]);
  } catch {
    return null; // 截断或版本/长度不符的点云一律拒绝渲染
  }
}

function stateColor(state: string): string {
  if (state === 'TRACKING' || state === 'LOCKED') return '#4ade80';
  if (state === 'LOCKING' || state === 'CANDIDATE') return '#fbbf24';
  if (state === 'LOST') return '#f87171';
  return '#94a3b8';
}

function pos_set(attr: THREE.BufferAttribute, x: number, y: number, z: number, i: number) {
  attr.setXYZ(i, x, y, z);
}

const VIEW_LABELS: Record<string, string> = {
  SEARCHING: '搜索目标', CANDIDATE: '候选确认', LOCKING: '锁定中',
  LOCKED: '已锁定', TRACKING: '跟踪中', LOST: '丢失', REACQUIRE: '重捕获',
};

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="demo-stat">
      <span className="demo-stat-label">{label}</span>
      <span className="demo-stat-value" style={color ? { color } : undefined}>{value}</span>
    </div>
  );
}

type World = {
  scene: THREE.Scene;
  camera: THREE.OrthographicCamera;
  renderer: THREE.WebGLRenderer;
  controls: OrbitControls;
  points: THREE.Points | null;
  bbox: THREE.LineSegments | null;
  poles: THREE.Points;
  poleLines: THREE.LineSegments;
  trajectory: THREE.Line;
  roi: THREE.Box3Helper;
  focusRect: THREE.LineLoop;
  bevMarkers: THREE.Points;
  candidateBoxes: THREE.Group;
  manualBoxGroup: THREE.Group | null;
  manualBoxMesh: THREE.Mesh | null;
  manualHandle: THREE.Mesh | null;
  labelAnchor: THREE.Vector3;
  fitted: boolean;
};

function fitCamera(camera: THREE.OrthographicCamera, controls: OrbitControls,
                   box: THREE.Box3, aspect: number) {
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 10);
  camera.position.set(center.x + radius * 0.9, center.y - radius * 0.9, center.z + radius * 0.7);
  controls.target.copy(center);
  const halfHeight = radius * 1.2;
  camera.top = halfHeight; camera.bottom = -halfHeight;
  camera.right = halfHeight * aspect; camera.left = -halfHeight * aspect;
  camera.near = -radius * 8; camera.far = radius * 12;
  camera.updateProjectionMatrix();
  controls.update();
}

export default function DemoApp() {
  const [captures, setCaptures] = useState<Capture[]>([]);
  const [selected, setSelected] = useState<string>('');
  const [sessionId, setSessionId] = useState<string>('');
  const [snapshot, setSnapshot] = useState<DemoSnapshot | null>(null);
  const [mode, setMode] = useState<'single' | 'accumulated'>('accumulated');
  const [windowSize, setWindowSize] = useState(20);
  const [speed, setSpeed] = useState(1);
  const [showRoi, setShowRoi] = useState(false);
  const [showPoles, setShowPoles] = useState(false);
  const [showTrajectory, setShowTrajectory] = useState(false);
  const [debug, setDebug] = useState(false);
  const [error, setError] = useState('');
  const [follow, setFollow] = useState(true);
  const [annotateMode, setAnnotateMode] = useState(false);
  const [paused, setPaused] = useState(false);
  const [manualBox, setManualBox] = useState<BoxLabel | null>(null);
  const [saveNote, setSaveNote] = useState('');
  const [selectedStage, setSelectedStage] = useState('raw');
  const [pipelineInfo, setPipelineInfo] = useState<PipelineInfo | null>(null);
  const [debugInfo, setDebugInfo] = useState<DebugInfo | null>(null);
  const [selectedCandidateId, setSelectedCandidateId] = useState('');
  const [logs, setLogs] = useState<LogItem[]>([]);
  const [logModule, setLogModule] = useState('all');
  const [focusHalf, setFocusHalf] = useState(6);
  const [minConfidence, setMinConfidence] = useState(.5);
  const [maxJump, setMaxJump] = useState(6.5);
  // 会话与采集绑定：创建会话那一刻的 capture_id，保存标注只允许写回它。
  const [sessionCapture, setSessionCapture] = useState('');
  // 最近一次实际渲染的点云帧号（来自点云元数据 index），用于帧身份门禁与展示。
  const [cloudFrame, setCloudFrame] = useState<number | null>(null);

  const canvasRef = useRef<HTMLDivElement>(null);
  const worldRef = useRef<World | null>(null);
  const modeRef = useRef(mode);
  const layersRef = useRef({ showRoi, showPoles, showTrajectory });
  const goalRef = useRef<DemoGoal | null>(null);
  const sessionIdRef = useRef('');
  const snapshotRef = useRef<DemoSnapshot | null>(null);
  const cloudFrameRef = useRef<number | null>(null);
  // 人工框创建那一刻的点云帧号：标注归属以此为准，而非随后推进的快照帧。
  const manualBoxFrameRef = useRef<number | null>(null);
  const trailRef = useRef<number[][]>([]);
  const focusRef = useRef<FocusInfo | null>(null);
  const annotateModeRef = useRef(false);
  const followRef = useRef(true);
  const manualBoxRef = useRef<BoxLabel | null>(null);
  const selectedStageRef = useRef(selectedStage);
  const selectedCandidateRef = useRef(selectedCandidateId);
  const dragRef = useRef<{ kind: 'move' | 'rotate'; planeZ: number;
    offset: [number, number]; startAngle: number; startYaw: number } | null>(null);

  useEffect(() => { modeRef.current = mode; }, [mode]);
  useEffect(() => { annotateModeRef.current = annotateMode; }, [annotateMode]);
  useEffect(() => { followRef.current = follow; }, [follow]);
  useEffect(() => { manualBoxRef.current = manualBox; }, [manualBox]);
  useEffect(() => { selectedStageRef.current = selectedStage; }, [selectedStage]);
  useEffect(() => { selectedCandidateRef.current = selectedCandidateId; }, [selectedCandidateId]);
  useEffect(() => { layersRef.current = { showRoi, showPoles, showTrajectory }; }, [showRoi, showPoles, showTrajectory]);

  // 一次性创建 Three.js 场景：正交相机 Z 向上，网格表达尺度。
  useEffect(() => {
    const container = canvasRef.current;
    if (!container) return;
    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-50, 50, 50, -50, -500, 1000);
    camera.up.set(0, 0, 1);
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    const grid = new THREE.GridHelper(240, 24, 0x334155, 0x1e293b);
    grid.rotation.x = Math.PI / 2;
    grid.position.z = -2;
    scene.add(grid);
    const poles = new THREE.Points(
      new THREE.BufferGeometry(),
      new THREE.PointsMaterial({ color: 0xff5533, size: 6, sizeAttenuation: false }));
    poles.visible = false;
    scene.add(poles);
    const poleLines = new THREE.LineSegments(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ color: 0xff8866 }));
    poleLines.visible = false;
    scene.add(poleLines);
    const trajectory = new THREE.Line(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ color: 0xffd24a }));
    trajectory.visible = false;
    scene.add(trajectory);
    const roi = new THREE.Box3Helper(new THREE.Box3(
      new THREE.Vector3(50, -130, -3), new THREE.Vector3(130, 130, 8)), 0x33ddff);
    roi.visible = false;
    scene.add(roi);
    const focusGeom = new THREE.BufferGeometry();
    focusGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(12), 3));
    const focusRect = new THREE.LineLoop(focusGeom, new THREE.LineBasicMaterial({ color: 0x33ddff }));
    focusRect.visible = false;
    scene.add(focusRect);
    const bevMarkers = new THREE.Points(
      new THREE.BufferGeometry(),
      new THREE.PointsMaterial({ color: 0xff4444, size: 8, sizeAttenuation: false }));
    bevMarkers.visible = false;
    scene.add(bevMarkers);
    const candidateBoxes = new THREE.Group();
    scene.add(candidateBoxes);
    worldRef.current = { scene, camera, renderer, controls, points: null, bbox: null,
      poles, poleLines, trajectory, roi, focusRect, bevMarkers, candidateBoxes,
      manualBoxGroup: null, manualBoxMesh: null, manualHandle: null,
      labelAnchor: new THREE.Vector3(), fitted: false };

    // ---- 实时标注交互（标注模式开启时接管指针）：点云上单击生成标准框，
    // ---- 拖框平移，拖黄把手旋转；与跟踪绿框同屏对比。
    const raycaster = new THREE.Raycaster();
    const ndcOf = (event: MouseEvent | PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      if (!rect.width || !rect.height) return null;
      return new THREE.Vector2(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1);
    };
    const planePoint = (event: MouseEvent | PointerEvent, z: number) => {
      const ndc = ndcOf(event);
      if (!ndc) return null;
      raycaster.setFromCamera(ndc, camera);
      const plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), -z);
      return raycaster.ray.intersectPlane(plane, new THREE.Vector3());
    };
    const onPointerDown = (event: PointerEvent) => {
      const world = worldRef.current;
      if (!world || !annotateModeRef.current) return;
      const ndc = ndcOf(event);
      if (!ndc) return;
      raycaster.params.Points = { threshold: (camera.top - camera.bottom) * 0.015 };
      raycaster.setFromCamera(ndc, camera);
      if (world.manualBoxMesh && manualBoxRef.current) {
        const handleHit = world.manualHandle
          ? raycaster.intersectObject(world.manualHandle, false) : [];
        const boxHit = raycaster.intersectObject(world.manualBoxMesh, false);
        const center = manualBoxRef.current.center;
        if (handleHit.length) {
          dragRef.current = { kind: 'rotate', planeZ: handleHit[0].point.z,
            offset: [0, 0],
            startAngle: Math.atan2(handleHit[0].point.y - center[1],
                                   handleHit[0].point.x - center[0]),
            startYaw: manualBoxRef.current.yaw };
          world.controls.enabled = false;
          return;
        }
        if (boxHit.length) {
          const pt = boxHit[0].point;
          dragRef.current = { kind: 'move', planeZ: pt.z,
            offset: [center[0] - pt.x, center[1] - pt.y], startAngle: 0, startYaw: 0 };
          world.controls.enabled = false;
          return;
        }
      }
      if (!world.points) return;
      const hits = raycaster.intersectObject(world.points, false);
      if (!hits.length || hits[0].index === undefined) return;
      const position = (world.points.geometry as THREE.BufferGeometry)
        .getAttribute('position') as THREE.BufferAttribute;
      const index = hits[0].index;
      const px = position.getX(index), py = position.getY(index), pz = position.getZ(index);
      manualBoxFrameRef.current = cloudFrameRef.current;
      setManualBox({
        center: [px, py, pz + GOAL_DEFAULT_SIZE[2] / 2],
        size: [...GOAL_DEFAULT_SIZE],
        yaw: Math.atan2(py, px) + Math.PI / 2,
      });
    };
    const onPointerMove = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag || !manualBoxRef.current) return;
      const hit = planePoint(event, drag.planeZ);
      if (!hit) return;
      if (drag.kind === 'move') {
        setManualBox((prev) => prev && {
          ...prev, center: [hit.x + drag.offset[0], hit.y + drag.offset[1], prev.center[2]],
        });
      } else {
        const center = manualBoxRef.current.center;
        const angle = Math.atan2(hit.y - center[1], hit.x - center[0]);
        setManualBox((prev) => prev && { ...prev, yaw: drag.startYaw + (angle - drag.startAngle) });
      }
    };
    const onPointerUp = () => {
      if (dragRef.current && worldRef.current) worldRef.current.controls.enabled = true;
      dragRef.current = null;
    };
    renderer.domElement.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
    // 测试/调试钩子：合成 pointer 事件会触发 OrbitControls setPointerCapture
    // 异常（真实指针无此问题），自动化验证经此直接生成/清除标注框。
    (window as unknown as Record<string, unknown>).__demoAnnotate = {
      create: (x: number, y: number, z: number) => {
        manualBoxFrameRef.current = cloudFrameRef.current;
        setManualBox({
          center: [x, y, z + GOAL_DEFAULT_SIZE[2] / 2],
          size: [...GOAL_DEFAULT_SIZE],
          yaw: Math.atan2(y, x) + Math.PI / 2,
        });
      },
      clear: () => setManualBox(null),
      current: () => manualBoxRef.current,
    };
    const onResize = () => {
      // 容器无尺寸（面板隐藏/布局中）时跳过：0 宽度会让投影矩阵退化成 NaN。
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
      // 先排程再执行：任何一帧异常都不会终止渲染循环。
      raf = requestAnimationFrame(animate);
      try {
        if (camera.right === camera.left) onResize();
        controls.update();
        renderer.render(scene, camera);
      } catch (error) {
        console.error('[viewer-loop]', error);
      }
    };
    raf = requestAnimationFrame(animate);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
      renderer.domElement.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
      controls.dispose();
      renderer.dispose();
      container.removeChild(renderer.domElement);
      worldRef.current = null;
    };
  }, []);

  // 点云更新：按高度着色，自动聚焦一次；bbox/立柱/轨迹图层同步刷新。
  const updatePoints = useCallback((cloud: Cloud) => {
    const world = worldRef.current;
    if (!world) return;
    const { points } = cloud;
    const stride = cloud.meta.fields?.length ?? (cloud.meta.version === 2 ? 5 : 4);
    const count = points.length / stride;
    // 点数变化时必须重建缓冲：BufferAttribute 容量固定，超容量写入会被静默丢弃。
    if (world.points) {
      const previous = (world.points.geometry as THREE.BufferGeometry)
        .getAttribute('position') as THREE.BufferAttribute | null;
      if (!previous || previous.count !== count) {
        world.scene.remove(world.points);
        (world.points.geometry as THREE.BufferGeometry).dispose();
        (world.points.material as THREE.Material).dispose();
        world.points = null;
      }
    }
    if (!world.points) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(count * 3), 3));
      geometry.setAttribute('color', new THREE.BufferAttribute(new Float32Array(count * 3), 3));
      // 正交相机下 sizeAttenuation 会让点变成亚像素；固定像素尺寸保证可见。
      world.points = new THREE.Points(geometry, new THREE.PointsMaterial({
        size: 2.5, vertexColors: true, sizeAttenuation: false }));
      world.points.frustumCulled = false;
      world.scene.add(world.points);
    }
    const geometry = world.points.geometry as THREE.BufferGeometry;
    const position = geometry.getAttribute('position') as THREE.BufferAttribute;
    const color = geometry.getAttribute('color') as THREE.BufferAttribute;
    const zLo = -2, zHi = 8;
    for (let i = 0; i < count; i++) {
      position.setXYZ(i, points[i * stride], points[i * stride + 1], points[i * stride + 2]);
      const classification = stride >= 5 ? points[i * stride + 4] : 1;
      const highlighted = stride >= 6 && points[i * stride + 5] > 0;
      if (highlighted || classification === 2) {
        color.setXYZ(i, 1.0, 0.62, 0.18);
      } else if (classification === 0) {
        // Rejected points remain present but are deliberately subdued.
        color.setXYZ(i, 0.16, 0.19, 0.23);
      } else if (classification === 3) {
        // Complete current raw frame used as spatial context; it is not a
        // claim that these samples were rejected by the accumulated branch.
        color.setXYZ(i, 0.24, 0.31, 0.39);
      } else {
        const t = Math.min(Math.max((points[i * stride + 2] - zLo) / (zHi - zLo), 0), 1);
        color.setXYZ(i, 0.15 + 0.35 * t, 0.75 + 0.2 * t, 0.95 - 0.2 * t);
      }
    }
    position.needsUpdate = true;
    color.needsUpdate = true;
    geometry.setDrawRange(0, count);
    geometry.computeBoundingSphere();
    updateOverlays();
    if (!world.fitted && count > 0) {
      world.fitted = true;
      geometry.computeBoundingBox();
      const element = world.renderer.domElement;
      fitCamera(world.camera, world.controls, geometry.boundingBox!,
        element.clientWidth / Math.max(element.clientHeight, 1));
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function updateOverlays() {
    const world = worldRef.current;
    if (!world) return;
    const goal = goalRef.current;
    const layers = layersRef.current;
    world.roi.visible = layers.showRoi;
    world.poles.visible = layers.showPoles;
    world.poleLines.visible = layers.showPoles;
    world.trajectory.visible = layers.showTrajectory && trailRef.current.length > 1;
    if (world.trajectory.visible) {
      const trail = trailRef.current;
      const positions = new Float32Array(trail.length * 3);
      trail.forEach((center, i) => positions.set([center[0], center[1], center[2]], i * 3));
      world.trajectory.geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    }
    const focus = focusRef.current;
    if (world.focusRect && focus && focus.active && focus.center) {
      world.focusRect.visible = true;
      {
        const [cx, cy] = focus.center;
        const h = focus.half_size;
        const attr = (world.focusRect.geometry as THREE.BufferGeometry).getAttribute('position') as THREE.BufferAttribute;
        pos_set(attr, cx - h, cy - h, 0.1, 0);
        pos_set(attr, cx + h, cy - h, 0.1, 1);
        pos_set(attr, cx + h, cy + h, 0.1, 2);
        pos_set(attr, cx - h, cy + h, 0.1, 3);
        attr.needsUpdate = true;
      }
    } else if (world.focusRect) {
      world.focusRect.visible = false;
    }
    if (world.bbox) {
      world.scene.remove(world.bbox);
      world.bbox.geometry.dispose();
      world.bbox = null;
    }
    if (goal && goal.detected) {
      const [dx, dy, dz] = goal.size;
      const box = new THREE.BoxGeometry(Math.max(dx, 0.4), Math.max(dy, 0.4), Math.max(dz, 0.4));
      world.bbox = new THREE.LineSegments(new THREE.EdgesGeometry(box),
        new THREE.LineBasicMaterial({ color: 0x4ade80 }));
      box.dispose();
      world.bbox.position.set(goal.center[0], goal.center[1], goal.center[2]);
      world.bbox.rotation.z = goal.yaw - Math.PI / 2;
      world.scene.add(world.bbox);
      world.labelAnchor.set(goal.center[0], goal.center[1], goal.center[2] + (goal.size[2] || 2) / 2 + 0.6);
    }
    // 跟随镜头：锁定后把视野中心缓移到球门（xy），并把视野半高向 18m 收敛。
    // 全景下 7m 的球门框只有十几个像素，不推近根本看不见。
    if (followRef.current && goal && goal.detected) {
      const target = world.controls.target;
      const dx = goal.center[0] - target.x, dy = goal.center[1] - target.y;
      const dist = Math.hypot(dx, dy);
      if (dist > 0.05) {
        const step = dist > 20 ? 1.0 : 0.25;
        const move = new THREE.Vector3(dx * step, dy * step, 0);
        world.camera.position.add(move);
        target.add(move);
      }
      const client = world.renderer.domElement;
      if (client.clientWidth && client.clientHeight) {
        const cam = world.camera;
        const half = (cam.top - cam.bottom) / 2;
        const next = half + (18 - half) * 0.08;
        if (Math.abs(next - half) > 0.1) {
          const aspect = client.clientWidth / client.clientHeight;
          cam.top = next; cam.bottom = -next;
          cam.right = next * aspect; cam.left = -next * aspect;
          cam.updateProjectionMatrix();
        }
      }
    }
  }

  // 人工标注框 → 场景（橙）：尺寸变化重建几何，位置/朝向只更新变换。
  useEffect(() => {
    const world = worldRef.current;
    if (!world) return;
    if (!manualBox) {
      manualBoxFrameRef.current = null;
      if (world.manualBoxGroup) {
        world.scene.remove(world.manualBoxGroup);
        world.manualBoxGroup.traverse((child) => {
          if (child instanceof THREE.Mesh || child instanceof THREE.LineSegments) {
            child.geometry.dispose();
            (child.material as THREE.Material).dispose();
          }
        });
        world.manualBoxGroup = null; world.manualBoxMesh = null; world.manualHandle = null;
      }
      return;
    }
    const [dx, dy, dz] = manualBox.size;
    let group = world.manualBoxGroup;
    if (!group) {
      group = new THREE.Group();
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1),
        new THREE.MeshBasicMaterial({ color: 0xff9f43, transparent: true, opacity: 0.12,
          side: THREE.DoubleSide, depthWrite: false }));
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)),
        new THREE.LineBasicMaterial({ color: 0xff9f43 }));
      const handle = new THREE.Mesh(new THREE.SphereGeometry(0.45, 16, 12),
        new THREE.MeshBasicMaterial({ color: 0xfbbf24 }));
      const arm = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(
        [new THREE.Vector3(), new THREE.Vector3()]),
        new THREE.LineBasicMaterial({ color: 0xfbbf24 }));
      group.add(mesh, edges, arm, handle);
      world.scene.add(group);
      world.manualBoxGroup = group; world.manualBoxMesh = mesh; world.manualHandle = handle;
    }
    const mesh = world.manualBoxMesh!;
    mesh.scale.set(dx, dy, dz);
    const edges = group.children[1] as THREE.LineSegments;
    edges.scale.set(dx, dy, dz);
    const yawDir = new THREE.Vector3(Math.cos(manualBox.yaw), Math.sin(manualBox.yaw), 0);
    const handle = world.manualHandle!;
    handle.position.copy(yawDir.clone().multiplyScalar(dy / 2 + 1.4));
    const arm = group.children[2] as THREE.LineSegments;
    const armPos = arm.geometry.getAttribute('position') as THREE.BufferAttribute;
    armPos.setXYZ(0, yawDir.x * dy / 2, yawDir.y * dy / 2, 0);
    armPos.setXYZ(1, yawDir.x * (dy / 2 + 1.4), yawDir.y * (dy / 2 + 1.4), 0);
    armPos.needsUpdate = true;
    group.position.set(manualBox.center[0], manualBox.center[1], manualBox.center[2]);
    group.rotation.z = manualBox.yaw - Math.PI / 2;
  }, [manualBox]);

  // 快照轮询：120ms 一次。单飞门禁：上一轮（快照+点云）未完成前不发新请求，
  // 避免响应乱序把旧云渲染到新帧上；会话已切换/中止的过期结果一律丢弃。
  useEffect(() => {
    if (!sessionId) return;
    const controller = new AbortController();
    let lastFrame = -1;
    let inFlight = false;
    const timer = setInterval(async () => {
      if (inFlight || controller.signal.aborted) return;
      inFlight = true;
      try {
        const state = await api<DemoSnapshot>(`/demo/sessions/${sessionId}`,
          { signal: controller.signal });
        if (controller.signal.aborted || sessionIdRef.current !== sessionId) return;
        setSnapshot(state);
        snapshotRef.current = state;
        focusRef.current = state.focus ?? null;
        updateOverlays();
        if (state.goal && state.goal.detected) {
          goalRef.current = state.goal;
          trailRef.current.push([...state.goal.center]);
          if (trailRef.current.length > 300) trailRef.current.shift();
        }
        if (state.status === 'done' || state.status === 'error') {
          // 回放结束自动复位，便于直接重新开始；已结束会话由后端在下次创建时清理。
          setSessionId('');
          setSessionCapture('');
          setManualBox(null);
          setPaused(false);
          clearInterval(timer);
          return;
        }
        if (state.frame_index !== lastFrame) {
          const cloud = await readDemoCloud(
            sessionId, modeRef.current, controller.signal,
            selectedStageRef.current, selectedCandidateRef.current || undefined);
          if (controller.signal.aborted || sessionIdRef.current !== sessionId) return;
          if (cloud) {
            // 去重键用点云元数据里的实际帧号：响应可能落后于快照，
            // 若按快照帧去重会把落后一帧的云当成新帧、之后不再刷新。
            const rendered = Number.isFinite(cloud.meta.index) ? cloud.meta.index : state.frame_index;
            lastFrame = rendered;
            cloudFrameRef.current = rendered;
            setCloudFrame(rendered);
            updatePoints(cloud);
          }
        }
      } catch { /* 轮询失败或已中止，下一轮重试 */ }
      finally { inFlight = false; }
    }, 120);
    return () => { clearInterval(timer); controller.abort(); };
  }, [sessionId, updatePoints, mode, selectedStage, selectedCandidateId]);

  // 可观察性数据：Pipeline/Candidate/Association/日志使用稳定 JSON 接口；
  // debug 开关只控制几何辅助层，不影响 Inspector 的数据更新。
  useEffect(() => {
    if (!sessionId) return;
    const controller = new AbortController();
    let inFlight = false;
    const timer = setInterval(async () => {
      if (inFlight || controller.signal.aborted) return;
      inFlight = true;
      try {
        const [info, pipe, logFeed] = await Promise.all([
          api<DebugInfo>(`/demo/sessions/${sessionId}/debug`, { signal: controller.signal }),
          api<PipelineInfo>(`/demo/sessions/${sessionId}/pipeline?source=${modeRef.current}`,
            { signal: controller.signal }),
          api<{ items: LogItem[] }>(`/demo/sessions/${sessionId}/logs?limit=80`,
            { signal: controller.signal }),
        ]);
        if (controller.signal.aborted || sessionIdRef.current !== sessionId) return;
        setDebugInfo(info);
        setPipelineInfo(pipe);
        setLogs(logFeed.items);
        const world = worldRef.current;
        if (!world) return;
        const acceptedCandidates = modeRef.current === 'single'
          ? info.single_candidates : info.accumulated_candidates;
        const rejectedCandidates = modeRef.current === 'single'
          ? (info.single_rejected_candidates ?? [])
          : (info.accumulated_rejected_candidates ?? []);
        const candidates = [...acceptedCandidates, ...rejectedCandidates];
        const polePositions: number[] = [];
        const linePositions: number[] = [];
        for (const candidate of candidates.slice(0, 6)) {
          const [dx, dy, dz] = candidate.size;
          const cos = Math.cos(candidate.yaw), sin = Math.sin(candidate.yaw);
          const half = Math.min(dy, 4) / 2;
          const offX = -sin * half, offY = cos * half;
          const c = candidate.center;
          polePositions.push(c[0] - offX, c[1] - offY, c[2] - dz / 2, c[0] - offX, c[1] - offY, c[2] + dz / 2);
          polePositions.push(c[0] + offX, c[1] + offY, c[2] - dz / 2, c[0] + offX, c[1] + offY, c[2] + dz / 2);
          linePositions.push(c[0] - offX, c[1] - offY, c[2] + dz / 2, c[0] + offX, c[1] + offY, c[2] + dz / 2);
        }
        world.poles.visible = debug && layersRef.current.showPoles;
        world.poleLines.visible = debug && layersRef.current.showPoles;
        world.poles.geometry.setAttribute('position',
          new THREE.BufferAttribute(new Float32Array(polePositions), 3));
        world.poleLines.geometry.setAttribute('position',
          new THREE.BufferAttribute(new Float32Array(linePositions), 3));
        const bev = info.bev_regions ?? [];
        world.bevMarkers.visible = bev.length > 0;
        if (bev.length) {
          const positions = new Float32Array(bev.length * 3);
          bev.forEach((region, i) => positions.set([region.center[0], region.center[1], 0.6], i * 3));
          world.bevMarkers.geometry.setAttribute('position',
            new THREE.BufferAttribute(positions, 3));
        }
        // Every candidate gets its own inspectable oriented box.  The selected
        // candidate is yellow; the rest remain orange and semi-transparent.
        for (const child of [...world.candidateBoxes.children]) {
          world.candidateBoxes.remove(child);
          const line = child as THREE.LineSegments;
          line.geometry.dispose();
          (line.material as THREE.Material).dispose();
        }
        if (debug) {
          candidates.forEach((candidate) => {
            const [dx, dy, dz] = candidate.size;
            const box = new THREE.BoxGeometry(Math.max(dx, .2), Math.max(dy, .2), Math.max(dz, .2));
            const selectedBox = candidate.candidate_id === selectedCandidateRef.current;
            const line = new THREE.LineSegments(new THREE.EdgesGeometry(box),
              new THREE.LineBasicMaterial({ color: selectedBox ? 0xfacc15
                : (candidate.accepted === false ? 0xef4444 : 0xfb923c),
                transparent: true, opacity: selectedBox ? 1 : .55 }));
            box.dispose();
            line.position.set(candidate.center[0], candidate.center[1], candidate.center[2]);
            line.rotation.z = candidate.yaw - Math.PI / 2;
            world.candidateBoxes.add(line);
          });
        }
      } catch { /* 忽略本轮 */ }
      finally { inFlight = false; }
    }, 500);
    return () => { clearInterval(timer); controller.abort(); };
  }, [debug, sessionId, mode]);

  // window/speed 变化时重启会话使设置生效。
  const startSession = useCallback(async (capture: string, window: number, speedValue: number) => {
    if (!capture) return;
    setError('');
    try {
      const created = await api<{ session_id: string }>('/demo/sessions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          capture_id: capture,
          speed: speedValue,
          config: { accumulation: { window_size: window } },
        }),
      });
      setSessionId(created.session_id);
      setSessionCapture(capture);
    } catch (e) { setError(String(e)); }
  }, []);

  const restartWith = (window: number, speedValue: number) => {
    setWindowSize(window);
    setSpeed(speedValue);
    // 优先用会话绑定的采集：即使 selected 被改动也不得跨采集重建会话。
    const capture = sessionCapture || selected;
    if (sessionId) {
      api(`/demo/sessions/${sessionId}`, { method: 'DELETE' }).catch(() => {});
      setSessionId('');
      startSession(capture, window, speedValue);
    } else {
      startSession(capture, window, speedValue);
    }
  };

  const stopSession = async () => {
    if (!sessionId) return;
    try { await api(`/demo/sessions/${sessionId}`, { method: 'DELETE' }); } catch { /* 已停止 */ }
    setSessionId('');
    setSessionCapture('');
    setSnapshot(null);
    setCloudFrame(null);
    cloudFrameRef.current = null;
    goalRef.current = null;
    focusRef.current = null;
    trailRef.current = [];
    setManualBox(null);
    setPaused(false);
    setSaveNote('');
  };

  useEffect(() => {
    // Demo 专用采集列表：只含 YAML 过滤后的目标场景数据。
    api<Array<{ id: string; filename: string; frame_count: number }>>('/demo/captures').then((list) => {
      setCaptures(list as Capture[]);
      if (list.length && !selected) {
        const preferred = list.find((item) => item.filename.includes('caochangyou')) ?? list[0];
        setSelected(preferred.id);
      }
    }).catch(() => setError('无法获取采集列表'));
    return () => {
      if (sessionIdRef.current) {
        fetch(`/api/demo/sessions/${sessionIdRef.current}`, { method: 'DELETE' }).catch(() => {});
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { sessionIdRef.current = sessionId; }, [sessionId]);

  const togglePause = async () => {
    if (!sessionId) return;
    try {
      await api(`/demo/sessions/${sessionId}/control`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: paused ? 'resume' : 'pause' }),
      });
      setPaused(!paused);
    } catch (e) { setError(String(e)); }
  };

  const saveManualBox = async () => {
    if (!manualBox || !sessionId) return;
    // 标注只允许写回会话绑定的采集；帧号取绘制该框时的点云帧，
    // 而非已继续推进的快照帧——两者不一致时不得混用。
    if (!sessionCapture) { setSaveNote('会话未绑定采集，无法保存'); return; }
    const frame = manualBoxFrameRef.current;
    if (frame === null || frame < 0) {
      setSaveNote('请等待当前点云加载完成后再保存');
      return;
    }
    try {
      const saved = await api<{ labels_total: number }>('/demo/annotations', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          capture_id: sessionCapture, type: 'goal',
          frame_range: [frame, frame], box: manualBox, labeler: 'demo-live',
        }),
      });
      setSaveNote(snapshot && snapshot.frame_index !== frame
        ? `已保存到点云帧 ${frame}（快照已前进到 ${snapshot.frame_index}，累计 ${saved.labels_total} 条标注）`
        : `已保存到帧 ${frame}（累计 ${saved.labels_total} 条标注）`);
    } catch (e) { setSaveNote(''); setError(String(e)); }
  };

  const switchMode = (next: 'single' | 'accumulated') => {
    setMode(next);
    // 两种模式的点云范围差异巨大，切换后重新取景。
    if (worldRef.current) worldRef.current.fitted = false;
  };

  const applyDebugParameters = async () => {
    if (!sessionId) return;
    try {
      await api(`/demo/sessions/${sessionId}/config`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          focus: { half_size: focusHalf },
          goal_detection: { scoring: { min_confidence: minConfidence } },
          tracking: { max_center_jump: maxJump },
        }),
      });
      setSaveNote('参数已应用，管线从当前帧重新开始');
    } catch (e) { setError(String(e)); }
  };

  const downloadSnapshot = async () => {
    if (!sessionId) return;
    const response = await fetch(`/api/demo/sessions/${sessionId}/snapshot`);
    if (!response.ok) return;
    const blob = new Blob([JSON.stringify(await response.json(), null, 2)],
      { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = `goal-debug-frame-${snapshot?.frame_index ?? 0}.json`;
    anchor.click(); URL.revokeObjectURL(url);
  };


  const goal = snapshot?.goal && snapshot.goal.detected ? snapshot.goal : null;
  // 帧身份：点云/快照/Inspector 是否来自同一帧；不一致时必须在界面上明示。
  const pipelineFrame = pipelineInfo && pipelineInfo.frame_index >= 0 ? pipelineInfo.frame_index : null;
  const frameMismatch = snapshot != null
    && ((cloudFrame !== null && cloudFrame !== snapshot.frame_index)
      || (pipelineFrame !== null && pipelineFrame !== snapshot.frame_index));
  const currentCandidates = mode === 'single'
    ? [...(debugInfo?.single_candidates ?? []), ...(debugInfo?.single_rejected_candidates ?? [])]
    : [...(debugInfo?.accumulated_candidates ?? []), ...(debugInfo?.accumulated_rejected_candidates ?? [])];
  const selectedCandidate = currentCandidates.find((item) => item.candidate_id === selectedCandidateId);
  const visibleLogs = logModule === 'all' ? logs : logs.filter((item) => item.module === logModule);

  return (
    <div className="demo-root">
      <header className="demo-header">
        <div>
          <h1>NA70 Football Goal Tracking Demo</h1>
          <div className="demo-sub">LiDAR-only · 无 IMU · 单帧 + 滑动窗口叠加</div>
        </div>
        <PageNav/>
        <div className="demo-controls">
          <label>叠加帧数
            <select value={windowSize} onChange={(e) => restartWith(Number(e.target.value), speed)}>
              {[1, 5, 10, 20, 30, 60, 100].map((n) => <option key={n} value={n}>{n} 帧</option>)}
            </select>
          </label>
          <label>回放速度
            <select value={speed} onChange={(e) => restartWith(windowSize, Number(e.target.value))}>
              {[0.5, 1, 2, 5].map((s) => <option key={s} value={s}>{s}×</option>)}
            </select>
          </label>
          <select value={selected} onChange={(e) => setSelected(e.target.value)}
                  disabled={!!sessionId}
                  title={sessionId ? '演示会话进行中，停止后才能切换采集' : undefined}>
            {captures.map((c) => <option key={c.id} value={c.id}>{c.filename}</option>)}
          </select>
          {!sessionId
            ? <button onClick={() => startSession(selected, windowSize, speed)} disabled={!selected}>开始演示</button>
            : <button className="danger" onClick={stopSession}>停止</button>}
          {sessionId && (
            <button className={paused ? 'active' : ''} onClick={togglePause}>
              {paused ? '继续' : '暂停'}
            </button>
          )}
          <button className={mode === 'single' ? 'active' : ''} onClick={() => switchMode('single')}>单帧</button>
          <button className={mode === 'accumulated' ? 'active' : ''} onClick={() => switchMode('accumulated')}>{windowSize}帧叠加</button>
          <label><input type="checkbox" checked={showRoi} onChange={(e) => setShowRoi(e.target.checked)} /> ROI</label>
          <label><input type="checkbox" checked={showPoles} onChange={(e) => setShowPoles(e.target.checked)} /> 立柱</label>
          <label><input type="checkbox" checked={showTrajectory} onChange={(e) => setShowTrajectory(e.target.checked)} /> 轨迹</label>
          <label><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> 跟随</label>
          <label><input type="checkbox" checked={debug} onChange={(e) => setDebug(e.target.checked)} /> debug</label>
          <label><input type="checkbox" checked={annotateMode} onChange={(e) => { setAnnotateMode(e.target.checked); if (!e.target.checked) setSaveNote(''); }} /> 标注</label>
        </div>
      </header>
      <div className="demo-body" ref={canvasRef}>
        <div className="demo-label" id="goal-label">
          {goal ? `GOAL #${goal.id} · ${VIEW_LABELS[goal.state] || goal.state}` : ''}
        </div>
        {!sessionId && (
          <div className="demo-hint">选择采集文件后点击「开始演示」——回放以约 10 Hz 逐帧运行叠加、检测与跟踪；50 m 内近场杂波不参与叠加</div>
        )}
        <div className="demo-panel demo-status">
          <h2>System Status</h2>
          <Stat label="LiDAR" value={snapshot?.lidar_online ? 'ONLINE' : 'OFFLINE'} />
          <Stat label="IMU" value="DISABLED" />
          <Stat label="Accumulation" value={`${snapshot?.accumulated_frames ?? 0} / ${snapshot?.window_size ?? windowSize} 帧`} />
          <Stat label="叠加点数" value={String(snapshot?.accumulated_points ?? 0)} />
          <Stat label="FPS" value={String(snapshot?.fps ?? 0)} />
          <Stat label="处理耗时" value={`${snapshot?.processing_ms ?? 0} ms`} />
          <Stat label="状态" value={VIEW_LABELS[snapshot?.goal?.state ?? 'SEARCHING'] || '搜索目标'}
                color={stateColor(snapshot?.goal?.state ?? '')} />
          <Stat label="策略" value={snapshot?.strategy ?? 'poles'} />
          <Stat label="FOV" value={snapshot?.focus?.active && snapshot.focus.center
            ? `${snapshot.focus.half_size} m @ (${snapshot.focus.center[0].toFixed(0)}, ${snapshot.focus.center[1].toFixed(0)})`
            : '全图'} />
        </div>
        <div className="demo-panel demo-goal">
          <h2>Goal Tracking</h2>
          {goal ? (
            <>
              <Stat label="ID" value={`#${goal.id}`} />
              <Stat label="Distance" value={`${goal.distance.toFixed(1)} m`} />
              <Stat label="Confidence" value={`${(goal.confidence * 100).toFixed(0)} %`} />
              <Stat label="State" value={VIEW_LABELS[goal.state] || goal.state} color={stateColor(goal.state)} />
              <Stat label="Age" value={`${goal.age} 帧`} />
              <Stat label="尺寸" value={goal.size.map((v) => v.toFixed(2)).join(' × ')} />
            </>
          ) : <div className="demo-empty">尚未锁定球门</div>}
        </div>
        <div className="demo-panel demo-inspector" data-testid="pipeline-inspector">
          <div className="demo-inspector-head">
            <h2>Pipeline / Algorithm Inspector</h2>
            <button onClick={downloadSnapshot} disabled={!sessionId}>导出 Snapshot</button>
          </div>
          {sessionId && (
            <div className="demo-hint" data-testid="frame-identity"
                 style={frameMismatch ? { color: '#f87171' } : undefined}>
              {`帧身份 · 点云 F${cloudFrame ?? '—'} · 快照 F${snapshot?.frame_index ?? '—'} · Inspector F${pipelineFrame ?? '—'}`}
              {frameMismatch ? ' · 数据来自不同帧，不可视为同一帧' : ''}
            </div>
          )}
          <div className="demo-legend">
            <span><i className="legend-context"/>原始上下文</span>
            <span><i className="legend-active"/>算法可见</span>
            <span><i className="legend-support"/>候选支撑</span>
            <span><i className="legend-rejected"/>已拒绝（仍显示）</span>
          </div>
          <div className="pipeline-list">
            {(pipelineInfo?.stages ?? []).map((stage, stageIndex) => (
              <button key={`${stage.stage}-${stageIndex}`}
                className={selectedStage === stage.stage ? 'selected' : ''}
                onClick={() => setSelectedStage(stage.stage)}>
                <strong>{stage.label}</strong>
                <span>{(stage.input_points ?? stage.input_entities ?? 0).toLocaleString()} → {(stage.output_points ?? stage.output_entities ?? 0).toLocaleString()}</span>
                <small>−{(stage.rejected_points ?? stage.removed_points ?? 0).toLocaleString()} · {stage.latency_ms == null ? '未执行' : `${stage.latency_ms.toFixed(2)} ms`}</small>
                {stage.reject_reason && <em>{stage.reject_reason}</em>}
              </button>
            ))}
          </div>
          <details open>
            <summary>候选与框来源（{currentCandidates.length}）</summary>
            <div className="candidate-list">
              {currentCandidates.map((candidate) => (
                <button key={candidate.candidate_id}
                  className={selectedCandidateId === candidate.candidate_id ? 'selected' : ''}
                  onClick={() => setSelectedCandidateId(candidate.candidate_id)}>
                  <strong>{candidate.candidate_id}</strong>
                  <span>检测 {(100 * candidate.confidence).toFixed(0)}% · {candidate.point_count ?? 0} 点</span>
                  <small className={candidate.accepted === false ? 'candidate-rejected' : ''}>
                    {candidate.reject_reason ?? 'ACCEPTED'} · {candidate.distance?.toFixed(1) ?? '-'} m</small>
                </button>
              ))}
              {!currentCandidates.length && <div className="demo-empty">当前 Stage 没有候选</div>}
            </div>
            {selectedCandidate && (
              <pre className="candidate-json">{JSON.stringify({
                id: selectedCandidate.candidate_id,
                bbox: { center: selectedCandidate.center, size: selectedCandidate.size,
                  yaw: selectedCandidate.yaw },
                score: selectedCandidate.score_breakdown,
                support_points: selectedCandidate.point_count,
                reject_reason: selectedCandidate.reject_reason,
              }, null, 2)}</pre>
            )}
          </details>
          <details>
            <summary>Association / Tracking</summary>
            <pre className="candidate-json">{JSON.stringify(debugInfo?.association ?? {
              accepted: false, reject_reason: 'no_data',
            }, null, 2)}</pre>
          </details>
          <details>
            <summary>实时参数（应用后从当前帧重置）</summary>
            <div className="debug-params">
              <label>FOV 半宽 m<input type="number" step="0.5" value={focusHalf}
                onChange={(e) => setFocusHalf(Number(e.target.value))}/></label>
              <label>检测阈值<input type="number" step="0.05" min="0" max="1" value={minConfidence}
                onChange={(e) => setMinConfidence(Number(e.target.value))}/></label>
              <label>关联门限 m<input type="number" step="0.5" value={maxJump}
                onChange={(e) => setMaxJump(Number(e.target.value))}/></label>
              <button onClick={applyDebugParameters} disabled={!sessionId}>应用参数</button>
            </div>
          </details>
          <details>
            <summary>模块日志</summary>
            <select value={logModule} onChange={(e) => setLogModule(e.target.value)}>
              <option value="all">全部模块</option><option value="pipeline">pipeline</option>
              <option value="tracker">tracker</option><option value="config">config</option>
            </select>
            <div className="debug-logs">
              {visibleLogs.slice(-20).reverse().map((item, index) => (
                <div key={`${item.frame_id}-${index}`}><b>{item.module}</b>
                  <span>F{item.frame_id}</span> {item.message}</div>
              ))}
            </div>
          </details>
        </div>
        {(annotateMode || manualBox) && (
          <div className="demo-panel demo-annotate">
            <h2>实时标注对比</h2>
            {!manualBox && <div className="demo-empty">在点云上单击生成标准球门框（自动朝向雷达），拖框平移、拖黄把手旋转</div>}
            {manualBox && (
              <>
                <Stat label="人工框中心" value={manualBox.center.map((v) => v.toFixed(1)).join(', ')} />
                <Stat label="人工框宽×高" value={`${manualBox.size[1].toFixed(2)} × ${manualBox.size[2].toFixed(2)}`} />
                {goal && (
                  <>
                    <Stat label="中心偏差 Δ" color={
                      Math.hypot(manualBox.center[0] - goal.center[0], manualBox.center[1] - goal.center[1]) > 2
                        ? '#f87171' : '#4ade80'}
                      value={`${Math.hypot(manualBox.center[0] - goal.center[0], manualBox.center[1] - goal.center[1]).toFixed(2)} m`} />
                    <Stat label="宽度差" value={`${Math.abs(manualBox.size[1] - goal.size[1]).toFixed(2)} m`} />
                    <Stat label="高度差" value={`${Math.abs(manualBox.size[2] - goal.size[2]).toFixed(2)} m`} />
                  </>
                )}
                <div className="demo-annotate-actions">
                  <button onClick={saveManualBox} disabled={!sessionId}>保存标注</button>
                  <button className="danger" onClick={() => { setManualBox(null); setSaveNote(''); }}>清除</button>
                </div>
                {saveNote && <div className="demo-hint">{saveNote}</div>}
              </>
            )}
            <div className="demo-hint">绿框=系统跟踪 · 橙框=人工标注；保存写入 /api/demo/annotations（评分器可对账）</div>
          </div>
        )}
        {error && <div className="demo-error">{error}</div>}
      </div>
    </div>
  );
}

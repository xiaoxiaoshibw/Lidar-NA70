import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import type { Frame } from './api';

export type Settings = { color: string; size: number; opacity: number; min: number; max: number; accumulation: number; grid: boolean; gridSize: number; axes: boolean; budget: number; navigation: string; showMotion: boolean };
export type ViewerTool = 'navigate' | 'range';
export type RangeMeasurement = {
  point: [number, number, number];
  distance: number;
  horizontal: number;
  vertical: number;
};
export type ViewHandle = { view: (name: string) => void };
export type MapCloud = { key: string; positions: Float32Array; intensity: Float32Array };
export type Marker = { position: number[]; color: [number, number, number] };
type Cloud = { key: string; mesh: THREE.Points; count: number; samples: THREE.Vector3[]; bounds: THREE.Box3; positions: Float32Array };
type World = { scene: THREE.Scene; camera: THREE.OrthographicCamera; renderer: THREE.WebGLRenderer; controls: OrbitControls; material: THREE.ShaderMaterial; chunks: Map<number, Cloud>; map: Cloud | null; grid: THREE.GridHelper; axes: THREE.AxesHelper; overlay: THREE.Group; center: THREE.Vector3; radius: number; bodyCenter: THREE.Vector3; bodyRadius: number; span: number; autoFit: boolean; capture: string };

// GPU colour/clipping: sliders never rebuild millions of positions.
// Fixed groups of 25 frames retain their buffers across timeline updates.
// ``tint`` marks the fused backend map so it renders in its own palette without
// a second material or a second uniform set.
const vertexShader = `
  attribute float intensity;
  attribute float motion;
  attribute float tint;
  varying float value;
  varying float radius;
  varying float moving;
  varying float tinted;
  uniform float pointSize;
  uniform float mode;
  void main() {
    radius = length(position);
    moving = motion;
    tinted = tint;
    value = mode < 0.5 ? position.z : (mode < 1.5 ? intensity : radius);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = pointSize;
  }
`;
const fragmentShader = `
  varying float value;
  varying float radius;
  varying float moving;
  varying float tinted;
  uniform float low;
  uniform float high;
  uniform float minDistance;
  uniform float maxDistance;
  uniform float opacity;
  uniform float showMotion;
  vec3 palette(float t) {
    vec3 a = vec3(0.10, 0.35, 0.95), b = vec3(0.08, 0.87, 0.74);
    vec3 c = vec3(0.97, 0.91, 0.24), d = vec3(1.0, 0.26, 0.16);
    return t < .33 ? mix(a,b,t/.33) : t < .66 ? mix(b,c,(t-.33)/.33) : mix(c,d,(t-.66)/.34);
  }
  vec3 mapPalette(float t) {
    vec3 a = vec3(0.06, 0.52, 0.62), b = vec3(0.87, 0.99, 0.95);
    return mix(a, b, t);
  }
  void main() {
    if (radius < minDistance || radius > maxDistance) discard;
    if (length(gl_PointCoord - vec2(.5)) > .5) discard;
    float t = clamp((value-low)/max(high-low,0.001),0.0,1.0);
    vec3 body = showMotion > .5 && moving > .5 ? vec3(1.0, .12, .08) : palette(t);
    body = mix(body, mapPalette(t), tinted);
    gl_FragColor = vec4(body, opacity);
    #include <colorspace_fragment>
  }
`;

function orient(w: World, name: string) {
  if (name === 'in' || name === 'out') {
    w.camera.zoom = THREE.MathUtils.clamp(w.camera.zoom * (name === 'in' ? 1.35 : 1 / 1.35), .05, 1000);
    w.camera.updateProjectionMatrix(); w.controls.update(); return;
  }
  // 'top' deliberately keeps a small lateral offset: a camera exactly on the +Z
  // pole has no defined azimuth, which is what made horizontal dragging break.
  const directions: Record<string, number[]> = { reset: [1, -1.4, .8], all: [1, -1.4, .8], top: [0, -.35, 1], front: [0, -1, .001], side: [1, 0, .001] };
  const center = name === 'all' ? w.center : w.bodyCenter;
  const radius = name === 'all' ? w.radius : w.bodyRadius;
  const aspect = w.renderer.domElement.clientWidth / Math.max(1, w.renderer.domElement.clientHeight);
  w.span = radius * 2.25 / Math.min(1, aspect);
  w.camera.left = -w.span * aspect / 2; w.camera.right = w.span * aspect / 2;
  w.camera.top = w.span / 2; w.camera.bottom = -w.span / 2;
  // camera.up is fixed once, before OrbitControls is constructed; never touch it here.
  w.camera.zoom = 1;
  w.camera.position.copy(center).add(new THREE.Vector3(...directions[name] as [number, number, number]).normalize().multiplyScalar(Math.max(w.radius * 3, 1000)));
  w.camera.near = .01; w.camera.far = Math.max(20000, w.radius * 12);
  w.camera.updateProjectionMatrix(); w.controls.target.copy(center); w.controls.update();
}
function quantile(values: number[], q: number) { values.sort((a, b) => a - b); return values[Math.floor((values.length - 1) * q)] || 0; }
// Polar is measured from the camera up vector (+Z), which is exactly what the
// OrbitControls clamp constrains. Exposing it keeps the orbit limits observable.
function cameraAngles(w: World) {
  const offset = w.camera.position.clone().sub(w.controls.target);
  const radius = Math.max(offset.length(), 1e-6);
  return {
    polar: Math.acos(THREE.MathUtils.clamp(offset.z / radius, -1, 1)) * 180 / Math.PI,
    azimuth: Math.atan2(offset.y, offset.x) * 180 / Math.PI,
  };
}
function clearOverlay(group: THREE.Group) {
  for (const child of [...group.children]) {
    group.remove(child);
    child.traverse(object => {
      const renderable = object as THREE.Object3D & { geometry?: THREE.BufferGeometry; material?: THREE.Material | THREE.Material[] };
      renderable.geometry?.dispose();
      if (Array.isArray(renderable.material)) renderable.material.forEach(material => material.dispose());
      else renderable.material?.dispose();
    });
  }
}
function makeGrid(cellSize: number) {
  const grid = new THREE.GridHelper(cellSize * 100, 100, '#4b6571', '#253c46');
  grid.rotation.x = Math.PI / 2;
  (grid.material as THREE.Material).transparent = true;
  (grid.material as THREE.Material).opacity = .32;
  return grid;
}
function nearestSample(w: World, event: MouseEvent, maxPixels = 18) {
  const box = w.renderer.domElement.getBoundingClientRect();
  const cursorX = event.clientX - box.left, cursorY = event.clientY - box.top;
  const projected = new THREE.Vector3();
  let best = maxPixels * maxPixels;
  let target: THREE.Vector3 | null = null;
  for (const cloud of [...w.chunks.values(), ...(w.map ? [w.map] : [])]) {
    for (const point of cloud.samples) {
      const radius = point.length();
      if (radius < w.material.uniforms.minDistance.value || radius > w.material.uniforms.maxDistance.value) continue;
      projected.copy(point).project(w.camera);
      if (projected.z < -1 || projected.z > 1) continue;
      const x = (projected.x + 1) * box.width / 2;
      const y = (1 - projected.y) * box.height / 2;
      const distance = (x - cursorX) ** 2 + (y - cursorY) ** 2;
      if (distance < best) { best = distance; target = point; }
    }
  }
  return target?.clone() ?? null;
}
function buildCloud(material: THREE.ShaderMaterial, key: string, positions: Float32Array, intensity: ArrayLike<number>, tint: number, sampleBudget = 512): Cloud {
  const count = Math.floor(positions.length / 3);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('intensity', new THREE.BufferAttribute(intensity instanceof Uint8Array ? intensity : new Uint8Array(intensity), 1));
  geometry.setAttribute('motion', new THREE.BufferAttribute(new Uint8Array(count), 1));
  geometry.setAttribute('tint', new THREE.BufferAttribute(tint ? new Uint8Array(count).fill(1) : new Uint8Array(count), 1));
  const bounds = new THREE.Box3(), point = new THREE.Vector3(), samples: THREE.Vector3[] = [];
  const stride = Math.max(1, Math.floor(count / sampleBudget));
  for (let i = 0; i < count; i++) {
    point.set(positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]);
    bounds.expandByPoint(point);
    if (i % stride === 0) samples.push(point.clone());
  }
  const mesh = new THREE.Points(geometry, material);
  mesh.frustumCulled = false;
  return { key, mesh, count, samples, bounds, positions };
}
function disposeCloud(w: World, cloud: Cloud) {
  w.scene.remove(cloud.mesh);
  cloud.mesh.geometry.dispose();
}

export const CloudView = forwardRef<ViewHandle, {
  frames: Frame[]; settings: Settings; capture: string; focusIndex: number; trail?: number[][]; selectedBand?: number | null;
  map?: MapCloud | null; trajectory?: number[][][]; markers?: Marker[];
  activeTool?: ViewerTool; measurement?: RangeMeasurement | null;
  onMeasure?: (measurement: RangeMeasurement | null) => void;
  onStats: (count: number, fps: number, camera: { azimuth: number; polar: number }) => void;
  onChunkNeeded?: (box: { min: number[]; max: number[] }) => void;
}>(function CloudView({ frames, settings, capture, focusIndex, trail, selectedBand, map, trajectory, markers, activeTool = 'navigate', measurement, onMeasure, onStats, onChunkNeeded }, ref) {
  const host = useRef<HTMLDivElement>(null);
  const world = useRef<World | null>(null);
  const stats = useRef(onStats); stats.current = onStats;
  const needChunk = useRef(onChunkNeeded); needChunk.current = onChunkNeeded;
  const tool = useRef(activeTool); tool.current = activeTool;
  const measure = useRef(onMeasure); measure.current = onMeasure;
  const count = useRef(0), measuredFps = useRef(0);
  const [error, setError] = useState('');
  const [legend, setLegend] = useState([0, 0]);
  useImperativeHandle(ref, () => ({ view(name) { if (world.current) orient(world.current, name); } }), []);

  useEffect(() => {
    const el = host.current!;
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: 'high-performance' }); }
    catch { setError('无法创建 WebGL 画布，请开启浏览器硬件加速并重新打开页面。'); return; }
    renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5)); renderer.setClearColor('#080d12');
    renderer.domElement.tabIndex = 0;
    renderer.domElement.setAttribute('aria-label', '三维点云，双击定位；F 聚焦主体，R 查看全部，1/2/3 切换视角');
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-30, 30, 30, -30, .01, 20000);
    // Z-up MUST be set before OrbitControls is constructed. OrbitControls bakes a
    // fixed "object.up -> +Y" quaternion in its constructor and never recomputes
    // it, so changing up afterwards leaves the orbit maths using Y as the pole
    // while the camera renders with Z up. The camera is then clamped onto the
    // wrong pole, where the azimuth is ill-conditioned: horizontal drags stop
    // responding and then jump unpredictably.
    camera.up.set(0, 0, 1);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true; controls.dampingFactor = .08;
    // Make orbiting feel like direct manipulation: the point cloud follows
    // the cursor instead of moving in the opposite direction.
    controls.rotateSpeed = -.45; controls.panSpeed = .85; controls.zoomSpeed = .8;
    controls.zoomToCursor = true; controls.screenSpacePanning = true;
    controls.minZoom = .05; controls.maxZoom = 1000;
    // The polar angle is measured from +Z (the camera up vector). Stop well short
    // of the pole so the azimuth stays conditioned, and stop at the horizon so
    // the scene never flips underneath the viewer.
    controls.minPolarAngle = .25; controls.maxPolarAngle = Math.PI / 2 + .02;
    const material = new THREE.ShaderMaterial({ vertexShader, fragmentShader, transparent: false, depthWrite: true,
      uniforms: { pointSize: { value: 2 }, mode: { value: 0 }, low: { value: -10 }, high: { value: 30 }, minDistance: { value: 0 }, maxDistance: { value: 1200 }, opacity: { value: 1 }, showMotion: { value: 1 } },
    });
    const grid = makeGrid(settings.gridSize);
    scene.add(grid);
    const axes = new THREE.AxesHelper(10); scene.add(axes);
    const overlay = new THREE.Group(); overlay.renderOrder = 10; scene.add(overlay);
    const w: World = { scene, camera, controls, material, chunks: new Map(), map: null, grid, axes, overlay, renderer, center: new THREE.Vector3(), bodyCenter: new THREE.Vector3(), bodyRadius: 30, radius: 30, span: 60, autoFit: true, capture: '' };
    world.current = w;
    const resize = () => { const width = el.clientWidth, height = el.clientHeight; renderer.setSize(width, height); const a = width / Math.max(height, 1); camera.left = -w.span * a / 2; camera.right = w.span * a / 2; camera.top = w.span / 2; camera.bottom = -w.span / 2; camera.updateProjectionMatrix(); };
    const observer = new ResizeObserver(resize); observer.observe(el); resize(); orient(w, 'reset');
    let token = 0, ticks = 0, then = performance.now();
    const draw = () => {
      token = requestAnimationFrame(draw); controls.update(); renderer.render(scene, camera); ticks++;
      const now = performance.now();
      if (now - then >= 1000) { measuredFps.current = Math.round(ticks * 1000 / (now - then)); stats.current(count.current, measuredFps.current, cameraAngles(w)); ticks = 0; then = now; }
    }; draw();
    // Bounded picking: do not raycast twenty million points on a double click.
    const focus = (event: MouseEvent) => {
      if (tool.current !== 'navigate') return;
      const target = nearestSample(w, event, 26);
      if (target) { camera.position.add(target.clone().sub(controls.target)); controls.target.copy(target); controls.update(); }
    };
    let pointerStart: [number, number] | null = null;
    const pointerDown = (event: PointerEvent) => { if (event.button === 0) pointerStart = [event.clientX, event.clientY]; };
    const pointerUp = (event: PointerEvent) => {
      if (tool.current !== 'range' || !pointerStart || event.button !== 0) return;
      const moved = Math.hypot(event.clientX - pointerStart[0], event.clientY - pointerStart[1]);
      pointerStart = null;
      if (moved > 5) return;
      const point = nearestSample(w, event, 22);
      if (!point) return;
      measure.current?.({
        point: [point.x, point.y, point.z],
        distance: point.length(),
        horizontal: Math.hypot(point.x, point.y),
        vertical: point.z,
      });
    };
    const key = (event: KeyboardEvent) => { const name = ({ f: 'reset', r: 'all', '1': 'top', '2': 'front', '3': 'side', '+': 'in', '=': 'in', '-': 'out' } as Record<string, string>)[event.key.toLowerCase()]; if (name) { event.preventDefault(); orient(w, name); } };
    const lost = (event: Event) => { event.preventDefault(); setError('显卡上下文已丢失，请刷新并选择较小的叠加窗口；原始文件会保留。'); };
    renderer.domElement.addEventListener('dblclick', focus); renderer.domElement.addEventListener('pointerdown', pointerDown); renderer.domElement.addEventListener('pointerup', pointerUp); renderer.domElement.addEventListener('keydown', key); renderer.domElement.addEventListener('webglcontextlost', lost);
    // Report the visible world region so the caller can stream only the map
    // chunks inside it instead of the whole map.
    const report = window.setInterval(() => {
      if (!needChunk.current) return;
      const target = controls.target;
      const half = Math.max(w.span * .6, 6);
      needChunk.current({ min: [target.x - half, target.y - half, target.z - half], max: [target.x + half, target.y + half, target.z + half] });
    }, 400);
    return () => {
      cancelAnimationFrame(token); window.clearInterval(report); observer.disconnect(); controls.dispose();
      for (const chunk of w.chunks.values()) chunk.mesh.geometry.dispose();
      if (w.map) w.map.mesh.geometry.dispose();
      clearOverlay(overlay);
      material.dispose(); grid.geometry.dispose(); (grid.material as THREE.Material).dispose(); axes.geometry.dispose(); (axes.material as THREE.Material).dispose();
      renderer.domElement.removeEventListener('dblclick', focus); renderer.domElement.removeEventListener('pointerdown', pointerDown); renderer.domElement.removeEventListener('pointerup', pointerUp); renderer.domElement.removeEventListener('keydown', key); renderer.domElement.removeEventListener('webglcontextlost', lost);
      renderer.dispose(); renderer.forceContextLoss(); el.removeChild(renderer.domElement); world.current = null;
    };
  }, []);

  // Raw capture frames: one GPU buffer per group of 25 frames.
  useEffect(() => {
    const w = world.current; if (!w) return;
    if (capture !== w.capture) {
      for (const chunk of w.chunks.values()) { w.scene.remove(chunk.mesh); chunk.mesh.geometry.dispose(); }
      w.chunks.clear(); w.capture = capture; w.autoFit = true;
    }
    const total = frames.reduce((sum, frame) => sum + frame.meta.point_count, 0);
    const stride = settings.budget === 0 ? 1 : Math.max(1, Math.ceil(total / settings.budget));
    const groups = new Map<number, Frame[]>();
    for (const frame of frames) { const group = Math.floor(frame.meta.index / 25); if (!groups.has(group)) groups.set(group, []); groups.get(group)!.push(frame); }
    for (const [id, chunk] of w.chunks) if (!groups.has(id)) { w.scene.remove(chunk.mesh); chunk.mesh.geometry.dispose(); w.chunks.delete(id); }
    for (const [id, group] of groups) {
      const key = group[0].meta.index + ':' + group.at(-1)!.meta.index + ':' + stride + ':' + (group.some(frame => frame.motion) ? 'analysis' : 'raw');
      if (w.chunks.get(id)?.key === key) continue;
      const capacity = group.reduce((sum, frame) => sum + Math.ceil(frame.meta.point_count / stride), 0);
      const positions = new Float32Array(capacity * 3), intensities = new Uint8Array(capacity), motions = new Uint8Array(capacity);
      const bounds = new THREE.Box3(), point = new THREE.Vector3(), samples: THREE.Vector3[] = [];
      let n = 0;
      const sampleStride = Math.max(1, Math.floor(capacity / 2048));
      for (const frame of group) {
        const data = frame.points;
        for (let i = 0; i < data.length; i += 4 * stride) {
          const x = data[i], y = data[i + 1], z = data[i + 2];
          if (!Number.isFinite(x + y + z) || x*x + y*y + z*z < .000001) continue;
          positions[n*3] = x; positions[n*3+1] = y; positions[n*3+2] = z; intensities[n] = data[i+3]; motions[n] = frame.motion?.[i / 4] || 0;
          point.set(x,y,z); bounds.expandByPoint(point);
          if (n % sampleStride === 0) samples.push(point.clone()); n++;
        }
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(positions.subarray(0,n*3), 3));
      geometry.setAttribute('intensity', new THREE.BufferAttribute(intensities.subarray(0,n), 1));
      geometry.setAttribute('motion', new THREE.BufferAttribute(motions.subarray(0,n), 1));
      geometry.setAttribute('tint', new THREE.BufferAttribute(new Uint8Array(n), 1));
      const mesh = new THREE.Points(geometry, w.material); mesh.frustumCulled = false;
      const old = w.chunks.get(id); if (old) { w.scene.remove(old.mesh); old.mesh.geometry.dispose(); }
      w.chunks.set(id, { key, mesh, count: n, samples, bounds, positions: positions.subarray(0, n * 3) }); w.scene.add(mesh);
    }
    refit(w);
  }, [frames, capture, settings.budget]);

  // The fused map is produced by the backend and arrives as voxel chunks. The
  // browser never transforms a point to build it.
  useEffect(() => {
    const w = world.current; if (!w) return;
    const key = `${capture}:${map?.key ?? ''}`;
    if (!map) {
      if (w.map) { disposeCloud(w, w.map); w.map = null; }
      return;
    }
    if (w.map?.key === key) return;
    if (w.map) disposeCloud(w, w.map);
    w.map = buildCloud(w.material, key, map.positions, map.intensity, 1, 1024);
    w.scene.add(w.map.mesh);
    refit(w);
  }, [map, capture]);

  useEffect(() => {
    const w = world.current; if (!w) return;
    w.grid.visible = settings.grid; w.axes.visible = settings.axes;
    if (Number(w.grid.userData.cellSize) !== settings.gridSize) {
      const position = w.grid.position.clone();
      w.scene.remove(w.grid); w.grid.geometry.dispose(); (w.grid.material as THREE.Material).dispose();
      w.grid = makeGrid(settings.gridSize); w.grid.userData.cellSize = settings.gridSize; w.grid.position.copy(position); w.grid.visible = settings.grid; w.scene.add(w.grid);
    }
    w.controls.mouseButtons.LEFT = settings.navigation === 'pan' ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE;
    w.controls.mouseButtons.RIGHT = settings.navigation === 'pan' ? THREE.MOUSE.ROTATE : THREE.MOUSE.PAN;
    const u = w.material.uniforms;
    u.pointSize.value = settings.size * w.renderer.getPixelRatio(); u.opacity.value = settings.opacity;
    w.material.transparent = settings.opacity < 1; w.material.depthWrite = settings.opacity === 1;
    u.minDistance.value = settings.min; u.maxDistance.value = settings.max;
    u.showMotion.value = settings.showMotion ? 1 : 0;
    u.mode.value = settings.color === 'height' ? 0 : settings.color === 'intensity' ? 1 : 2;
    const clouds = [...w.chunks.values(), ...(w.map ? [w.map] : [])];
    const values: number[] = []; let n = 0;
    for (const cloud of clouds) {
      if (settings.min === 0 && settings.max === 1200) n += cloud.count;
      else {
        const xyz = cloud.positions;
        for (let i = 0; i < xyz.length; i += 3) { const d = Math.hypot(xyz[i],xyz[i+1],xyz[i+2]); if (d >= settings.min && d <= settings.max) n++; }
      }
      for (const p of cloud.samples) { const d = p.length(); if (d >= settings.min && d <= settings.max) values.push(settings.color === 'height' ? p.z : d); }
    }
    u.low.value = settings.color === 'intensity' ? 0 : quantile(values,.02);
    u.high.value = settings.color === 'intensity' ? 255 : quantile(values,.98);
    count.current = n; stats.current(n, measuredFps.current, cameraAngles(w)); setLegend([u.low.value,u.high.value]);
  }, [frames, settings, capture, map]);

  useEffect(() => {
    const w = world.current; if (!w) return;
    clearOverlay(w.overlay);
    // Trajectory: one polyline per segment. Segments are never joined, because
    // two segments have no measured relative constraint.
    const palette = [0x51d1e0, 0xf0b34c, 0xa78bfa, 0xff7ab6, 0x7bd88f, 0xe0e0e0];
    (trajectory || []).forEach((segment, index) => {
      if (!segment || segment.length < 2) return;
      const geometry = new THREE.BufferGeometry().setFromPoints(segment.map(point => new THREE.Vector3(point[0], point[1], point[2])));
      const line = new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: palette[index % palette.length] }));
      (line.material as THREE.LineBasicMaterial).depthTest = false;
      line.renderOrder = 13; w.overlay.add(line);
    });
    if (markers && markers.length) {
      const positions = new Float32Array(markers.length * 3), colors = new Float32Array(markers.length * 3);
      markers.forEach((marker, index) => { positions.set(marker.position, index * 3); colors.set(marker.color, index * 3); });
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
      const points = new THREE.Points(geometry, new THREE.PointsMaterial({ size: 10, vertexColors: true, sizeAttenuation: false, depthTest: false, transparent: true }));
      points.renderOrder = 14; w.overlay.add(points);
    }
    if (measurement) {
      const origin = new THREE.Vector3(0, 0, 0);
      const point = new THREE.Vector3(...measurement.point);
      const ground = new THREE.Vector3(point.x, point.y, 0);
      const lineGeometry = new THREE.BufferGeometry().setFromPoints([origin, point]);
      const line = new THREE.Line(lineGeometry, new THREE.LineBasicMaterial({ color: 0x7cf5d4, depthTest: false }));
      line.renderOrder = 16; w.overlay.add(line);
      const components = new THREE.LineSegments(
        new THREE.BufferGeometry().setFromPoints([origin, ground, ground, point]),
        new THREE.LineDashedMaterial({ color: 0xf0b34c, dashSize: .8, gapSize: .45, depthTest: false, transparent: true, opacity: .85 }),
      );
      components.computeLineDistances(); components.renderOrder = 15; w.overlay.add(components);
      const markerGeometry = new THREE.BufferGeometry().setFromPoints([origin, point]);
      const marker = new THREE.Points(markerGeometry, new THREE.PointsMaterial({ color: 0xffffff, size: 9, sizeAttenuation: false, depthTest: false }));
      marker.renderOrder = 17; w.overlay.add(marker);
    }
    if (!settings.showMotion) return;
    const frame = frames.find(item => item.meta.index === focusIndex);
    if (!frame?.motionMeta?.ready) return;
    // Boxes come from the current time slice only. The travelled path is drawn
    // as a separate trail, so a long band never inflates the body-sized box.
    for (const target of frame.motionMeta.targets) {
      const box = new THREE.Box3(new THREE.Vector3(...target.minimum as [number, number, number]), new THREE.Vector3(...target.maximum as [number, number, number]));
      const selected = selectedBand === target.band_id;
      const helper = new THREE.Box3Helper(box, selected ? 0xffd23d : target.status === 'CONFIRMED' ? 0xff3d2e : 0xf0b34c);
      (helper.material as THREE.LineBasicMaterial).depthTest = false; helper.renderOrder = 11; w.overlay.add(helper);
      if (target.speed < .01) continue;
      const direction = new THREE.Vector3(...target.velocity as [number, number, number]);
      if (direction.lengthSq() < 1e-6) continue;
      direction.normalize();
      const arrow = new THREE.ArrowHelper(direction, new THREE.Vector3(...target.position as [number, number, number]), Math.max(.5, Math.min(4, target.speed * 2)), 0xff3d2e, .22, .12);
      arrow.traverse(object => { const item = object as THREE.Object3D & { material?: THREE.Material }; if (item.material) item.material.depthTest = false; });
      arrow.renderOrder = 12; w.overlay.add(arrow);
    }
    if (trail && trail.length > 1) {
      const geometry = new THREE.BufferGeometry().setFromPoints(trail.map(point => new THREE.Vector3(...point as [number, number, number])));
      const line = new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: 0xffd23d, transparent: true, opacity: .9 }));
      (line.material as THREE.LineBasicMaterial).depthTest = false; line.renderOrder = 13; w.overlay.add(line);
    }
  }, [frames, focusIndex, settings.showMotion, selectedBand, trail, trajectory, markers, measurement]);

  // Framing: union of every resident cloud, with the 5%-95% body range driving
  // the default view. Distant returns are kept, never filtered.
  function refit(w: World) {
    const clouds = [...w.chunks.values(), ...(w.map ? [w.map] : [])];
    const bounds = new THREE.Box3(), xs: number[] = [], ys: number[] = [], zs: number[] = [];
    for (const cloud of clouds) {
      bounds.union(cloud.bounds);
      for (const p of cloud.samples) { xs.push(p.x); ys.push(p.y); zs.push(p.z); }
    }
    if (!xs.length) return;
    bounds.getCenter(w.center); w.radius = Math.max(bounds.getSize(new THREE.Vector3()).length()/2, 1);
    const body = new THREE.Box3(new THREE.Vector3(quantile(xs,.05), quantile(ys,.05), quantile(zs,.05)), new THREE.Vector3(quantile(xs,.95), quantile(ys,.95), quantile(zs,.95)));
    body.getCenter(w.bodyCenter); w.bodyRadius = Math.max(body.getSize(new THREE.Vector3()).length()/2, 1);
    w.grid.position.set(w.bodyCenter.x, w.bodyCenter.y, body.min.z - 1);
    if (w.autoFit) { orient(w, 'reset'); w.autoFit = false; }
  }

  return <><div className={`cloud-canvas ${activeTool === 'range' ? 'range-cursor' : ''}`} ref={host} data-testid="cloud-canvas" />
    {error && <div className="canvas-error">{error}</div>}
    {frames.length > 0 && <div className="legend"><span>{settings.color === 'height' ? '高度 Z · m' : settings.color === 'intensity' ? '反射率' : '距离 · m'}</span><div className="spectrum"/><div className="legend-values"><span>{legend[0].toFixed(1)}</span><span>{legend[1].toFixed(1)}</span></div></div>}
  </>;
});

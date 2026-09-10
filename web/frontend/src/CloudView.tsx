import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import type { Frame } from './api';

export type Settings = { color: string; size: number; opacity: number; min: number; max: number; accumulation: number; grid: boolean; axes: boolean; budget: number; navigation: string };
export type ViewHandle = { view: (name: string) => void };
type Chunk = { key: string; mesh: THREE.Points; count: number; samples: THREE.Vector3[]; bounds: THREE.Box3 };
type World = { scene: THREE.Scene; camera: THREE.OrthographicCamera; renderer: THREE.WebGLRenderer; controls: OrbitControls; material: THREE.ShaderMaterial; chunks: Map<number, Chunk>; grid: THREE.GridHelper; axes: THREE.AxesHelper; center: THREE.Vector3; radius: number; bodyCenter: THREE.Vector3; bodyRadius: number; span: number; autoFit: boolean; capture: string };

// GPU colour/clipping: sliders never rebuild millions of positions.
// Fixed groups of 25 frames retain their buffers across timeline updates.
const vertexShader = `
  attribute float intensity;
  varying float value;
  varying float radius;
  uniform float pointSize;
  uniform float mode;
  void main() {
    radius = length(position);
    value = mode < 0.5 ? position.z : (mode < 1.5 ? intensity : radius);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = pointSize;
  }
`;
const fragmentShader = `
  varying float value;
  varying float radius;
  uniform float low;
  uniform float high;
  uniform float minDistance;
  uniform float maxDistance;
  uniform float opacity;
  vec3 palette(float t) {
    vec3 a = vec3(0.10, 0.35, 0.95), b = vec3(0.08, 0.87, 0.74);
    vec3 c = vec3(0.97, 0.91, 0.24), d = vec3(1.0, 0.26, 0.16);
    return t < .33 ? mix(a,b,t/.33) : t < .66 ? mix(b,c,(t-.33)/.33) : mix(c,d,(t-.66)/.34);
  }
  void main() {
    if (radius < minDistance || radius > maxDistance) discard;
    if (length(gl_PointCoord - vec2(.5)) > .5) discard;
    float t = clamp((value-low)/max(high-low,0.001),0.0,1.0);
    gl_FragColor = vec4(palette(t), opacity);
    #include <colorspace_fragment>
  }
`;

function orient(w: World, name: string) {
  if (name === 'in' || name === 'out') {
    w.camera.zoom = THREE.MathUtils.clamp(w.camera.zoom * (name === 'in' ? 1.35 : 1 / 1.35), .05, 1000);
    w.camera.updateProjectionMatrix(); w.controls.update(); return;
  }
  const directions: Record<string, number[]> = { reset: [1, -1.4, .8], all: [1, -1.4, .8], top: [0, -.001, 1], front: [0, -1, .001], side: [1, 0, .001] };
  const center = name === 'all' ? w.center : w.bodyCenter;
  const radius = name === 'all' ? w.radius : w.bodyRadius;
  const aspect = w.renderer.domElement.clientWidth / Math.max(1, w.renderer.domElement.clientHeight);
  w.span = radius * 2.25 / Math.min(1, aspect);
  w.camera.left = -w.span * aspect / 2; w.camera.right = w.span * aspect / 2;
  w.camera.top = w.span / 2; w.camera.bottom = -w.span / 2;
  w.camera.zoom = 1; w.camera.up.set(0, 0, 1);
  w.camera.position.copy(center).add(new THREE.Vector3(...directions[name] as [number, number, number]).normalize().multiplyScalar(Math.max(w.radius * 3, 1000)));
  w.camera.near = .01; w.camera.far = Math.max(20000, w.radius * 12);
  w.camera.updateProjectionMatrix(); w.controls.target.copy(center); w.controls.update();
}
function quantile(values: number[], q: number) { values.sort((a, b) => a - b); return values[Math.floor((values.length - 1) * q)] || 0; }

export const CloudView = forwardRef<ViewHandle, { frames: Frame[]; settings: Settings; capture: string; onStats: (count: number, fps: number) => void }>(function CloudView({ frames, settings, capture, onStats }, ref) {
  const host = useRef<HTMLDivElement>(null);
  const world = useRef<World | null>(null);
  const stats = useRef(onStats); stats.current = onStats;
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
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true; controls.dampingFactor = .15;
    controls.rotateSpeed = .45; controls.panSpeed = .85; controls.zoomSpeed = .8;
    controls.zoomToCursor = true; controls.screenSpacePanning = true;
    controls.minZoom = .05; controls.maxZoom = 1000;
    controls.minPolarAngle = .001; controls.maxPolarAngle = Math.PI - .001;
    const material = new THREE.ShaderMaterial({ vertexShader, fragmentShader, transparent: false, depthWrite: true,
      uniforms: { pointSize: { value: 2 }, mode: { value: 0 }, low: { value: -10 }, high: { value: 30 }, minDistance: { value: 0 }, maxDistance: { value: 1200 }, opacity: { value: 1 } },
    });
    const grid = new THREE.GridHelper(500, 50, '#4b6571', '#253c46');
    grid.rotation.x = Math.PI / 2;
    (grid.material as THREE.Material).transparent = true; (grid.material as THREE.Material).opacity = .32;
    scene.add(grid);
    const axes = new THREE.AxesHelper(10); scene.add(axes);
    const w: World = { scene, camera, controls, material, chunks: new Map(), grid, axes, renderer, center: new THREE.Vector3(), bodyCenter: new THREE.Vector3(), bodyRadius: 30, radius: 30, span: 60, autoFit: true, capture: '' };
    world.current = w;
    const resize = () => { const width = el.clientWidth, height = el.clientHeight; renderer.setSize(width, height); const a = width / Math.max(height, 1); camera.left = -w.span * a / 2; camera.right = w.span * a / 2; camera.top = w.span / 2; camera.bottom = -w.span / 2; camera.updateProjectionMatrix(); };
    const observer = new ResizeObserver(resize); observer.observe(el); resize(); orient(w, 'reset');
    let token = 0, ticks = 0, then = performance.now();
    const draw = () => {
      token = requestAnimationFrame(draw); controls.update(); renderer.render(scene, camera); ticks++;
      const now = performance.now();
      if (now - then >= 1000) { measuredFps.current = Math.round(ticks * 1000 / (now - then)); stats.current(count.current, measuredFps.current); ticks = 0; then = now; }
    }; draw();
    // Bounded picking: do not raycast twenty million points on a double click.
    const focus = (event: MouseEvent) => {
      const box = renderer.domElement.getBoundingClientRect();
      const cursor = new THREE.Vector2((event.clientX - box.left) / box.width * 2 - 1, 1 - (event.clientY - box.top) / box.height * 2);
      let best = .08 ** 2; let target: THREE.Vector3 | undefined;
      const projected = new THREE.Vector3();
      for (const chunk of w.chunks.values()) for (const point of chunk.samples) {
        const radius = point.length();
        if (radius < material.uniforms.minDistance.value || radius > material.uniforms.maxDistance.value) continue;
        projected.copy(point).project(camera);
        if (Math.abs(projected.z) > 1) continue;
        const distance = (projected.x - cursor.x) ** 2 + (projected.y - cursor.y) ** 2;
        if (distance < best) { best = distance; target = point; }
      }
      if (target) { camera.position.add(target.clone().sub(controls.target)); controls.target.copy(target); controls.update(); }
    };
    const key = (event: KeyboardEvent) => { const name = ({ f: 'reset', r: 'all', '1': 'top', '2': 'front', '3': 'side', '+': 'in', '=': 'in', '-': 'out' } as Record<string, string>)[event.key.toLowerCase()]; if (name) { event.preventDefault(); orient(w, name); } };
    const lost = (event: Event) => { event.preventDefault(); setError('显卡上下文已丢失，请刷新并选择较小的叠加窗口；原始文件会保留。'); };
    renderer.domElement.addEventListener('dblclick', focus); renderer.domElement.addEventListener('keydown', key); renderer.domElement.addEventListener('webglcontextlost', lost);
    return () => {
      cancelAnimationFrame(token); observer.disconnect(); controls.dispose();
      for (const chunk of w.chunks.values()) chunk.mesh.geometry.dispose();
      material.dispose(); grid.geometry.dispose(); (grid.material as THREE.Material).dispose(); axes.geometry.dispose(); (axes.material as THREE.Material).dispose();
      renderer.domElement.removeEventListener('dblclick', focus); renderer.domElement.removeEventListener('keydown', key); renderer.domElement.removeEventListener('webglcontextlost', lost);
      renderer.dispose(); renderer.forceContextLoss(); el.removeChild(renderer.domElement); world.current = null;
    };
  }, []);

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
      const key = group[0].meta.index + ':' + group.at(-1)!.meta.index + ':' + stride;
      if (w.chunks.get(id)?.key === key) continue;
      const capacity = group.reduce((sum, frame) => sum + Math.ceil(frame.meta.point_count / stride), 0);
      const positions = new Float32Array(capacity * 3), intensities = new Uint8Array(capacity);
      const bounds = new THREE.Box3(), point = new THREE.Vector3(), samples: THREE.Vector3[] = [];
      let n = 0;
      const sampleStride = Math.max(1, Math.floor(capacity / 512));
      for (const frame of group) {
        const data = frame.points;
        for (let i = 0; i < data.length; i += 4 * stride) {
          const x = data[i], y = data[i + 1], z = data[i + 2];
          if (!Number.isFinite(x + y + z) || x*x + y*y + z*z < .000001) continue;
          positions[n*3] = x; positions[n*3+1] = y; positions[n*3+2] = z; intensities[n] = data[i+3];
          point.set(x,y,z); bounds.expandByPoint(point);
          if (n % sampleStride === 0) samples.push(point.clone()); n++;
        }
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(positions.subarray(0,n*3), 3));
      geometry.setAttribute('intensity', new THREE.BufferAttribute(intensities.subarray(0,n), 1));
      const mesh = new THREE.Points(geometry, w.material); mesh.frustumCulled = false;
      const old = w.chunks.get(id); if (old) { w.scene.remove(old.mesh); old.mesh.geometry.dispose(); }
      w.chunks.set(id, { key, mesh, count: n, samples, bounds }); w.scene.add(mesh);
    }
    const bounds = new THREE.Box3(), xs: number[] = [], ys: number[] = [], zs: number[] = [];
    for (const chunk of w.chunks.values()) {
      bounds.union(chunk.bounds);
      for (const p of chunk.samples) { xs.push(p.x); ys.push(p.y); zs.push(p.z); }
    }
    if (xs.length) {
      bounds.getCenter(w.center); w.radius = Math.max(bounds.getSize(new THREE.Vector3()).length()/2, 1);
      // Framing only: distant returns are retained, not filtered.
      const body = new THREE.Box3(new THREE.Vector3(quantile(xs,.05), quantile(ys,.05), quantile(zs,.05)), new THREE.Vector3(quantile(xs,.95), quantile(ys,.95), quantile(zs,.95)));
      body.getCenter(w.bodyCenter); w.bodyRadius = Math.max(body.getSize(new THREE.Vector3()).length()/2, 1);
      w.grid.position.set(w.bodyCenter.x, w.bodyCenter.y, body.min.z - 1);
      if (w.autoFit) { orient(w, 'reset'); w.autoFit = false; }
    }
  }, [frames, capture, settings.budget]);

  useEffect(() => {
    const w = world.current; if (!w) return;
    w.grid.visible = settings.grid; w.axes.visible = settings.axes;
    w.controls.mouseButtons.LEFT = settings.navigation === 'pan' ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE;
    w.controls.mouseButtons.RIGHT = settings.navigation === 'pan' ? THREE.MOUSE.ROTATE : THREE.MOUSE.PAN;
    const u = w.material.uniforms;
    u.pointSize.value = settings.size * w.renderer.getPixelRatio(); u.opacity.value = settings.opacity;
    w.material.transparent = settings.opacity < 1; w.material.depthWrite = settings.opacity === 1;
    u.minDistance.value = settings.min; u.maxDistance.value = settings.max;
    u.mode.value = settings.color === 'height' ? 0 : settings.color === 'intensity' ? 1 : 2;
    const values: number[] = []; let n = 0;
    for (const chunk of w.chunks.values()) {
      if (settings.min === 0 && settings.max === 1200) n += chunk.count;
      else {
        const xyz = chunk.mesh.geometry.getAttribute('position').array;
        for (let i = 0; i < xyz.length; i += 3) { const d = Math.hypot(xyz[i],xyz[i+1],xyz[i+2]); if (d >= settings.min && d <= settings.max) n++; }
      }
      for (const p of chunk.samples) { const d = p.length(); if (d >= settings.min && d <= settings.max) values.push(settings.color === 'height' ? p.z : d); }
    }
    u.low.value = settings.color === 'intensity' ? 0 : quantile(values,.02);
    u.high.value = settings.color === 'intensity' ? 255 : quantile(values,.98);
    count.current = n; stats.current(n, measuredFps.current); setLegend([u.low.value,u.high.value]);
  }, [frames, settings, capture]);

  return <><div className="cloud-canvas" ref={host} data-testid="cloud-canvas" />
    {error && <div className="canvas-error">{error}</div>}
    {frames.length > 0 && <div className="legend"><span>{settings.color === 'height' ? '高度 Z · m' : settings.color === 'intensity' ? '反射率' : '距离 · m'}</span><div className="spectrum"/><div className="legend-values"><span>{legend[0].toFixed(1)}</span><span>{legend[1].toFixed(1)}</span></div></div>}
  </>;
});

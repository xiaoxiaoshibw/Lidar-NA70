// SYNTHETIC: verify causal point stacking and the visible range/count controls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sample = {tid: 1, euler: [0, 0, 0], gyro: [0, 0, 0], accel: [0, 0, 9.8], quaternion: [1, 0, 0, 0]};
const frame = (index, coordinates) => ({index, start: index, end: index + .5, complete: true,
  rays: coordinates.length, eligible: coordinates.length, count: coordinates.length,
  xyzi: Buffer.from(new Float32Array(coordinates.flatMap(x => [x, 0, 0, 1])).buffer).toString('base64')});
const data = {frames: [frame(0, [1]), frame(1, [2, 3]), frame(2, [4, 5, 6])],
  batches: [{t: 0, count: 1, min: [0, 0, 0], max: [0, 0, 0], euler: [0, 0, 0],
    gyro: [0, 0, 0], gyro_min: [0, 0, 0], gyro_max: [0, 0, 0], samples: [sample]}],
  poses: [{t: 0, x: 0, y: 0}, {t: 1, x: 1, y: 1}],
  model: {vertices: [[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces: [{group: 'body', v: [0, 1, 2]}]}};
const template = fs.readFileSync(path.join(__dirname, '../../tools/na70_host_timeline_view.html'), 'utf8');
const source = template.split('<script>')[1].split('</script>')[0].replace('/*__DATA__*/', JSON.stringify(data));
let pixels = 0;
const cloudCtx = new Proxy({fillRect() {pixels++;}}, {get(target, prop) {return target[prop] ?? (() => {});}});
const otherCtx = new Proxy({}, {get(target, prop) {return target[prop] ?? (() => {});}});
const elements = {};
const doc = {getElementById(id) {
  if (!elements[id]) elements[id] = {value: id === 'range' ? '150' : id === 'speed' ? '1' : id === 'stack' ? '10' : '0',
    clientWidth: 800, clientHeight: 400, getContext: () => id === 'cloud' ? cloudCtx : otherCtx, setPointerCapture() {}};
  return elements[id];
}};
const context = {document: doc, window: {}, devicePixelRatio: 1, requestAnimationFrame() {},
  atob: s => Buffer.from(s, 'base64').toString('binary'), Uint8Array, Float32Array,
  setInterval: () => 1, clearInterval() {}};
vm.runInNewContext(source, context);
assert.equal(vm.runInNewContext('densityPointIndex(109,1222,1200)', context), 110,
  'match NumPy linspace rounding for the previous display');
assert.equal(vm.runInNewContext('densityPointIndex(1199,1222,1200)', context), 1221);
assert.equal(pixels, 1, 'first frame must not draw future frames');
assert.match(elements.cloudInfo.textContent, /源帧 0–0.*实际堆叠 1 帧/);
elements.stack.value = '100'; pixels = 0; elements.stack.onchange();
assert.equal(pixels, 1, 'a long window must still stop at first frame');
elements.seek.value = '1'; pixels = 0; elements.seek.oninput();
assert.equal(pixels, 3, 'middle frame includes only itself and its predecessor');
assert.match(elements.cloudInfo.textContent, /源帧 0–1.*实际堆叠 2 帧/);
assert.match(elements.cloudInfo.textContent, /抽样 3/);
elements.seek.value = '2'; pixels = 0; elements.seek.oninput();
assert.equal(pixels, 6, 'end frame includes all three frames');
assert.match(elements.cloudInfo.textContent, /源帧 0–2.*实际堆叠 3 帧/);
assert.match(elements.cloudInfo.textContent, /抽样 6/);
elements.density.value = '2'; pixels = 0; elements.density.onchange();
assert.equal(pixels, 5, 'density cap is applied separately to each frame');
assert.match(elements.cloudInfo.textContent, /已导出 6，显示抽样 5/);
elements.density.value = '0';
elements.topView.onclick();
assert.equal(vm.runInNewContext('az', context), 0);
assert.equal(vm.runInNewContext('elev', context), Math.PI / 2, 'XY top view');
elements.frontView.onclick();
assert.equal(vm.runInNewContext('az', context), 0);
assert.equal(vm.runInNewContext('elev', context), 0, 'XZ side view');
elements.reset.onclick();
elements.stack.value = '1'; pixels = 0; elements.stack.onchange();
assert.equal(pixels, 3, 'single-frame mode must discard history');
assert.match(elements.cloudInfo.textContent, /源帧 2–2.*实际堆叠 1 帧/);
elements.range.value = '5'; pixels = 0; elements.range.onchange();
assert.equal(pixels, 2, 'display distance still filters points after changing stack');
console.log('SYNTHETIC causal stack boundaries, counts, single-frame and distance filters: PASS');

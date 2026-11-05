// SYNTHETIC logic check without a browser: timeline, frame controls and canvas calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sample = tid => ({tid, euler: [tid, 0, 0], gyro: [tid, 0, 0],
  accel: [0, 0, 9.8], quaternion: [1, 0, 0, 0]});
const batch = (t, tid) => ({t, count: 2, min: [0, 0, 0], max: [1, 0, 0],
  euler: [1, 0, 0], gyro: [1, 0, 0], gyro_min: [0, 0, 0],
  gyro_max: [1, 0, 0], samples: [sample(tid), sample(tid + 1)]});
const frame = (index, end) => ({index, start: end, end, complete: true,
  rays: 1, eligible: 1, count: 1, xyzi: Buffer.from(new Float32Array([1, 1, 1, 1]).buffer).toString('base64')});
const data = {frames: [frame(0, 0), frame(1, 1), frame(2, 2)],
  batches: [batch(.5, 10), batch(1.5, 20)],
  poses: [{t: .8, x: 0, y: 0}, {t: 1.8, x: 1, y: 1}],
  model: {vertices: [[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces: [{group: 'body', v: [0, 1, 2]}]}};
const template = fs.readFileSync(path.join(__dirname, '../../tools/na70_host_timeline_view.html'), 'utf8');
const source = template.split('<script>')[1].split('</script>')[0].replace('/*__DATA__*/', JSON.stringify(data));
const context2d = new Proxy({}, {get(target, prop) {return target[prop] ?? (() => {});}});
const elements = {};
const doc = {getElementById(id) {
  if (!elements[id]) elements[id] = {value: id === 'range' ? '150' : id === 'speed' ? '1' : '0',
    clientWidth: 800, clientHeight: 400, getContext: () => context2d, setPointerCapture() {}};
  return elements[id];
}};
let timer;
const context = {document: doc, window: {}, devicePixelRatio: 1, requestAnimationFrame() {},
  atob: s => Buffer.from(s, 'base64').toString('binary'), Uint8Array, Float32Array,
  setInterval: fn => {timer = fn; return 1;}, clearInterval: () => {timer = null;}};
vm.runInNewContext(source, context);
assert.match(elements.imuInfo.textContent, /无 IMU 数据/);
elements.seek.value = '1'; elements.seek.oninput();
assert.match(elements.imuInfo.textContent, /tid=11/);
assert.match(elements.poseInfo.textContent, /当前位姿/);
elements.sampleSeek.value = '0'; elements.sampleSeek.oninput();
assert.match(elements.imuInfo.textContent, /tid=10/);
elements.batchSeek.value = '1'; elements.batchSeek.oninput();
assert.match(elements.batchLabel.textContent, /手动选批/);
assert.match(elements.imuInfo.textContent, /tid=21/);
elements.batchPlay.onclick();
assert.match(elements.imuInfo.textContent, /tid=20/);
assert.ok(timer); timer();
assert.match(elements.imuInfo.textContent, /tid=21/);
elements.seek.value = '2'; elements.seek.oninput();
assert.match(elements.imuInfo.textContent, /无 IMU 数据/);
assert.match(elements.poseInfo.textContent, /无历史 LIO 轨迹/);
console.log('SYNTHETIC timeline boundaries, batch selection and frame-sequence controls: PASS');

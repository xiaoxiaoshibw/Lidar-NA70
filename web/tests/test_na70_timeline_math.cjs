// SYNTHETIC: exercise the quaternion functions used by the offline viewer.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../../tools/na70_host_timeline_view.html'), 'utf8');
const names = ['qNormalize', 'qMultiply', 'qRelative', 'qRotate'];
const functions = names.map(name => {
  const match = html.match(new RegExp(`^function ${name}\\([^\\n]+$`, 'm'));
  assert.ok(match, `missing ${name}`);
  return match[0];
}).join('\n');
const ctx = {};
vm.runInNewContext(functions + '\nthis.api = {qRelative, qRotate};', ctx);
const { qRelative, qRotate } = ctx.api;
function near(actual, expected) {
  assert.equal(actual.length, expected.length);
  actual.forEach((n, i) => assert.ok(Math.abs(n - expected[i]) < 1e-10));
}
const s = Math.SQRT1_2;
near(qRotate(qRelative([1, 0, 0, 0], [1, 0, 0, 0]), [1, 0, 0]), [1, 0, 0]);
near(qRotate(qRelative([1, 0, 0, 0], [s, 0, 0, s]), [1, 0, 0]), [0, 1, 0]);
near(qRotate(qRelative([1, 0, 0, 0], [-s, 0, 0, -s]), [1, 0, 0]), [0, 1, 0]);
near(qRotate(qRelative([s, 0, 0, s], [s, 0, 0, s]), [1, 0, 0]), [1, 0, 0]);
console.log('SYNTHETIC quaternion identity, 90-degree rotation, sign equivalence, and initial reference: PASS');

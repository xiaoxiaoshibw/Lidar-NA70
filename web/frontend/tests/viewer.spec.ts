import { test, expect } from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';

const captures = path.resolve('../..', 'radar_capture_data');
const evidence = path.resolve('..', 'test-results');
test('real PCAP import, playback, seek, display controls, persistence and cancellation', async ({ page, request }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('/');
  await expect(page.getByText('先静止采集，再缓慢移动雷达')).toBeVisible();
  await page.screenshot({ path: path.join(evidence, 'webui-empty.png') });
  const results = [];
  for (const name of ['1.pcap', '2.pcap']) {
    const started = performance.now();
    await page.getByTestId('file-input').setInputFiles(path.join(captures, name));
    await expect(page.locator('.canvas-meta')).toContainText('200 帧叠加', { timeout: 45000 });
    await expect(page.locator('.processing')).toHaveCount(0);
    const firstFrameMs = performance.now() - started;
    await page.waitForTimeout(1500);
    const initialFPS = Number(await page.getByTestId('fps').textContent());
    await page.getByTitle('播放', { exact: true }).click();
    await page.waitForTimeout(5000);
    await page.getByTitle('暂停', { exact: true }).click();
    const playbackFrame = Number(await page.getByLabel('回放时间轴').inputValue());
    expect(playbackFrame).toBeGreaterThan(30);
    await page.getByLabel('回放时间轴').fill('10');
    await expect(page.locator('.canvas-meta')).toContainText('FRAME 00011');
    await page.getByLabel('叠加帧数').selectOption('10');
    await expect(page.locator('.canvas-meta')).toContainText('10 帧叠加');
    await page.getByRole('button', { name: '反射率', exact: true }).click();
    await expect(page.locator('.legend')).toContainText('反射率');
    await page.getByRole('button', { name: '俯视', exact: true }).click();
    await page.getByLabel('点大小', { exact: true }).fill('3');
    await page.getByLabel('透明度', { exact: true }).fill('0.75');
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(evidence, `webui-${name}.png`) });
    // Rapid seek and file/window changes must not render a stale request.
    await page.getByLabel('回放时间轴').fill('200');
    await page.getByLabel('回放时间轴').fill('20');
    await expect(page.locator('.canvas-meta')).toContainText('FRAME 00021');
    await page.getByLabel('叠加帧数').selectOption('1');
    await page.getByTitle('重置显示设置').click();
    const max = Number(await page.getByLabel('回放时间轴').getAttribute('max'));
    await page.getByLabel('回放时间轴').fill(String(max));
    await expect(page.locator('.canvas-meta')).toContainText('不完整帧');
    const mem = await page.evaluate(() => (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory?.usedJSHeapSize ?? null);
    results.push({ file: name, firstFrameMs, initialFPS, framesInFiveSeconds: playbackFrame, usedJSHeapBytes: mem });
    await page.getByLabel('回放时间轴').fill('0');
    await expect(page.locator('.canvas-meta')).toContainText('FRAME 00001');
  }
  // Refresh retains imported records, without automatically selecting stale data.
  await page.reload();
  await page.locator('.file-select').filter({ hasText: '2.pcap' }).first().click();
  await expect(page.locator('.canvas-meta')).toContainText('FRAME 00001');
  await page.getByRole('button', { name: '侧视', exact: true }).click();
  await page.getByRole('button', { name: '正视', exact: true }).click();
  await page.getByRole('button', { name: '距离', exact: true }).click();
  await page.getByLabel('最小距离', { exact: true }).fill('5');
  await page.getByLabel('最大距离', { exact: true }).fill('100');
  await page.getByLabel('渲染点数上限').selectOption('1000000');
  await page.getByLabel('坐标网格', { exact: true }).uncheck();
  await page.getByLabel('坐标轴', { exact: true }).uncheck();
  await page.getByTitle('重置显示设置').click();
  await page.getByTitle('复位视角').click();
  await page.screenshot({ path: path.join(evidence, 'webui-final.png') });
  const list = await (await request.get('/api/captures')).json();
  fs.writeFileSync(path.join(evidence, 'browser-metrics.json'), JSON.stringify({ results, captures: list, errors }, null, 2));
  expect(errors).toEqual([]);
});

test('invalid file feedback and original-file-preserving removal', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('file-input').setInputFiles({ name: 'invalid-test.pcap', mimeType: 'application/octet-stream', buffer: Buffer.from('not a valid packet capture') });
  await expect(page.getByText('无法读取此文件')).toBeVisible();
  await page.getByRole('button', { name: '移除记录', exact: true }).click();
  await expect(page.getByText('已移除导入副本，原始 PCAP 文件保留。')).toBeVisible();
  await expect(page.locator('.file-select').filter({ hasText: 'invalid-test.pcap' })).toHaveCount(0);
});

test('toolbox measures from the coordinate origin and grid scale is configurable', async ({ page }) => {
  const synthetic = path.resolve('../test-results/toolbox-synthetic.pcap');
  execFileSync(path.resolve('../.venv/Scripts/python.exe'), ['-c',
    "import sys; from pathlib import Path; sys.path.insert(0, 'tests'); from test_mapping_api import scene; from test_decoder import make_pcap; Path('test-results').mkdir(exist_ok=True); make_pcap(Path('test-results/toolbox-synthetic.pcap'), scene(3))"],
    { cwd: path.resolve('..') });
  await page.goto('/');
  await page.getByTestId('file-input').setInputFiles(synthetic);
  await expect(page.locator('.canvas-meta')).toContainText('FRAME 00001', { timeout: 90000 });

  await expect(page.getByLabel('视图工具箱')).toBeVisible();
  await page.getByRole('button', { name: '原点测距', exact: true }).click();
  const canvas = page.locator('[data-testid="cloud-canvas"] canvas');
  await expect(canvas).toHaveCSS('cursor', /url\(/);
  const box = (await canvas.boundingBox())!;
  const offsets = [[.5, .5], [.42, .5], [.58, .5], [.5, .42], [.5, .58], [.35, .5], [.65, .5]];
  for (const [x, y] of offsets) {
    await page.mouse.click(box.x + box.width * x, box.y + box.height * y);
    if (await page.getByLabel('测距结果').count()) break;
  }
  await expect(page.getByLabel('测距结果')).toContainText('三维距离');
  await expect(page.getByLabel('测距结果')).toContainText('XY 水平');
  await expect(page.getByLabel('测距结果')).toContainText('坐标 XYZ');

  await page.getByLabel('网格每格距离').selectOption('5');
  await expect(page.locator('.axis-label')).toContainText('网格 5 m');
  await page.screenshot({ path: path.join(evidence, 'webui-toolbox-measurement.png') });
  await page.getByTitle('清除测量').click();
  await expect(page.getByLabel('测距结果')).toHaveCount(0);
  await page.getByRole('button', { name: '浏览', exact: true }).click();
  await expect(canvas).not.toHaveCSS('cursor', /url\(/);
});

async function drag(page, from: [number, number], to: [number, number]) {
  await page.mouse.move(from[0], from[1]);
  await page.mouse.down();
  for (let step = 1; step <= 16; step++) {
    await page.mouse.move(from[0] + (to[0] - from[0]) * step / 16, from[1] + (to[1] - from[1]) * step / 16);
  }
  await page.mouse.up();
  // Let the damping settle and the once-per-second camera readout refresh.
  await page.waitForTimeout(1300);
}

// Regression: OrbitControls bakes its "up -> +Y" quaternion in the constructor.
// Setting camera.up afterwards left the orbit maths and the render using
// different poles, so the camera was clamped onto the wrong axis, horizontal
// drags stopped responding and then jumped unpredictably.
test('horizontal orbit keeps responding and never collapses onto the pole', async ({ page, request }) => {
  test.setTimeout(180000);
  await page.goto('/');
  const list = await (await request.get('/api/captures')).json() as Array<{ filename: string }>;
  const name = list.some(capture => capture.filename === '1.pcap') ? '1.pcap' : '2.pcap';
  await page.locator('.file-select').filter({ hasText: name }).first().click();
  await expect(page.locator('.canvas-meta')).toContainText('FRAME 00001', { timeout: 90000 });
  const canvas = page.locator('[data-testid="cloud-canvas"] canvas');
  await expect(canvas).toBeVisible();
  const box = (await canvas.boundingBox())!;
  const centre: [number, number] = [box.x + box.width / 2, box.y + box.height / 2];
  const span = box.width * 0.15;
  const read = async () => ({
    azimuth: Number(await page.getByTestId('camera-state').getAttribute('data-azimuth')),
    polar: Number(await page.getByTestId('camera-state').getAttribute('data-polar')),
  });

  await page.waitForTimeout(1300);
  const first = await read();
  const azimuths = [first.azimuth];
  const polars = [first.polar];
  for (let sweep = 0; sweep < 6; sweep++) {
    await drag(page, [centre[0] - span, centre[1]], [centre[0] + span, centre[1]]);
    const state = await read();
    azimuths.push(state.azimuth);
    polars.push(state.polar);
  }
  // Every sweep must still move the camera; a stuck axis would repeat the angle.
  const moved = azimuths.slice(1).filter((value, index) => Math.abs(value - azimuths[index]) > 5);
  expect(moved.length).toBeGreaterThanOrEqual(5);
  // Polar is measured from +Z: it must stay clear of the pole and above the horizon.
  expect(Math.min(...polars)).toBeGreaterThan(10);
  expect(Math.max(...polars)).toBeLessThan(95);

  // Dragging far past the top must clamp short of the pole rather than collapse.
  for (let repeat = 0; repeat < 2; repeat++) {
    await drag(page, [centre[0], centre[1] + box.height * 0.45], [centre[0], centre[1] - box.height * 0.45]);
  }
  const top = await read();
  expect(top.polar).toBeGreaterThan(10);
  expect(top.polar).toBeLessThan(95);
  // The preset top view must also stay off the pole.
  await page.getByRole('button', { name: '俯视', exact: true }).click();
  await page.waitForTimeout(1300);
  const preset = await read();
  expect(preset.polar).toBeGreaterThan(10);
  await page.screenshot({ path: path.join(evidence, 'webui-orbit.png') });
});

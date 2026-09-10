import { test, expect } from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs';

const captures = path.resolve('../..', 'radar_capture_data');
const evidence = path.resolve('..', 'test-results');
test('real PCAP import, playback, seek, display controls, persistence and cancellation', async ({ page, request }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('/');
  await expect(page.getByText('从一次扫描，看见三维空间')).toBeVisible();
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

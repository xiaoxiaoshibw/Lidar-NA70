import { test, expect } from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs';

for (const name of ['1.pcap', '2.pcap']) {
  test(`complete 1x playback: ${name}`, async ({ page }) => {
    test.setTimeout(230000);
    await page.goto('/');
    const row = page.locator('.file-select').filter({ hasText: name }).first();
    await expect(row).toBeVisible();
    await row.click();
    await expect(page.locator('.canvas-meta')).toContainText('FRAME 00001');
    const maximum = Number(await page.getByLabel('回放时间轴').getAttribute('max'));
    const started = performance.now();
    await page.getByTitle('播放', { exact: true }).click();
    await expect(page.getByTitle('播放', { exact: true })).toBeVisible({ timeout: 210000 });
    const elapsed = (performance.now() - started) / 1000;
    expect(Number(await page.getByLabel('回放时间轴').inputValue())).toBe(maximum);
    await expect(page.locator('.canvas-meta')).toContainText('不完整帧');
    const fps = Number(await page.getByTestId('fps').textContent());
    expect(fps).toBeGreaterThanOrEqual(30);
    fs.writeFileSync(path.resolve('..', 'test-results', `full-playback-${name}.json`), JSON.stringify({ file: name, frames: maximum + 1, elapsed_seconds: elapsed, final_fps: fps }, null, 2));
    // End-of-file restart, changed playback rate and camera interaction.
    await page.getByLabel('播放速度').selectOption('2');
    await page.getByTitle('播放', { exact: true }).click();
    await page.waitForTimeout(1200);
    await page.getByTitle('暂停', { exact: true }).click();
    const restarted = Number(await page.getByLabel('回放时间轴').inputValue());
    expect(restarted).toBeGreaterThan(10); expect(restarted).toBeLessThan(45);
    const canvas = page.getByTestId('cloud-canvas');
    const rect = (await canvas.boundingBox())!;
    await page.mouse.move(rect.x + rect.width / 2, rect.y + rect.height / 2);
    await page.mouse.down();
    await page.mouse.move(rect.x + rect.width / 2 + 120, rect.y + rect.height / 2 + 80, { steps: 20 });
    await page.mouse.up();
    await page.mouse.wheel(0, -600);
    await page.waitForTimeout(1200);
    expect(Number(await page.getByTestId('fps').textContent())).toBeGreaterThanOrEqual(30);
  });
}

test('cancel an upload while bytes are still streaming', async ({ page, request }) => {
  await page.goto('/');
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 10, downloadThroughput: 10485760, uploadThroughput: 100000 });
  await page.getByTestId('file-input').setInputFiles({ name: 'cancel-stream-test.pcap', mimeType: 'application/octet-stream', buffer: Buffer.alloc(4 * 1024 * 1024) });
  await expect(page.getByText('正在上传文件')).toBeVisible();
  await page.waitForTimeout(700);
  await page.getByRole('button', { name: '取消导入', exact: true }).click();
  await expect(page.getByText('从一次扫描，看见三维空间')).toBeVisible();
  await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await page.waitForTimeout(1000);
  const list = await (await request.get('/api/captures')).json();
  expect(list.filter((c: { filename: string }) => c.filename === 'cancel-stream-test.pcap')).toHaveLength(0);
});

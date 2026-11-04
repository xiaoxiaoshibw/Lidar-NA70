import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

test('SYNTHETIC capture entry starts and restores a recording after refresh', async ({ page }) => {
  let recording = false;
  let starts = 0;
  let submitted: any;
  const html = fs.readFileSync(path.resolve('../../packages/na70_core/na70_core/capture/console.html'), 'utf8');
  await page.route('http://127.0.0.1:8791/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
    if (url.pathname === '/api/session/start') {
      starts += 1;
      submitted = route.request().postDataJSON();
      recording = true;
      return route.fulfill({ status: 202, json: { session_id: 'synthetic', state: 'starting' } });
    }
    if (url.pathname === '/api/status') return route.fulfill({ json: {
      state: recording ? 'recording' : 'idle', task: 'B1', session_id: 'synthetic',
      started_unix: Date.now() / 1000, duration_s: 60, remaining_s: 59,
      devices: recording ? { imu: { hz: 200, frames: 100, checksum_errors: 0 }, lidar: { pcap_bytes: 4096 } } : null,
      sessions: [{ session_id: 'unfinished-1', status: 'unfinalized', task: 'B1', duration_s: null }], last: null,
    } });
    if (url.pathname === '/api/files') return route.fulfill({ json: { files: [{ path: 'events.jsonl', bytes: 20 }] } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/capture.html');
  await expect(page.getByRole('navigation')).toContainText('标定采集');
  const consoleFrame = page.frameLocator('iframe[title="标定采集控制台"]');
  await expect(consoleFrame.getByText('B1 试录动作卡（尚待验收）')).toBeVisible();
  await expect(consoleFrame.getByRole('img', { name: /俯视示意/ })).toBeVisible();
  await expect(consoleFrame.locator('#task option[value="B2"]')).toBeDisabled();
  await consoleFrame.getByText('unfinished-1').click();
  await expect(consoleFrame.getByText('events.jsonl')).toBeVisible();
  await expect(consoleFrame.getByText('未完成（可能中断或仍在录制）')).toBeVisible();
  await consoleFrame.getByText('温度测量（选填）').click();
  await consoleFrame.locator('#temperature').fill('42.3');
  await consoleFrame.locator('#temperature-method').fill('外壳表面 ±1℃');
  await consoleFrame.getByRole('button', { name: '开始录制' }).click();
  await expect(consoleFrame.locator('#msg')).toContainText('秒后开始');
  await consoleFrame.getByRole('button', { name: '停止' }).click();
  await expect(consoleFrame.locator('#msg')).toContainText('已取消');
  await consoleFrame.getByRole('button', { name: '开始录制' }).click();
  await expect(consoleFrame.locator('#state')).toContainText('正在录制');
  expect(starts).toBe(1);
  expect(submitted.operator.temperature_c).toBe(42.3);
  expect(submitted.operator.temperature_method).toBe('外壳表面 ±1℃');
  await page.reload();
  await expect(consoleFrame.locator('#state')).toContainText('正在录制');
});

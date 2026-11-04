import { test, expect } from '@playwright/test';
import path from 'node:path';

test('annotation window binds displayed cloud and blocks stale frame saves (SYNTHETIC)', async ({ page }) => {
  const saved: Record<string, any>[] = [];
  let stale = false;
  const windowContext = { capture_sha256: 'a'.repeat(64), observation_unit: 'accumulated_window',
    frame_range: [0, 0], coordinate_frame: 'lidar_link', cloud_id: 'synthetic-window',
    device_time_range: [1.3, 1.49], duration_seconds: .19 };
  await page.route('**/api/demo/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/captures')) return route.fulfill({ json: [{ id: 'cap', filename: 'SYNTHETIC', frame_count: 60 }] });
    if (url.pathname.endsWith('/timeline')) return route.fulfill({ json: { capture_id: 'cap', frame_count: 60, duration: 6.0,
      frames: Array.from({ length: 60 }, (_, i) => [i, i * 0.1, i * 0.1 + 0.09, 100, 20000, true]) } });
    if (url.pathname.endsWith('/annotations')) {
      if (route.request().method() === 'POST') saved.push(route.request().postDataJSON());
      return route.fulfill({ json: route.request().method() === 'POST' ? { saved: true } : [] });
    }
    if (url.pathname.endsWith('/sessions')) return route.fulfill({ json: { session_id: 'test' } });
    if (url.pathname.endsWith('/cloud')) {
      const body = route.request().postDataJSON();
      // stale=true 时返回旧窗口（end 差一帧）：前端对齐门禁拒绝，画布保持旧云。
      const end = stale ? Math.max(0, body.end - 1) : body.end;
      let header = Buffer.from(JSON.stringify({ version: 1, dtype: 'float32-le',
        fields: ['x', 'y', 'z', 'intensity'], start: body.start, end, filtering: 'none',
        point_count: 3, device_start_time: 1.3, device_time: 1.49,
        window_context: { ...windowContext, frame_range: [body.start, end] } }));
      header = Buffer.concat([header, Buffer.alloc((4 - header.length % 4) % 4, 32)]);
      const length = Buffer.alloc(4); length.writeUInt32LE(header.length);
      const points = new Float32Array([90,-30,0,100, 95,-25,1,100, 100,-20,2,100]);
      return route.fulfill({ contentType: 'application/octet-stream', body: Buffer.concat([length, header, Buffer.from(points.buffer)]) });
    }
    return route.fulfill({ json: { status: 'running', frame_index: 0, accumulated_frames: 5,
      accumulated_points: 3, device_time: 1.49, fps: 10, paused: true } });
  });
  await page.goto('/annotate/');
  await page.getByLabel('叠加帧数').selectOption('200');
  await page.getByRole('button', { name: '播放' }).click();
  const frameText = async () => Number((await page.getByText(/帧 \d+\/\d+/).first().innerText()).match(/帧 (\d+)\//)![1]);
  await expect.poll(frameText, { timeout: 5000 }).toBeGreaterThanOrEqual(5);
  await page.getByRole('button', { name: '暂停' }).click();
  await page.waitForTimeout(600);  // 等暂停帧的窗口云对齐落地
  const frame = await frameText();
  const canvas = page.locator('canvas').first();
  await expect(canvas).toBeVisible();
  const box = (await canvas.boundingBox())!;
  const draw = async () => {
    // (600,236) 起笔：左侧控制栏覆盖画布左缘，此处 elementFromPoint 命中画布。
    await page.mouse.move(box.x + 600, box.y + 236);
    await page.mouse.down();
    await page.mouse.move(box.x + 660, box.y + 266, { steps: 6 });
    await page.mouse.up();
  };
  await draw();
  await page.keyboard.press('g');  // 直接入列
  await expect(page.getByText(/待保存组（1）/)).toBeVisible();
  await page.keyboard.press('Control+s');
  await expect.poll(() => saved.length).toBe(1);
  expect(saved[0].frame_range).toEqual([0, frame]);
  expect(saved[0].cloud_id).toBe('synthetic-window');
  expect(saved[0].review_status).toBe('PENDING_REVIEW');
  // 时间轴回退 seek：新窗口云尚未对齐（返回旧 end），保存被阻断。
  stale = true;
  const timeline = page.locator('canvas').nth(1);
  const tbox = (await timeline.boundingBox())!;
  await page.mouse.click(tbox.x + 52, tbox.y + tbox.height / 2);  // ≈ 第 2 帧
  await expect.poll(frameText).toBeLessThan(frame);
  await page.waitForTimeout(400);
  await draw();
  await page.keyboard.press('g');
  await page.keyboard.press('Control+s');
  await expect(page.getByText('请等待当前窗口点云加载完成后再保存')).toBeVisible();
  expect(saved.length).toBe(1);
});

test('goal demo exposes staged cloud, inspector, logs and debug controls', async ({ page }) => {
  const pageErrors: string[] = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.goto('/demo/');
  await expect(page.getByTestId('pipeline-inspector')).toBeVisible();
  const start = page.getByRole('button', { name: '开始演示', exact: true });
  await expect(start).toBeEnabled();
  await start.click();
  await expect(page.getByRole('button', { name: /RANGE \/ ROI FILTER/ })).toBeVisible({ timeout: 20000 });
  await expect(page.getByRole('button', { name: /VOXEL DOWNSAMPLE/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /BEV PROPOSAL/ })).toBeVisible();
  await page.getByRole('button', { name: /Z REFINE/ }).click();
  await page.getByLabel('debug').check();
  await expect(page.getByText('算法可见')).toBeVisible();
  await expect(page.getByText('已拒绝（仍显示）')).toBeVisible();
  await page.getByText('模块日志').click();
  await expect(page.locator('.debug-logs')).toContainText('pipeline');
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.resolve('..', 'test-results', 'goal-observability.png'), fullPage: true });
  await page.getByText('实时参数（应用后从当前帧重置）').click();
  await page.getByLabel('FOV 半宽 m').fill('7');
  await page.getByRole('button', { name: '应用参数' }).click();
  await page.waitForTimeout(500);
  expect(pageErrors).toEqual([]);
});

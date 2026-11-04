import { test, expect, type Page } from '@playwright/test';

// 球门 Demo 会话身份与帧身份门禁（SYNTHETIC：全部 /api/demo/** 由路由模拟）。
// 验证三件事：
// 1) 会话创建后与采集绑定，演示进行中采集选择器被禁用，无法中途换采集；
// 2) 人工标注保存写回「会话绑定的采集」+「绘制框时的点云帧」（来自点云元数据
//    index），即使快照帧已前进到别的帧也不混用；
// 3) 点云尚未加载时保存被阻断，不产生任何标注请求。

const SNAPSHOT = { status: 'running', frame_index: 12, device_time: 1.2,
  accumulated_frames: 5, window_size: 20, accumulated_points: 3,
  window_raw_points: 3, fps: 10, processing_ms: 1, imu: false,
  lidar_online: true, goal: null };

// 与后端 /sessions/{id}/cloud 相同的长度前缀 + JSON 头 + float32 载荷。
function cloudBody(index: number, pointCount = 3): Buffer {
  const header = Buffer.from(JSON.stringify({
    version: 1, fields: ['x', 'y', 'z', 'intensity'], dtype: 'float32-le',
    index, point_count: pointCount, mode: 'accumulated', accumulated_frames: 5,
  }));
  const padded = Buffer.concat([header, Buffer.alloc((4 - header.length % 4) % 4, 32)]);
  const length = Buffer.alloc(4);
  length.writeUInt32LE(padded.length);
  const points = new Float32Array([90, -30, 0, 100, 95, -25, 1, 100, 100, -20, 2, 100]);
  return Buffer.concat([length, padded,
    Buffer.from(points.buffer, 0, pointCount * 16)]);
}

type DemoRouteOptions = {
  saved: Record<string, unknown>[];
  createdWith: { capture_id?: string }[];
  cloud: 'ok' | 'fail';
};

async function mockDemoRoutes(page: Page, options: DemoRouteOptions) {
  await page.route('**/api/demo/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (path.endsWith('/captures')) {
      return route.fulfill({ json: [
        { id: 'cap-a', filename: 'caochangyou-a.pcap', frame_count: 100 },
        { id: 'cap-b', filename: 'b.pcap', frame_count: 100 },
      ] });
    }
    if (path === '/api/demo/sessions' && method === 'POST') {
      options.createdWith.push(route.request().postDataJSON() as { capture_id?: string });
      return route.fulfill({ json: { session_id: 'sess-1', status: 'starting' } });
    }
    if (path.endsWith('/sessions/sess-1') && method === 'DELETE') {
      return route.fulfill({ json: {} });
    }
    if (path.endsWith('/sessions/sess-1') && method === 'GET') {
      // 快照帧(12)故意与点云帧(7)不同：标注必须按点云帧保存。
      return route.fulfill({ json: SNAPSHOT });
    }
    if (path.endsWith('/cloud')) {
      if (options.cloud === 'fail') return route.fulfill({ status: 404, json: { detail: 'no cloud' } });
      return route.fulfill({ contentType: 'application/octet-stream', body: cloudBody(7) });
    }
    if (path.endsWith('/debug')) {
      return route.fulfill({ json: { frame_index: 12, single_candidates: [], accumulated_candidates: [] } });
    }
    if (path.endsWith('/pipeline')) {
      return route.fulfill({ json: { frame_index: 12, source: 'accumulated', stages: [] } });
    }
    if (path.endsWith('/logs')) {
      return route.fulfill({ json: { items: [] } });
    }
    if (path.endsWith('/annotations')) {
      if (method === 'POST') {
        options.saved.push(route.request().postDataJSON() as Record<string, unknown>);
        return route.fulfill({ json: { labels_total: options.saved.length } });
      }
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
}

test('demo session binds its capture and saves annotations against the displayed cloud frame (SYNTHETIC)', async ({ page }) => {
  const saved: Record<string, unknown>[] = [];
  const createdWith: { capture_id?: string }[] = [];
  await mockDemoRoutes(page, { saved, createdWith, cloud: 'ok' });

  await page.goto('/demo/');
  // 默认选中 cap-a（caochangyou 优先），先切到 cap-b 再开会话。
  const captureSelect = page.locator('select', { hasText: 'b.pcap' });
  await captureSelect.selectOption('cap-b');
  await page.getByRole('button', { name: '开始演示' }).click();
  await expect.poll(() => createdWith[0]?.capture_id).toBe('cap-b');

  // 会话进行中：采集选择器禁用，无法把会话换到另一个采集。
  await expect(captureSelect).toBeDisabled();

  // 等点云（index=7）落地，帧身份行应显示真实帧号并标出与快照(12)不对齐。
  const identity = page.getByTestId('frame-identity');
  await expect(identity).toContainText('点云 F7');
  await expect(identity).toContainText('快照 F12');
  await expect(identity).toContainText('数据来自不同帧');

  // 标注：快照帧 12 ≠ 点云帧 7，保存必须写回点云帧 7 与 cap-b。
  await page.getByLabel('标注').check();
  await page.evaluate(() => {
    (window as unknown as Record<string, unknown>).__demoAnnotate &&
      ((window as unknown as { __demoAnnotate: { create: (x: number, y: number, z: number) => void } })
        .__demoAnnotate).create(90, -30, 0);
  });
  await page.getByRole('button', { name: '保存标注' }).click();
  await expect.poll(() => saved.length).toBe(1);
  expect(saved[0].capture_id).toBe('cap-b');
  expect(saved[0].frame_range).toEqual([7, 7]);
  await expect(page.getByText(/已保存到点云帧 7（快照已前进到 12/)).toBeVisible();
});

test('annotation save is blocked until a cloud frame has been rendered (SYNTHETIC)', async ({ page }) => {
  const saved: Record<string, unknown>[] = [];
  const createdWith: { capture_id?: string }[] = [];
  await mockDemoRoutes(page, { saved, createdWith, cloud: 'fail' });

  await page.goto('/demo/');
  await page.getByRole('button', { name: '开始演示' }).click();
  await page.getByLabel('标注').check();
  await page.evaluate(() => {
    (window as unknown as Record<string, unknown>).__demoAnnotate &&
      ((window as unknown as { __demoAnnotate: { create: (x: number, y: number, z: number) => void } })
        .__demoAnnotate).create(90, -30, 0);
  });
  await page.getByRole('button', { name: '保存标注' }).click();
  await expect(page.getByText('请等待当前点云加载完成后再保存')).toBeVisible();
  expect(saved.length).toBe(0);
});

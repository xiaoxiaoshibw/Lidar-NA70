import { expect, test } from '@playwright/test';
import path from 'node:path';

const primary = '11111111-1111-4111-8111-111111111111';
const background = '22222222-2222-4222-8222-222222222222';
const runId = '33333333-3333-4333-8333-333333333333';
const primaryWindow = 'primary-f000009-0.5s';
const backgroundWindow = 'background-f000009-0.5s';

const summary = (role: string, windowId: string) => ({
  window_id: windowId, input_role: role, requested_duration_s: 0.5,
  actual_span_s: 0.49, time_quality: 'ok', ray_count: 400,
  valid_return_count: 3, zero_distance_count: 397,
  target_roi: { point_count: 1, source_frame_count: 1, source_packet_count: 1 },
  wire_return_status: 'NOT_EVALUATED',
});

test('W1 run keeps source identity, shows raw diagnostics and background window (SYNTHETIC)', async ({ page }) => {
  let posted: Record<string, unknown> | null = null;
  let detailCalls = 0;
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));

  await page.route('**/api/captures', route => route.fulfill({ json: [
    { id: primary, filename: 'SYNTHETIC-target.pcap', status: 'ready', frame_count: 10, sha256: 'a'.repeat(64) },
    { id: background, filename: 'SYNTHETIC-background.pcap', status: 'ready', frame_count: 10, sha256: 'b'.repeat(64) },
  ] }));
  await page.route('**/api/wire/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const runBase = {
      run_id: runId, capture_id: primary, background_capture_id: background,
      end_frame: 9, background_end_frame: 9, wire_return_status: 'NOT_EVALUATED',
      window_count: 2, error: null, created_at: 1_800_000_000, updated_at: 1_800_000_001,
    };
    if (path.endsWith('/capabilities')) return route.fulfill({ json: {
      schema_version: 'wire-web-v1', modes: [{ id: 'w1_raw', label: 'W1 原始回波诊断', available: true, evaluation_status: 'NOT_EVALUATED' }],
      future_modes: [{ id: 'w3_roi', available: false }, { id: 'w4_full_fov', available: false }, { id: 'w5_motion', available: false }],
      max_active_runs: 2, default_window_seconds: [0.5, 1, 2],
    } });
    if (path.endsWith('/runs') && route.request().method() === 'POST') {
      posted = route.request().postDataJSON();
      return route.fulfill({ status: 202, json: { ...runBase, status: 'queued', window_count: 0 } });
    }
    if (path.endsWith('/runs')) return route.fulfill({ json: posted ? [{ ...runBase, status: 'ready' }] : [] });
    if (path.endsWith(`/runs/${runId}`)) {
      detailCalls += 1;
      const ready = detailCalls > 1;
      return route.fulfill({ json: {
        ...runBase, status: ready ? 'ready' : 'running', window_count: ready ? 2 : 0,
        request: { mode: 'W1_RAW_DIAGNOSTIC', capture_sha256: 'a'.repeat(64), background_sha256: 'b'.repeat(64),
          config_sha256: 'c'.repeat(64), render: false },
        windows: ready ? [summary('primary', primaryWindow), summary('background', backgroundWindow)] : [],
        evidence_gaps: ready ? ['target_attribution_requires_review'] : [],
      } });
    }
    const cloudMatch = path.match(/\/windows\/([^/]+)\/cloud$/);
    if (cloudMatch) return route.fulfill({ json: {
      run_id: runId, window_id: decodeURIComponent(cloudMatch[1]),
      input_role: decodeURIComponent(cloudMatch[1]).startsWith('background') ? 'background' : 'primary',
      capture_id: decodeURIComponent(cloudMatch[1]).startsWith('background') ? background : primary,
      total_valid_points: 10,
      sampled: true, points: [{ x: 1, y: 2, z: 3, intensity: 10, frame_index: 9,
        packet_index: 20, point_index: 2, device_time: 1.25 }],
    } });
    const windowMatch = path.match(/\/windows\/([^/]+)$/);
    if (windowMatch) {
      const windowId = decodeURIComponent(windowMatch[1]);
      return route.fulfill({ json: {
        run_id: runId,
        window: { ...summary(windowId.startsWith('background') ? 'background' : 'primary', windowId),
          packet_count: 2, frame_count: 1, first_frame: 9, end_frame: 9,
          first_packet_index: 20, last_packet_index: 21,
          actual_start_device_time: 1.0, actual_end_device_time: 1.49,
          partial_window: false, nonfinite_count: 0, valid_outside_rois_count: 2,
          direction_known_count: 20, direction_unknown_count: 380,
          all_valid: { point_count: 3, source_frame_count: 1, source_packet_count: 2,
            sensor_distance_m: { min: 1, p05: 1, p50: 2, p95: 3, max: 3 } },
          background_rois: [], reference_corridor: null, confirmed_target_return_count: null,
          time_resolution: 'packet_device_time_repeated_per_ray', segment_start_reason: null,
          equal_adjacent_device_times: 0 },
        packets: [20, 21].map((index, local_index) => ({
          window_id: windowId, index, frame_index: 9, local_index,
          device_time: 1.0 + local_index * .49, capture_time: 1.0,
          segment: 0, boundary_reason: local_index === 0 ? 'capture_start' : null,
          ray_count: 200, valid_mask: local_index + 1, target_roi_mask: 1,
          direction_known_mask: 10,
          background_roi_mask: 0, reference_corridor_mask: 0,
        })),
      } });
    }
    return route.fulfill({ status: 404, json: { detail: 'unknown mock route' } });
  });

  await page.goto(process.env.NA70_E2E_BASE_URL?.includes('5173') ? '/wire.html' : '/wire/');
  await expect(page.getByRole('heading', { name: '架空导线感知工作台' })).toBeVisible();
  await expect(page.getByText('NOT_EVALUATED').first()).toBeVisible();
  await page.getByLabel('主录制终点帧').fill('9');
  await page.getByLabel('同机位背景录制').selectOption(background);
  await page.getByLabel('背景终点帧').fill('9');
  await page.getByRole('button', { name: '运行 W1 原始诊断' }).click();
  await expect.poll(() => detailCalls, { timeout: 10000 }).toBeGreaterThan(1);
  await expect(page.getByText('已生成 2 个 W1 窗口。')).toBeVisible();
  expect(posted).toEqual({ capture_id: primary, background_capture_id: background, end_frame: 9,
    background_end_frame: 9, render: false });
  await expect(page.getByText('目标 ROI 内有效点')).toBeVisible();
  await expect(page.getByText('方向未知')).toBeVisible();
  await expect(page.getByText('方向可恢复').first()).toBeVisible();
  await expect(page.getByText('逐包时间线')).toBeVisible();
  await expect(page.getByText('展示 1 / 10 个有效点')).toBeVisible();
  await page.getByRole('button', { name: /背景 · 0.5 s/ }).click();
  await expect(page.getByText(backgroundWindow).first()).toBeVisible();
  await expect(page.getByText('接口未启用')).toHaveCount(3);
  await page.screenshot({ path: path.resolve('..', 'test-results', 'wire-workbench.png'), fullPage: true });
  expect(errors).toEqual([]);
});

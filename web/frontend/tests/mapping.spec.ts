import { test, expect } from '@playwright/test';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

// Browser checks for the mapping workbench. The first case is cheap and always
// runs; the second drives the full backend odometry on a real capture and is
// opt-in, because a real 234-frame run is minutes of CPU and must never be
// mistaken for a routine unit test.

const evidence = path.resolve('..', 'test-results');
const capture = path.resolve('../..', 'radar_capture_data', '4.pcap');
const heavy = process.env.NA70_MAPPING_E2E === '1';

test('SYNTHETIC complete job exposes aligned cloud and export', async ({ page }) => {
  execFileSync(path.resolve('../.venv/Scripts/python.exe'), ['-c',
    "import sys; from pathlib import Path; sys.path.insert(0, 'tests'); from test_mapping_api import scene; from test_decoder import make_pcap; Path('test-results').mkdir(exist_ok=True); make_pcap(Path('test-results/synthetic-mapping.pcap'), scene(4))"],
    { cwd: path.resolve('..') });
  await page.goto('/');
  await page.getByTestId('file-input').setInputFiles(path.resolve('../test-results/synthetic-mapping.pcap'));
  await expect(page.getByRole('button', { name: '开始点云配准' })).toBeEnabled();
  await expect(page.getByLabel('采集模式')).toHaveValue('fixed');
  await page.getByLabel('采集模式').selectOption('manual');
  await page.getByLabel('场景静态', { exact: true }).check();
  await page.getByLabel('路线说明', { exact: true }).fill('SYNTHETIC preparation record');
  await expect(page.locator('.collection-workbench [role=status]')).toHaveText('缺少真实移动');
  await expect(page.locator('.collection-workbench')).toContainText('不能进行真实移动验收');
  await expect(page.getByRole('button', { name: '开始点云配准' })).toBeEnabled();
  const createdResponse = page.waitForResponse(r => r.url().endsWith('/api/mapping/jobs') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '开始点云配准' }).click();
  const created = await (await createdResponse).json();
  expect(created.capture_preflight.metadata.route).toBe('SYNTHETIC preparation record');
  expect(created.capture_preflight.status).toBe('缺少真实移动');
  await expect(page.locator('.mapping-card')).toContainText('已完成', { timeout: 180000 });
  const aligned = page.waitForResponse(response => response.url().includes('/cloud') && response.status() === 200);
  await page.getByRole('button', { name: 'ODOMETRY', exact: true }).click();
  await aligned;
  await page.getByRole('button', { name: 'MAP', exact: true }).click();
  await expect(page.locator('.settings-section', { hasText: '地图结果' })).toContainText('体素分辨率');
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出证据包' }).click();
  await (await download).saveAs(path.join(evidence, 'synthetic-mapping-evidence.zip'));
  await page.screenshot({ path: path.join(evidence, 'mapping-synthetic-complete.png') });
});

test('mapping workbench surface is present and honest about its stage', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('/');

  await expect(page.locator('.navigation')).toContainText('静态环境建图');
  await expect(page.locator('.navigation')).toContainText('纯点云里程计');
  for (const label of ['RAW', 'ODOMETRY', 'MAP']) {
    await expect(page.getByRole('button', { name: label, exact: true })).toBeVisible();
  }
  // Without a finished job only the raw view is available.
  await expect(page.getByRole('button', { name: 'ODOMETRY', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'MAP', exact: true })).toBeDisabled();

  // The five coordinate frames must be named explicitly.
  const frames = page.locator('.coordinate-frames');
  for (const name of ['原始坐标系', '首帧坐标系', '局部子地图坐标系', '全局地图坐标系']) {
    await expect(frames).toContainText(name);
  }

  // And the page must not claim to be SLAM.
  await expect(page.locator('.mapping-card')).toContainText('不是完整 SLAM');
  await expect(page.locator('.mapping-card')).toContainText('无 IMU');
  await expect(page.getByLabel('静止初始化帧数')).toBeVisible();
  await page.getByTitle('操作帮助').click();
  await expect(page.locator('.help')).toContainText('失败帧');
  await expect(page.locator('.help')).toContainText('不是完整 SLAM');

  await page.screenshot({ path: path.join(evidence, 'mapping-workbench.png') });
  expect(errors).toEqual([]);
});

test('real capture: odometry job, failure timeline, trajectory and map chunks', async ({ page }) => {
  test.skip(!heavy, 'set NA70_MAPPING_E2E=1 to run the real-capture odometry job (minutes of CPU)');
  test.setTimeout(1_200_000);
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('/');

  await page.getByTestId('file-input').setInputFiles(capture);
  await expect(page.locator('.canvas-meta')).toContainText('帧叠加', { timeout: 90000 });
  await expect(page.getByRole('button', { name: '开始点云配准' })).toBeEnabled();

  await page.getByRole('button', { name: '开始点云配准' }).click();
  await expect(page.locator('.mapping-card')).toContainText('已完成', { timeout: 1_100_000 });

  // Rejected frames are never hidden: the strip and the trajectory list exist.
  await expect(page.locator('.failure-strip')).toBeVisible();

  await page.getByRole('button', { name: 'ODOMETRY', exact: true }).click();
  await expect(page.locator('.canvas-meta')).toContainText('后端位姿与轨迹');
  await expect(page.locator('.mapping-quality').first()).toContainText('重叠率');
  await page.screenshot({ path: path.join(evidence, 'mapping-odometry.png') });

  await page.getByRole('button', { name: 'MAP', exact: true }).click();
  await expect(page.locator('.canvas-meta')).toContainText('体素地图');
  await expect(page.locator('.statusbar')).toContainText('地图分块');
  await expect(page.locator('.settings-section', { hasText: '地图结果' })).toContainText('体素分辨率');
  await page.screenshot({ path: path.join(evidence, 'mapping-map.png') });

  expect(errors).toEqual([]);
});

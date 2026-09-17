import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// ---------------------------------------------------------------------------
// SUPERSEDED: these two cases drove the "开始本机分析" entry point, which the
// 2026-09-11 mainline change removed from the WebUI workflow. Moving-object
// detection is no longer the primary acceptance feature; the backend pipeline is
// untouched and still covered by web/tests/test_motion.py, test_bands.py and
// test_spatial.py.
//
// They are kept (skipped, assertions intact) rather than deleted so the browser
// coverage can be restored together with a re-exposed entry point, and so the
// reason stays visible instead of the cases quietly disappearing.
// ---------------------------------------------------------------------------

const capture4 = path.resolve('../..', 'radar_capture_data', '4.pcap');
const capture003 = path.resolve('../..', '2', '003.pcap');

async function openCapture(page, request, name: string, absolute: string) {
  await page.goto('/');
  const captures = await (await request.get('/api/captures')).json() as Array<{ filename: string }>;
  let row = page.locator('.file-select').filter({ hasText: name }).first();
  if (!captures.some(capture => capture.filename === name)) {
    await page.getByTestId('file-input').setInputFiles(absolute);
    row = page.locator('.file-select').filter({ hasText: name }).first();
  }
  await expect(row).toBeVisible({ timeout: 60000 });
  await row.click();
  await expect(page.locator('.canvas-meta')).toContainText('FRAME 00001', { timeout: 90000 });
}

test.describe.skip('moving-object analysis UI (entry removed from the current flow)', () => {
  test('4D band analysis runs and never reports a people count', async ({ page, request }) => {
    test.setTimeout(180000);
    await openCapture(page, request, '4.pcap', capture4);
    await page.getByRole('button', { name: /开始本机分析|重新分析/ }).click();
    await expect(page.locator('.analysis-card strong'))
      .toHaveText(/发现 \d+ 条移动点带|未发现移动点带/, { timeout: 90000 });
    await expect(page.getByLabel('叠加帧数')).toHaveValue('200');
    await page.getByLabel('回放时间轴').fill('150');
    await expect(page.locator('.motion-meta')).toContainText('前景', { timeout: 45000 });
    await expect(page.getByLabel('红色高亮移动点')).toBeChecked();
    await page.getByLabel('红色高亮移动点').uncheck();
    await page.getByLabel('红色高亮移动点').check();
    // The UI must state the limitation and must never claim a person count.
    await expect(page.locator('.band-note')).toContainText('不等于人数');
    await expect(page.locator('body')).not.toContainText(/\d+\s*个人/);
    await expect(page.getByRole('link', { name: '导出点带 CSV' })).toHaveAttribute('href', /analysis\/bands\.csv$/);
    await expect(page.getByRole('link', { name: '导出逐帧 CSV' })).toHaveAttribute('href', /analysis\.csv$/);
    await page.screenshot({ path: path.resolve('..', 'test-results', 'motion-analysis.png') });
  });

  test('selecting a moving band jumps to it and highlights its trail', async ({ page, request }) => {
    test.skip(!fs.existsSync(capture003), '2/003.pcap is not present on this machine');
    test.setTimeout(240000);
    await openCapture(page, request, '003.pcap', capture003);
    await page.getByRole('button', { name: /开始本机分析|重新分析/ }).click();
    await expect(page.locator('.analysis-card strong')).toHaveText(/发现 \d+ 条移动点带/, { timeout: 180000 });
    const rows = page.locator('.band-row');
    await expect(rows.first()).toBeVisible({ timeout: 45000 });
    expect(await rows.count()).toBeGreaterThan(0);
    await rows.first().click();
    await expect(rows.first()).toHaveClass(/selected/);
    await expect(page.locator('.motion-meta').last()).toContainText(/B\d{3}/, { timeout: 45000 });
    await expect(page.locator('body')).not.toContainText(/\d+\s*个人/);
    await page.screenshot({ path: path.resolve('..', 'test-results', 'motion-bands.png') });
  });
});

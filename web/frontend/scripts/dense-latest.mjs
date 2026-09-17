import { chromium, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
const browser = await chromium.launch({ args: ['--use-angle=d3d11'] });
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('http://127.0.0.1:8765');
  await page.locator('.file-select').filter({ hasText: '4.pcap' }).first().click();
  await expect(page.locator('.canvas-meta')).toContainText('200 帧叠加', { timeout: 60000 });
  await expect(page.locator('.loading-frame')).toHaveCount(0);
  await page.waitForTimeout(1200);
  const defaultCount = await page.getByTestId('point-count').textContent();
  await page.screenshot({ path: path.resolve('../test-results/dense-file4-default.png') });
  await page.getByRole('button', { name: '加载整包全部点', exact: true }).click();
  await expect(page.locator('.canvas-meta')).toContainText('234 帧叠加', { timeout: 60000 });
  await expect(page.locator('.loading-frame')).toHaveCount(0);
  await page.getByTitle('聚焦主体（F）', { exact: true }).click();
  await page.waitForTimeout(1200);
  const fullCount = await page.getByTestId('point-count').textContent();
  await page.screenshot({ path: path.resolve('../test-results/dense-file4-full.png') });
  await page.locator('.viewport').screenshot({ path: path.resolve('../test-results/dense-file4-cloud.png') });
  expect(errors).toEqual([]);
  fs.writeFileSync(path.resolve('../test-results/dense-file4-metrics.json'), JSON.stringify({ defaultCount, fullCount, errors }, null, 2));
  console.log(defaultCount, '\n', fullCount);
} finally { await browser.close(); }

// Documentation screenshot of an actual capture; requires the local server.
import { chromium } from '@playwright/test';
import path from 'node:path';
const browser = await chromium.launch({ args: ['--use-angle=d3d11'] });
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.goto('http://127.0.0.1:8765');
  await page.locator('.file-select').filter({ hasText: '2.pcap' }).first().click();
  await page.locator('.canvas-meta').waitFor();
  await page.getByLabel('回放时间轴').fill('80');
  await page.getByLabel('叠加帧数').selectOption('20');
  await page.getByLabel('最大距离', { exact: true }).fill('100');
  await page.getByLabel('点大小', { exact: true }).fill('2');
  await page.waitForTimeout(700);
  await page.getByTitle('复位视角').click();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: path.resolve('..', 'test-results', 'webui-nearfield.png') });
} finally { await browser.close(); }

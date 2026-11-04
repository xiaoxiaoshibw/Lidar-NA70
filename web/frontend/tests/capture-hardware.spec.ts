import { expect, test } from '@playwright/test';

test('Jetson dual-stream engineering recording through the browser', async ({ page }) => {
  test.skip(process.env.NA70_CAPTURE_HARDWARE !== '1', 'Requires a dedicated Jetson and SSH tunnel');
  test.setTimeout(120_000);
  await page.goto('/capture.html');
  const frame = page.frameLocator('iframe[title="标定采集控制台"]');
  await expect(frame.locator('#state')).toContainText('空闲');
  await frame.locator('#duration').fill('60');
  await frame.locator('#note').fill('A2 browser engineering diagnostic; not B1 calibration data');
  await frame.getByRole('button', { name: '开始录制' }).click();
  await expect(frame.locator('#state')).toContainText('正在录制', { timeout: 15_000 });
  await page.reload();
  await expect(frame.locator('#state')).toContainText('正在录制');
  await expect(frame.locator('#state')).toContainText('空闲', { timeout: 90_000 });
  await expect(frame.locator('#last')).toContainText('结果：');
  console.log('HARDWARE_RESULT', await frame.locator('#last').innerText());
});
